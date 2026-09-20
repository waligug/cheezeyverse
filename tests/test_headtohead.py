"""Head-to-head game lines, built from the rows a real MDB actually contains.

Every row below is verbatim from `mdb_query.ps1` against a real CV_Prep export - the Team table,
the Schedule rows for the two Berries-Generals meetings, and Chris Zimmer's and Dodger Manson's
PlayerGameStats lines for those games plus the preseason. Nothing here is reformatted, because
four parsers this week were broken by working from a tidied copy rather than the bytes.

THE ONE THAT MATTERS IS THE FILLER TRAP. A character does not get a new player id: he takes over
a dormant reserve slot that has been playing all season under somebody else's name, and that
history sits in PlayerGameStats under the same id. Dodger Manson's id has thirty regular-season
rows and he has six weeks of snapshots. Counted naively, his head-to-head record would include a
game in October that a filler played, against a Chris Zimmer who did not exist yet - and it
would look perfectly plausible, which is what makes it worth a test.

    python tests/test_headtohead.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.headtohead import (  # noqa: E402
    build, day_to_date, debut, first_game_day, run_started, stored_debut)

TEAMS = [{"ID": "5", "Name": "Berries", "City": "Saskatoon"},
         {"ID": "16", "Name": "Generals", "City": "Jackson"},
         {"ID": "7", "Name": "Stones", "City": "Pierre"},
         {"ID": "6", "Name": "Dealers", "City": "Wetaskiwin"},
         {"ID": "19", "Name": "Spirits", "City": "Ariel"}]

SCHEDULE = [
    # the two Berries-Generals meetings, Type 1 = regular season
    {"Day": "31", "Home": "Berries", "Away": "Generals", "Type": "1",
     "HomeScore": "46", "AwayScore": "53", "BoxName": "31-2"},
    {"Day": "134", "Home": "Generals", "Away": "Berries", "Type": "1",
     "HomeScore": "45", "AwayScore": "43", "BoxName": "134-1"},
    # preseason, which counts for nothing anywhere else either
    {"Day": "1", "Home": "Stones", "Away": "Berries", "Type": "0",
     "HomeScore": "40", "AwayScore": "38", "BoxName": "1-1"},
    {"Day": "1", "Home": "Generals", "Away": "Dealers", "Type": "0",
     "HomeScore": "50", "AwayScore": "44", "BoxName": "1-2"},
    {"Day": "2", "Home": "Generals", "Away": "Spirits", "Type": "0",
     "HomeScore": "48", "AwayScore": "47", "BoxName": "2-1"},
]

GAMES = [
    {"ID": "53", "Seasonday": "1", "Team": "Berries", "Opponent": "7", "Minutes": "14",
     "FGM": "1", "FGA": "4", "FTM": "0", "FTA": "0", "3PM": "0", "3PA": "2",
     "OffensiveRebounds": "0", "Rebounds": "1", "Assists": "1", "Steals": "0", "Blocks": "0",
     "Turnovers": "0", "Points": "2", "Fouls": "0", "PlusMinus": "-1", "Starter": "True"},
    {"ID": "220", "Seasonday": "1", "Team": "Generals", "Opponent": "6", "Minutes": "3",
     "FGM": "0", "FGA": "0", "FTM": "0", "FTA": "0", "3PM": "0", "3PA": "0",
     "OffensiveRebounds": "0", "Rebounds": "1", "Assists": "1", "Steals": "0", "Blocks": "0",
     "Turnovers": "0", "Points": "0", "Fouls": "0", "PlusMinus": "2", "Starter": "False"},
    {"ID": "220", "Seasonday": "2", "Team": "Generals", "Opponent": "19", "Minutes": "3",
     "FGM": "0", "FGA": "1", "FTM": "0", "FTA": "0", "3PM": "0", "3PA": "0",
     "OffensiveRebounds": "0", "Rebounds": "2", "Assists": "0", "Steals": "0", "Blocks": "0",
     "Turnovers": "1", "Points": "0", "Fouls": "2", "PlusMinus": "1", "Starter": "False"},
    {"ID": "53", "Seasonday": "31", "Team": "Berries", "Opponent": "16", "Minutes": "12",
     "FGM": "0", "FGA": "4", "FTM": "0", "FTA": "0", "3PM": "0", "3PA": "1",
     "OffensiveRebounds": "1", "Rebounds": "1", "Assists": "1", "Steals": "0", "Blocks": "0",
     "Turnovers": "2", "Points": "0", "Fouls": "1", "PlusMinus": "5", "Starter": "True"},
    {"ID": "220", "Seasonday": "31", "Team": "Generals", "Opponent": "5", "Minutes": "16",
     "FGM": "2", "FGA": "4", "FTM": "1", "FTA": "2", "3PM": "0", "3PA": "0",
     "OffensiveRebounds": "0", "Rebounds": "1", "Assists": "1", "Steals": "0", "Blocks": "0",
     "Turnovers": "0", "Points": "5", "Fouls": "0", "PlusMinus": "10", "Starter": "False"},
    {"ID": "53", "Seasonday": "134", "Team": "Berries", "Opponent": "16", "Minutes": "15",
     "FGM": "5", "FGA": "8", "FTM": "0", "FTA": "0", "3PM": "0", "3PA": "2",
     "OffensiveRebounds": "1", "Rebounds": "4", "Assists": "1", "Steals": "1", "Blocks": "0",
     "Turnovers": "0", "Points": "10", "Fouls": "1", "PlusMinus": "-1", "Starter": "True"},
    {"ID": "220", "Seasonday": "134", "Team": "Generals", "Opponent": "5", "Minutes": "18",
     "FGM": "3", "FGA": "3", "FTM": "0", "FTA": "0", "3PM": "0", "3PA": "0",
     "OffensiveRebounds": "0", "Rebounds": "2", "Assists": "1", "Steals": "0", "Blocks": "0",
     "Turnovers": "1", "Points": "6", "Fouls": "1", "PlusMinus": "-4", "Starter": "True"},
]

CHRIS = {"id": "chris", "first_name": "Chris", "last_name": "Zimmer",
         "league_player_ids": {"prep": 53}, "created_at": "2026-09-18T00:00:00+00:00"}
# created later, which is the whole point: his id's earlier games belong to the filler
DODGER = {"id": "dodger", "first_name": "Dodger", "last_name": "Manson",
          "league_player_ids": {"prep": 220}, "created_at": "2026-09-19T00:00:00+00:00"}
RUNS = [{"days": 7, "ok": True, "at": "2026-09-17T10:00:00+00:00"},
        {"days": 21, "ok": True, "at": "2026-09-17T20:00:00+00:00"},
        # a run that died before it simmed anything must not push anybody's debut later
        {"days": 28, "ok": False, "at": "2026-09-18T06:00:00+00:00"},
        {"days": 100, "ok": True, "at": "2026-09-18T12:00:00+00:00"}]


def main():
    # ---- the arrival day, derived from the run log ---------------------------------------
    assert first_game_day(CHRIS, RUNS) == 29, first_game_day(CHRIS, RUNS)
    assert first_game_day(DODGER, RUNS) == 129, first_game_day(DODGER, RUNS)
    # NOT 1. "Count everything when you cannot tell" was the safe-looking answer and it is the
    # wrong one: every game before a character existed was played by the filler whose slot he
    # took, so counting them invents head-to-head meetings out of another man's evenings. None
    # means unknown, and build() then publishes no games for him rather than somebody else's.
    assert first_game_day({"created_at": None}, RUNS) is None, "no birth date cannot be guessed at"
    assert first_game_day(CHRIS, []) is None, "an empty log cannot place anybody"

    # A RUN'S `at` IS ITS FINISH. Compare a birth to that and a character created while a run
    # was in flight loses that run's days - but he was not activated by it, because activation
    # happens at the START, before he existed. He is activated by the NEXT run, so the days it
    # simmed happened before he played and must count. Liam was created six minutes into an
    # eleven-minute run; the finish-time rule put his debut a week early.
    mid = [{"days": 100, "ok": True, "at": "2026-09-19T04:11:43+00:00", "seconds": 659},
           {"days": 13, "ok": True, "at": "2026-09-19T03:50:00+00:00", "seconds": 600}]
    assert run_started(mid[0]) == "2026-09-19T04:00:44+00:00", run_started(mid[0])
    during = {"created_at": "2026-09-19T04:05:22+00:00"}    # created while it ran
    before = {"created_at": "2026-09-19T03:59:00+00:00"}    # created just before it started
    assert first_game_day(during, mid) == 114, first_game_day(during, mid)
    assert first_game_day(before, mid) == 14, first_game_day(before, mid)

    # a dry run advances nothing, so it must not move anybody's debut
    dry = mid + [{"days": 50, "ok": True, "dry_run": True,
                  "at": "2026-09-19T03:00:00+00:00", "seconds": 5}]
    assert first_game_day(during, dry) == 114, first_game_day(during, dry)

    # ---- THE ROLLOVER. Seasonday restarts at 1 every year; the run log does not -------------
    seasons = [{"days": 150, "ok": True, "season": 2026, "started_at": "2026-01-01T00:00:00+00:00"},
               {"days": 40, "ok": True, "season": 2027, "started_at": "2027-01-05T00:00:00+00:00"}]
    old_hand = {"created_at": "2026-02-01T00:00:00+00:00"}
    assert first_game_day(old_hand, seasons, season=2027) == 1,         "a character who was here last season must own all of this one"
    assert first_game_day(old_hand, seasons) == 151,         "without a season this sums across the rollover, which is the bug being guarded"
    new_boy = {"created_at": "2027-01-20T00:00:00+00:00"}
    assert first_game_day(new_boy, seasons, season=2027) == 41, first_game_day(new_boy, seasons, 2027)

    # ---- ONLY THIS LEAGUE'S RUNS ------------------------------------------------------------
    # The panel's checkboxes allow a run that advances pro alone. Counting its days against a
    # prep character moves his debut past games that really are his.
    split = [{"days": 21, "ok": True, "season": 2026, "leagues": ["prep", "college", "pro"],
              "started_at": "2026-09-19T01:00:00+00:00"},
             {"days": 7, "ok": True, "season": 2026, "leagues": ["pro"],
              "started_at": "2026-09-19T02:00:00+00:00"}]
    latecomer = {"created_at": "2026-09-19T03:00:00+00:00"}
    assert first_game_day(latecomer, split, season=2026, league="prep") == 22, \
        "a pro-only run must not move a prep debut"
    assert first_game_day(latecomer, split, season=2026, league="pro") == 29

    # ---- A RUN THAT CANNOT BE PLACED IN A SEASON MAKES THE ANSWER UNKNOWN --------------------
    # It used to count in every season, which silently brought back the cross-rollover sum.
    untagged = [{"days": 30, "ok": True, "started_at": "2026-09-19T01:00:00+00:00"}]
    assert first_game_day(latecomer, untagged, season=2026) is None, \
        "a run with no season cannot be counted in one"
    assert first_game_day(latecomer, untagged) == 31, "with no season asked for, it still sums"

    # ---- STORED BEATS DERIVED, AND SAYS SO --------------------------------------------------
    # The day FBPB3 itself was sitting on when simweek stamped him in. Derivation is the
    # fallback, and `since_source` is how anybody ever notices the two disagreeing.
    placed = {**DODGER, "level_history": [
        {"level": "prep", "from_season": 2026, "from_day": 78, "how_it_started": "created"}]}
    assert stored_debut(placed, "prep", 2026) == 78
    assert debut(placed, "prep", RUNS, 2026) == (78, "stored")
    assert debut(DODGER, "prep", RUNS, None)[1] == "run log"
    assert debut({"created_at": None}, "prep", [], 2026) == (None, "unknown")
    # a level entered in an earlier season is his for all of this one, and so is a promotion
    assert stored_debut({"level_history": [{"level": "college", "from_season": 2026,
                                            "from_day": 40}]}, "college", 2027) == 1
    assert stored_debut({"level_history": [{"level": "college", "from_season": 2027,
                                            "how_it_started": "promoted"}]}, "college", 2027) == 1
    # and a level he has not reached yet does not answer for one he has
    assert stored_debut({"level_history": [{"level": "college", "from_season": 2028,
                                            "from_day": 5}]}, "college", 2027) is None

    # ---- UNKNOWN PUBLISHES NOTHING, NOT SOMEBODY ELSE'S GAMES -------------------------------
    blind = build([{**DODGER, "created_at": None}], GAMES, SCHEDULE, TEAMS, runs=[],
                  league="prep", season=2026)
    assert blind["characters"][0]["games"] == [], blind["characters"][0]
    assert blind["characters"][0]["since_source"] == "unknown"

    data = build([CHRIS, DODGER], GAMES, SCHEDULE, TEAMS, runs=RUNS, league="prep",
                 opener="2026-10-20")
    by_name = {c["name"]: c for c in data["characters"]}
    chris, dodger = by_name["Chris Zimmer"], by_name["Dodger Manson"]

    # ---- THE FILLER TRAP -------------------------------------------------------------------
    # Dodger arrived on day 129, so day 31 was played by the man whose slot he took. Counting it
    # would show him a game against a Chris Zimmer who did not exist, and it would look fine.
    assert [g["day"] for g in dodger["games"]] == [134], dodger["games"]
    assert dodger["since_day"] == 129
    # Chris arrived on day 29, so 31 is his and the preseason game on day 1 is not
    assert [g["day"] for g in chris["games"]] == [31, 134], chris["games"]

    # ---- preseason is excluded even for somebody who was there from day one -----------------
    from_day_one = {**CHRIS, "created_at": None,
                    "level_history": [{"level": "prep", "from_day": 1, "from_season": 2026}]}
    early = build([from_day_one], GAMES, SCHEDULE, TEAMS, runs=RUNS, league="prep", season=2026)
    assert early["characters"][0]["since_day"] == 1, early["characters"][0]
    assert early["characters"][0]["since_source"] == "stored", early["characters"][0]
    assert [g["day"] for g in early["characters"][0]["games"]] == [31, 134], \
        "a preseason game reached the record"

    # ---- the numbers, against the rows the MDB really holds --------------------------------
    g = chris["games"][1]
    assert (g["pts"], g["reb"], g["ast"], g["min"], g["fgm"], g["fga"]) == (10, 4, 1, 15, 5, 8), g
    assert g["opp"] == "Generals" and g["home"] is False, g
    assert g["score"] == [43, 45] and g["won"] is False, g   # Berries lost 43-45 away
    assert g["start"] is True and g["playoff"] is False, g

    first = chris["games"][0]
    assert first["home"] is True and first["score"] == [46, 53] and first["won"] is False, first

    # Dodger's one real meeting: he was home and his team won
    d = dodger["games"][0]
    assert d["home"] is True and d["won"] is True and d["score"] == [45, 43], d
    assert (d["pts"], d["min"]) == (6, 18), d

    # ---- the team is per GAME, because people move -----------------------------------------
    assert all(x["team"] == "Berries" for x in chris["games"]), chris["games"]
    assert chris["team"] == "Berries"

    # ---- a character with no player id in this league is simply absent ----------------------
    assert build([{**CHRIS, "league_player_ids": {}}], GAMES, SCHEDULE, TEAMS,
                 league="prep")["characters"] == []

    # ---- day one is the opener ---------------------------------------------------------------
    assert str(day_to_date(1, "2026-10-20")) == "2026-10-20"
    assert str(day_to_date(185, "2026-10-20")) == "2027-04-22", \
        "day 185 must be the last day of the regular season"

    # ---- the launcher, which is where this nearly shipped empty ---------------------------
    # query() ran plain "powershell". On Windows that is the 64-BIT shell, Jet is registered
    # 32-bit only, and a .NET exception is non-terminating in a -File script - so it printed
    # nothing, exited 0, and the caller read the silence as an empty result. Every character
    # would have had zero games, with no error anywhere.
    import os
    from commissioner.headtohead import powershell, query
    wow = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "SysWOW64",
                       "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.exists(wow):
        assert powershell() == wow, f"not using the 32-bit shell: {powershell()}"
    # and a failure must RAISE rather than come back as an empty list
    try:
        query(ROOT / "no-such-database.mdb", "SELECT 1")
    except Exception:
        pass
    else:
        raise AssertionError("a missing database returned rows instead of raising")

    # ---- BUT AN EMPTY RESULT IS NOT A FAILURE ---------------------------------------------
    # The two are told apart by the EXIT CODE and nothing else: mdb_query.ps1 stops on error, so
    # a provider or SQL problem exits non-zero. An earlier version also raised when stdout was
    # empty, reasoning that a real result always carries a header line - which is false, because
    # ConvertTo-Csv of an empty DataTable emits nothing at all.
    #
    # That cost a publish. After FBPB3's rollover PlayerGameStats is legitimately empty - the
    # exact state the games archive exists to survive - and query() raised, so the merge that
    # would have preserved 161 game lines never ran and no games.json was written for any league.
    mdb = next((p for p in [
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Documents" / "GDS"
        / "Fast Break Pro Basketball 3" / "leaguedata" / d / "LeagueOutput.mdb"
        for d in ("Chung_test", "CV_Prep")] if p.exists()), None)
    if mdb is None:
        print("    (no MDB on this machine: the empty-result check is skipped)")
    else:
        rows = query(mdb, "SELECT * FROM PlayerGameStats WHERE ID = -1")
        assert rows == [], f"a query matching nothing must return [], got {rows!r}"
        assert query(mdb, "SELECT COUNT(*) AS n FROM PlayerGameStats"), \
            "a query that DOES match must still return its rows"
        for bad in ("SELECT * FROM NoSuchTable", "SELECT nonsense FROM"):
            try:
                query(mdb, bad)
            except Exception:
                pass
            else:
                raise AssertionError(f"{bad!r} returned rows instead of raising")

    print("OK  head-to-head: the filler's games stay out, preseason stays out, numbers match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
