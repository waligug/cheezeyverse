"""Per-game lines for the characters, so the site can answer "how do I do against him?".

WHERE THIS COMES FROM. FBPB3 does not save box scores - there is no setting for it, and the
Stabbyverse reference site has the same dead links ours does. But `Tools -> Output MDB` writes a
`PlayerGameStats` table with one row per player per game, minutes included, for the whole season
already played. No setting to turn on, nothing that only counts from today forward.

THE TRAP THIS MODULE EXISTS TO AVOID. A character does not get a new player id when he is
created: he takes over a dormant reserve slot, and that slot has been playing games all season
under the filler's name. Its whole history is in PlayerGameStats under the same id. Dodger
Manson's id has thirty regular-season rows and he has six weeks of snapshots - so most of those
games are not his, and a naive head-to-head would tell him about an evening he did not play.

HIS ARRIVAL DAY is the answer, and it is RECORDED, not reconstructed. simweek reads FBPB3's own
day counter out of league.dat at the moment it stamps him into the slot, and it lives on his
level history as `from_day`. The run log is kept as a fallback for characters placed before that
was recorded, and `since_source` on every entry says which of the two produced the number -
because a wrong arrival day reads exactly like a right one, and the only way anybody catches one
is the two answers disagreeing. Neither available means no games are published for him at all:
the filler's evenings are not his, and inventing meetings is worse than showing none.

THE JOINS, confirmed against a real MDB:
    PlayerGameStats.Seasonday = Schedule.Day
    PlayerGameStats.Team      = Schedule.Home or Schedule.Away (a NAME)
    PlayerGameStats.Opponent  = Team.ID       (a NUMBER - hence the Team table)
    Schedule.Type             0 preseason, 1 regular season, 2 playoffs
    Day 1 = the season opener, so a date is day - 1 added to it
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

# The columns worth publishing, named as the site wants them rather than as Access does.
STAT_COLUMNS = {
    "Minutes": "min", "Points": "pts", "Rebounds": "reb", "OffensiveRebounds": "oreb",
    "Assists": "ast", "Steals": "stl", "Blocks": "blk", "Turnovers": "to", "Fouls": "pf",
    "FGM": "fgm", "FGA": "fga", "FTM": "ftm", "FTA": "fta", "3PM": "tpm", "3PA": "tpa",
    "PlusMinus": "pm",
}
COUNTING = ["pts", "reb", "ast", "stl", "blk", "to", "min"]
REGULAR_SEASON, PLAYOFFS = 1, 2


def _int(value, default=0):
    try:
        return int(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


def run_started(run):
    """When a run BEGAN, as an ISO string, or "" if it cannot be worked out.

    `at` is stamped when a run FINISHES. A character created while a run was in flight is not in
    that run - activation happens at its start - so comparing his birth to the finish time
    credits him with days that were simmed before he existed. Liam was created six minutes into
    a run that took eleven; the finish-time rule put his debut a week early.
    """
    started = str(run.get("started_at") or "").strip()
    if started:
        return started
    at, seconds = str(run.get("at") or "").strip(), run.get("seconds")
    if not at or seconds is None:
        # "" means unknown, as the docstring says. Returning `at` here handed back the FINISH
        # time wearing a start time's name, which is the rule this function exists to replace.
        return ""
    try:
        stamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return at
    return (stamp - timedelta(seconds=float(seconds))).isoformat()


def stored_debut(character, league=None, season=None):
    """The arrival day written down when he was placed, or None if it was not.

    simweek reads FBPB3's own day counter out of league.dat at the moment it stamps a character
    into a reserve slot, and record_level keeps it on that level's history entry as `from_day`.
    That is a fact recorded once by the code that did the placing, which is worth far more than
    re-deriving it from a log on every publish: the log can be truncated, rewound by
    restore_backup, or simply not contain a run that a tool made.

    A level he entered in an EARLIER season he has played from day 1 of this one. A promotion or
    a draft lands him at the rollover, so day 1 as well, even on an entry written before
    `from_day` existed.
    """
    best = None
    for entry in character.get("level_history") or []:
        if league is not None and entry.get("level") != league:
            continue
        from_season = entry.get("from_season")
        if season is not None and from_season is not None:
            try:
                if int(from_season) > int(season):
                    continue          # a level he has not reached yet in the season being asked about
                if int(from_season) < int(season):
                    best = 1           # here before this season started: all of it is his
                    continue
            except (TypeError, ValueError):
                pass
        day = entry.get("from_day")
        if day is not None:
            best = max(1, _int(day, 1))
        elif entry.get("how_it_started") in ("promoted", "drafted"):
            best = 1
    return best


def first_game_day(character, runs, season=None, league=None):
    """The fallback: 1 + every day simmed in this league, this season, before he existed.

    Only reached when `stored_debut` has nothing, which means a character placed before the day
    was recorded, or one whose history is missing. Five things it has to get right, four of them
    learned by getting them wrong:

      * THE START, not the finish. See run_started.
      * ONLY RUNS THAT SIMMED. A failed run and a dry run both advance nothing, and there has
        been at least one of each - one killed by a locked desktop.
      * ONLY THIS LEAGUE. The panel's checkboxes let a run advance pro alone; counting its days
        against a prep character moves his debut past games that are his.
      * ONLY THIS SEASON. Seasonday restarts every year while the log accumulates. A run with no
        season recorded could belong to any of them, so it makes the answer unknowable rather
        than being quietly counted - which is what the season filter used to do.
      * THE WHOLE LOG. The caller must not hand over a truncated `runs()`; the default limit is
        twenty and quietly made everybody a day-one player.

    Returns None when it cannot tell, and the caller says so rather than guessing. It used to
    return 1, on the reasoning that counting everything is the safe direction - it is not. Every
    game before a character existed belongs to the filler whose slot he took, and publishing
    those invents head-to-head meetings that never happened, which is precisely the failure this
    module exists to prevent.
    """
    born = str(character.get("created_at") or "")
    if not born or not runs:
        return None
    simmed = 0
    for run in runs or []:
        if run.get("ok") is False or run.get("dry_run"):
            continue
        if league is not None:
            in_run = run.get("leagues")
            if in_run is not None and league not in in_run:
                continue
        if season is not None:
            if run.get("season") is None:
                return None            # cannot place this run in a season: refuse to guess
            try:
                if int(run["season"]) != int(season):
                    continue
            except (TypeError, ValueError):
                return None
        # A row with no duration can only be placed by its finish time. That is the old rule,
        # and it is wrong for somebody created while that run was in flight - but refusing the
        # whole derivation would hide every game of every character whose log was written before
        # durations were recorded, which is worse and much harder to notice.
        began = run_started(run) or str(run.get("at") or "")
        if not began:
            return None                # no start and no finish: the run cannot be placed at all
        if began >= born:
            continue
        simmed += _int(run.get("days"))
    return simmed + 1


def debut(character, league=None, runs=None, season=None):
    """(day, source) - the day this character's own games start, and where that came from.

    The source travels with the data all the way to games.json on purpose. A wrong arrival day
    looks exactly as plausible as a right one on the page, and the only way anybody spots one is
    by seeing that the two independent answers disagree.
    """
    stored = stored_debut(character, league, season)
    if stored is not None:
        return stored, "stored"
    derived = first_game_day(character, runs, season=season, league=league)
    if derived is not None:
        return derived, "run log"
    return None, "unknown"


def build(characters, games, schedule, teams, runs=None, league=None, opener=None,
          season=None):
    """The payload the site reads: one entry per character, with his own game lines.

    `games`, `schedule` and `teams` are the three MDB tables as lists of dicts - whatever
    mdb_query.ps1's CSV parsed into. Everything here is pure, so it is testable off a handful of
    real rows rather than needing a database.
    """
    by_team_id = {_int(t.get("ID")): str(t.get("Name") or "").strip() for t in teams or []}
    # (day, home, away) -> the schedule row, so a game line can say who won and where
    fixtures = {}
    for row in schedule or []:
        fixtures[(_int(row.get("Day")), str(row.get("Home") or "").strip(),
                  str(row.get("Away") or "").strip())] = row

    def fixture_for(day, team, opponent):
        return (fixtures.get((day, team, opponent))    # our man at home
                or fixtures.get((day, opponent, team)))  # away

    wanted = {}
    for c in characters:
        pid = (c.get("league_player_ids") or {}).get(league)
        if pid is None:
            continue
        wanted[_int(pid)] = c

    out = []
    for pid, c in wanted.items():
        since, source = debut(c, league, runs, season)
        lines = []
        for row in games or []:
            if _int(row.get("ID")) != pid:
                continue
            day = _int(row.get("Seasonday"))
            # No arrival day means no way to tell his games from the filler's, so he gets none.
            # Publishing them anyway would invent meetings, which is worse than a blank row.
            if since is None or day < since:
                continue        # played by the filler whose slot this was
            team = str(row.get("Team") or "").strip()
            opponent = by_team_id.get(_int(row.get("Opponent")), "")
            fix = fixture_for(day, team, opponent)
            if fix is None:
                continue
            kind = _int(fix.get("Type"))
            if kind not in (REGULAR_SEASON, PLAYOFFS):
                continue        # preseason games count for nothing anywhere else either
            home = str(fix.get("Home") or "").strip() == team
            ours = _int(fix.get("HomeScore") if home else fix.get("AwayScore"))
            theirs = _int(fix.get("AwayScore") if home else fix.get("HomeScore"))
            # `team` is on every line, not just on the character: Tim changed teams mid-season
            # and Gravy was remade onto a different one, so "his team" is a property of the game
            # rather than of the man.
            line = {"day": day, "team": team, "opp": opponent, "home": home,
                    "won": ours > theirs, "score": [ours, theirs], "playoff": kind == PLAYOFFS}
            line.update({short: _int(row.get(col)) for col, short in STAT_COLUMNS.items()})
            line["start"] = str(row.get("Starter") or "").strip().lower() == "true"
            lines.append(line)
        lines.sort(key=lambda g: g["day"])
        out.append({
            "id": c.get("id"), "player": pid, "since_day": since, "since_source": source,
            "name": f'{c.get("first_name", "")} {c.get("last_name", "")}'.strip(),
            # where he is NOW: the last game he played, falling back to the store's own idea
            "team": (lines[-1]["team"] if lines else None) or str(c.get("team_abbrev") or ""),
            "games": lines,
        })
    out.sort(key=lambda p: p["name"])
    payload = {"league": league, "characters": out}
    if opener:
        payload["opener"] = str(opener)
    return payload


def day_to_date(day, opener):
    """Season day 1 is the opener, so day N is N-1 days after it."""
    if isinstance(opener, str):
        opener = date.fromisoformat(opener)
    return opener + timedelta(days=_int(day) - 1)


# ---- reading the MDB ------------------------------------------------------------------------
MDB_SQL = {
    "teams": "SELECT ID, Name, City FROM Team",
    "schedule": "SELECT Day, Home, Away, Type, HomeScore, AwayScore, BoxName FROM Schedule",
    # only our characters' rows: PlayerGameStats is ~5,400 rows a league and we want seven of
    # them, so the filter belongs in the query rather than in Python
    "games": "SELECT * FROM PlayerGameStats WHERE ID IN ({ids})",
}


def powershell():
    """The 32-BIT PowerShell, because Jet is a 32-bit provider and only it is registered.

    This is not a preference. Under the 64-bit shell - which is what plain "powershell" resolves
    to on SERVERPC - opening the connection throws "The 'Microsoft.Jet.OLEDB.4.0' provider is
    not registered on the local machine". An earlier version of this function ran plain
    "powershell" and its docstring asserted the script "already knows to run under the right
    bitness". It does not, it never did, and I had not checked: the claim was invented.

    The consequence was the worst kind. A .NET method exception is NON-TERMINATING in a -File
    script, so PowerShell printed nothing, exited 0, and query() parsed the empty output as an
    empty CSV. games.json would have published with zero games for everybody, no error anywhere
    and nothing in the log - a feature that looks built and silently holds nothing.
    """
    import os
    wow = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "SysWOW64",
                       "WindowsPowerShell", "v1.0", "powershell.exe")
    return wow if os.path.exists(wow) else "powershell"


def query(mdb_path, sql, script=None):
    """Run one SQL statement against an Access MDB and return a list of dicts.

    THE EXIT CODE IS WHAT SAYS WHETHER IT WORKED, and nothing else can. `mdb_query.ps1` sets
    $ErrorActionPreference = "Stop", so a missing provider, a bad path or a SQL error is a
    NON-ZERO exit; that is raised above. A zero exit is a query that ran.

    Empty stdout on a zero exit therefore means NO ROWS, and must return []. This used to raise,
    on the reasoning that a real result always has at least a header line - which is not true:
    `ConvertTo-Csv` of an empty DataTable emits nothing whatsoever, header included. That guard
    was written for the 64-bit-PowerShell failure, where the script silently produced nothing,
    and it was the right guard before the .ps1 learned to fail loudly. Once it did, the guard was
    catching the wrong thing.

    It cost a publish: after FBPB3's rollover PlayerGameStats is legitimately empty, which is the
    exact state the games archive exists to survive, and this raised instead of returning [] - so
    the merge that would have preserved 161 game lines never ran at all.
    """
    import csv
    import os
    import subprocess
    import tempfile

    # THE RESULT COMES BACK THROUGH A FILE, NOT THROUGH STDOUT, and that is about text rather
    # than size. Ten of prep's 425 players are spelled with an accent - Gerald Rudloff carries
    # an acute, and so do Aytac Donis and Bartolome Drexler. Windows PowerShell 5.1 writes a
    # redirected stdout in the console codepage whatever [Console]::OutputEncoding says, so
    # those bytes reached Python as undecodable cp1252, the reader thread raised, and the query
    # returned NOTHING AT ALL. Not a mangled name - an empty result set, from a process that
    # exited 0. It archived 390 player seasons with every name blank, and pro looked perfect
    # because its names happen to be ASCII.
    #
    # `Export-Csv -Encoding UTF8` writes the file with an explicit encoding and a BOM, which is
    # why the .ps1 has always had an -Out path and why this now uses it.
    script = script or (Path(__file__).resolve().parent / "export" / "mdb_query.ps1")
    handle, csv_path = tempfile.mkstemp(suffix=".csv", prefix="cv-mdb-")
    os.close(handle)
    out = subprocess.run(
        [powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-Path", str(mdb_path), "-Sql", sql, "-Out", csv_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        if out.returncode == 0:
            # utf-8-sig: Export-Csv writes a BOM, and a BOM left in place turns the first
            # column's name into something no lookup will ever match.
            text = Path(csv_path).read_text(encoding="utf-8-sig", errors="replace")
            return list(csv.DictReader(text.splitlines()))
    finally:
        try:
            os.unlink(csv_path)
        except OSError:
            pass
    # Only a non-zero exit reaches here: the success path returned above.
    raise RuntimeError(f"mdb_query failed: {(out.stderr or out.stdout)[:300]}")


def from_mdb(mdb_path, characters, runs=None, league=None, opener=None, season=None):
    """Everything `build` needs, read out of one LeagueOutput.mdb."""
    ids = [str(_int((c.get("league_player_ids") or {}).get(league)))
           for c in characters if (c.get("league_player_ids") or {}).get(league) is not None]
    if not ids:
        return {"league": league, "characters": []}
    return build(characters,
                 query(mdb_path, MDB_SQL["games"].format(ids=", ".join(ids))),
                 query(mdb_path, MDB_SQL["schedule"]),
                 query(mdb_path, MDB_SQL["teams"]),
                 runs=runs, league=league, opener=opener, season=season)
