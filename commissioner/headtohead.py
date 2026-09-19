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

`first_day` is the answer, and it is derived rather than stored: the store records every sim
run with the days it simmed, so the game day a character arrived on is one plus the days of
every run that finished before he was created. Exact, from data already written, and it needs
nobody to have remembered to record anything.

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
        return at
    try:
        stamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return at
    return (stamp - timedelta(seconds=float(seconds))).isoformat()


def first_game_day(character, runs, season=None):
    """The season day this character arrived on: 1 + every day simmed before he existed.

    Derived rather than stored, from the store's own run log. Four things it has to get right,
    three of them learned by getting them wrong:

      * THE START, not the finish. See run_started.
      * ONLY RUNS THAT SIMMED. A failed run and a dry run both advance nothing, and there has
        been at least one of each - one killed by a locked desktop.
      * ONLY THIS SEASON. Seasonday restarts at 1 every year while the run log goes on
        accumulating, so summing across a rollover gives a debut somewhere in the middle of next
        century. A character created before this season started was here for all of it, so his
        answer is 1 - which is also what happens naturally when no run this season predates him.
      * THE WHOLE LOG. The caller must not hand over a truncated `runs()`; the default limit is
        twenty and quietly makes everybody a day-one player.

    Returns 1 when it cannot tell, which counts everything. A character seeing a few games that
    were not his is the safe direction against losing games that were, and the page shows the
    day it is counting from so the discrepancy is visible rather than mysterious.
    """
    born = str(character.get("created_at") or "")
    if not born:
        return 1
    simmed = 0
    for run in runs or []:
        if run.get("ok") is False or run.get("dry_run"):
            continue
        if season is not None and run.get("season") is not None:
            try:
                if int(run["season"]) != int(season):
                    continue
            except (TypeError, ValueError):
                pass
        began = run_started(run)
        if not began or began >= born:
            continue
        simmed += _int(run.get("days"))
    return simmed + 1


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
        since = first_game_day(c, runs, season)
        lines = []
        for row in games or []:
            if _int(row.get("ID")) != pid:
                continue
            day = _int(row.get("Seasonday"))
            if day < since:
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
            "id": c.get("id"), "player": pid, "since_day": since,
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

    Raises on failure rather than returning nothing. The scripts set $ErrorActionPreference =
    "Stop" so a provider or SQL problem is a non-zero exit instead of silence, and an empty
    result here now means the table really is empty.
    """
    import csv
    import io
    import subprocess

    script = script or (Path(__file__).resolve().parent / "export" / "mdb_query.ps1")
    out = subprocess.run(
        [powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-Path", str(mdb_path), "-Sql", sql],
        capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"mdb_query failed: {(out.stderr or out.stdout)[:300]}")
    rows = list(csv.DictReader(io.StringIO(out.stdout)))
    if not rows and not out.stdout.strip():
        # not even a header line: the script produced nothing at all, which is what the 64-bit
        # shell did for months of nobody noticing
        raise RuntimeError(f"mdb_query returned no output at all for {sql[:60]!r} - "
                           f"check the Jet provider and the PowerShell bitness ({powershell()})")
    return rows


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
