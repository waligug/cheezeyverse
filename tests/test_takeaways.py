"""The season takeaways: every line earned, and never able to take an offseason down.

The offseason report named who grew and who improved most, and stopped. Everything that
happened on a court was sitting unread in two files the publish already writes. This turns them
into lines a group chat would actually read.

Four properties worth pinning, three of them learned by getting them wrong against real data:

  * A LINE ONLY EXISTS IF A NUMBER SUPPORTS IT. No "solid season", no "good numbers". Missing
    data makes the report shorter, never vaguer.
  * THE STANDINGS KEYS ARE `w` AND `l`. Reading them as wins/losses printed every team's record
    as "?-?", which looks like missing data and is really a misspelt key.
  * HEAD-TO-HEAD IS TOLD FROM THE WINNER'S SIDE, once per pair. Told from whichever name sorted
    first it produced a wall of "Chris Zimmer won 0" - true, unreadable, and less interesting
    than what actually happened.
  * IT CAN NEVER RAISE. It is colour on a report that runs after the saves have been written and
    the points paid. A scoreline must not be able to fail an offseason.

    python tests/test_takeaways.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import takeaways  # noqa: E402

FAILS = []


def ok(cond, msg):
    if not cond:
        FAILS.append(msg)


def game(day, opp, won, pts, **kw):
    row = {"day": day, "team": "Ironworks", "opp": opp, "home": True, "won": won,
           "score": [70, 60] if won else [60, 70], "playoff": False, "min": 30,
           "pts": pts, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "to": 0, "pf": 0,
           "fgm": 0, "fga": 0, "ftm": 0, "fta": 0, "tpm": 0, "tpa": 0, "season": 2027}
    row.update(kw)
    return row


def write(site, league, stats, games):
    d = site / "leagues" / league
    d.mkdir(parents=True, exist_ok=True)
    (d / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    (d / "games.json").write_text(json.dumps(games), encoding="utf-8")


def main():
    work = Path(tempfile.mkdtemp(prefix="takeaways-"))
    keep = takeaways.SITE
    try:
        takeaways.SITE = work
        stats = {"table": [{"name": "Ironworks", "w": 24, "l": 6, "games": 30, "pct": .8},
                           {"name": "Spies", "w": 10, "l": 20, "games": 30, "pct": .33}],
                 "players": [
                     {"name": "Ace Star", "team": "Ironworks", "PTS": 400, "REB": 100,
                      "AST": 50, "STL": 10, "BLK": 5, "ts": .6,
                      "rank": {"PTS": 1, "REB": 4, "AST": 30, "STL": 40, "BLK": 50}},
                     {"name": "Cold Shot", "team": "Spies", "PTS": 90, "REB": 20,
                      "AST": 10, "STL": 2, "BLK": 1, "ts": .3,
                      "rank": {"PTS": 80, "REB": 90, "AST": 95, "STL": 99, "BLK": 99}}]}
        games = {"characters": [
            {"name": "Ace Star", "team": "Ironworks", "games": [
                game(1, "Spies", True, 12), game(2, "Spies", True, 31, reb=12, ast=5),
                game(3, "Rails", False, 8)]},
            {"name": "Cold Shot", "team": "Spies", "games": [
                game(1, "Ironworks", False, 6, tpm=1, tpa=20),
                game(2, "Ironworks", False, 4, tpm=0, tpa=6),
                game(3, "Rails", True, 9)] + [game(n, "Rails", True, 5) for n in range(4, 12)]}]}
        write(work, "prep", stats, games)
        lines = takeaways.for_league("prep", 2027, {"Ace Star", "Cold Shot"})
        text = "\n".join(lines)

        ok("31 pts" in text and "12 reb" in text,
           f"the best night was not found or not described: {text}")
        ok("Game of the season" in text and "Ace Star" in text,
           "the game of the season did not name the right player")
        ok("Ironworks 24-6" in text,
           f"the team record came out wrong - standings use 'w' and 'l': {text}")
        ok("?-?" not in text, f"a record printed as ?-?, the misspelt-key bug: {text}")
        ok("1st in points (400)" in text, f"the league placing is missing or wrong: {text}")
        # Cold Shot ranks 80th+ in everything, so he must not be listed among the placings.
        ok("Cold Shot 80th" not in text and "80th in" not in text,
           f"an unremarkable placing was reported as news: {text}")
        ok("1-for-26 from three" in text,
           f"the cold streak was not found (1 of 26 across the season): {text}")
        ok("Ace Star took 2 of 2 from Cold Shot (swept)" in text,
           f"head to head is not told from the winner's side: {text}")
        ok("won 0" not in text, f"the one-sided phrasing came back: {text}")
        ok("league leader" in text and "Ace Star" in text, "the league leader line is missing")

        # ---- a split, rather than a sweep ---------------------------------------------------
        games2 = {"characters": [
            {"name": "Ace Star", "team": "Ironworks", "games": [
                game(1, "Spies", True, 10), game(2, "Spies", False, 10)]},
            {"name": "Cold Shot", "team": "Spies", "games": [
                game(1, "Ironworks", False, 10), game(2, "Ironworks", True, 10)]}]}
        write(work, "college", stats, games2)
        split = "\n".join(takeaways.for_league("college", 2027, {"Ace Star", "Cold Shot"}))
        ok("split their 2" in split, f"an even head-to-head was not reported as a split: {split}")

        # ---- nothing to say is silence, not filler ------------------------------------------
        write(work, "pro", {"table": [], "players": []}, {"characters": []})
        ok(takeaways.for_league("pro", 2027, set()) == [],
           "a league with no data produced lines anyway")
        ok(takeaways.for_league("nosuch", 2027, set()) == [],
           "a league with no files at all did not come back empty")

        # ---- it must never raise -------------------------------------------------------------
        write(work, "broken", {"players": [{"name": "X"}]},
              {"characters": [{"name": "X", "games": [{"season": 2027, "min": 5}]}]})
        try:
            takeaways.for_league("broken", 2027, {"X"})
        except Exception as exc:                                        # noqa: BLE001
            FAILS.append(f"malformed data raised instead of being skipped: {exc!r}")
        try:
            out = takeaways.for_season(2027, store=None, leagues=("broken", "nosuch"))
            ok(isinstance(out, list), "for_season did not return a list")
        except Exception as exc:                                        # noqa: BLE001
            FAILS.append(f"for_season raised, which would fail an offseason: {exc!r}")
    finally:
        takeaways.SITE = keep
        shutil.rmtree(work, ignore_errors=True)

    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  takeaways: the best night, the league placings that are worth saying, the team "
          "records off the real keys, head-to-head from the winner's side, the cold streak - "
          "and nothing at all when there is nothing to say")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
