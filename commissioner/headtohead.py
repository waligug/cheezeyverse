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

from datetime import date, timedelta

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


def first_game_day(character, runs):
    """The season day this character arrived on: 1 + every day simmed before he existed.

    Derived rather than stored, because nothing was recording it and the information is already
    there. `runs` is the store's own run log; only runs that actually simmed count, so a failed
    one - and there has been at least one, killed by a locked desktop - does not push somebody's
    debut a week later than it happened.

    Returns 1 when it cannot tell, which counts everything. That is the wrong answer in the safe
    direction: a character sees a few games that were not his, rather than losing games that
    were. The page says which day it is counting from so the discrepancy is visible rather than
    mysterious.
    """
    born = str(character.get("created_at") or "")
    if not born:
        return 1
    simmed = 0
    for run in runs or []:
        when = str(run.get("at") or "")
        if not when or when >= born:
            continue
        if run.get("ok") is False:
            continue
        simmed += _int(run.get("days"))
    return simmed + 1


def build(characters, games, schedule, teams, runs=None, league=None, opener=None):
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
        since = first_game_day(c, runs)
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
