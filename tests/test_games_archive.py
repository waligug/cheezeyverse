"""Game history survives a season rollover, which the MDB cannot make it do.

THE FAILURE THIS PREVENTS. `games.json` was rebuilt from `LeagueOutput.mdb` every publish. The
MDB holds exactly one season - measured: PlayerGameStats and Schedule both have NO season column,
and a real export held 9,856 rows over Seasonday 1-176 with no (Day, Home, Away) repeated - so
the export replaces rather than accumulates.

The first publish after FBPB3's rollover would therefore have read an empty PlayerGameStats and
written a games.json with zero lines for all seven characters. Head-to-head goes blank, and 161
game lines - every night Nate's friends have played, including the playoff run - survive only in
the published branch's git history. No error, no warning: the page simply says nobody has played.

The properties, each of which the old code got wrong:

  * A CHARACTER MISSING FROM THE EXPORT KEEPS HIS HISTORY. Not a corner case - it is what happens
    to everybody the instant the rollover empties the table.
  * THIS SEASON'S EXPORT WINS FOR THIS SEASON. A re-sim after a fix must correct a night, not sit
    beside the old version and have every average count it twice.
  * OTHER SEASONS ARE NEVER TOUCHED. Nothing can re-derive them once the MDB has moved on.
  * UNSTAMPED MEANS 2026, because the lines archived before the stamp existed were played in the
    universe's only season.

    python tests/test_games_archive.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import gamesarchive as ga  # noqa: E402


def line(day, pts, opp="X"):
    return {"day": day, "pts": pts, "team": "A", "opp": opp, "won": True}


def payload(season, *entries):
    return {"league": "prep", "season": season, "characters": list(entries)}


def games_of(payload, name):
    for c in payload["characters"]:
        if c["name"] == name:
            return c["games"]
    return None


def main():
    # 2026, as archived: the season is stamped ONCE at the top of the file, not on every line
    y2026 = payload(2026,
                    {"id": "1", "name": "Chris", "team": "Berries",
                     "games": [line(5, 10), line(9, 12), line(78, 8)]},
                    {"id": "2", "name": "Gravy", "team": "Tulips", "games": [line(99, 4)]})
    history = [(2026, y2026)]

    # ---- the rollover case: the MDB has been emptied ------------------------------------
    empty = payload(2027,
                    {"id": "1", "name": "Chris", "team": "Berries", "games": []},
                    {"id": "2", "name": "Gravy", "team": "Tulips", "games": []})
    after = ga.merge(history, empty, 2027)
    assert len(games_of(after, "Chris")) == 3,         f"the rollover wiped Chris: {games_of(after, 'Chris')}"
    assert len(games_of(after, "Gravy")) == 1, games_of(after, "Gravy")

    # every published line carries its season, because the page matches meetings by day and
    # day 5 of 2026 is not day 5 of 2027
    assert all(g["season"] == 2026 for g in games_of(after, "Chris")), games_of(after, "Chris")
    assert after["seasons"] == [2026, 2027], after["seasons"]

    # ---- a character absent from the export ENTIRELY keeps his history -------------------
    gone = payload(2027, {"id": "1", "name": "Chris", "team": "Berries", "games": []})
    after = ga.merge(history, gone, 2027)
    assert games_of(after, "Gravy") and len(games_of(after, "Gravy")) == 1,         "a character the export no longer mentions lost his games"

    # ---- next season's games are ADDED, not swapped in ------------------------------------
    played = payload(2027, {"id": "1", "name": "Chris", "team": "Berries",
                            "games": [line(3, 20), line(7, 15)]})
    after = ga.merge(history, played, 2027)
    chris = games_of(after, "Chris")
    assert len(chris) == 5, f"expected 3 old + 2 new, got {len(chris)}"
    # day 3 of 2027 and day 5 of 2026 are different nights and must both survive
    assert [(g["season"], g["day"]) for g in chris] ==         [(2026, 5), (2026, 9), (2026, 78), (2027, 3), (2027, 7)], chris

    # ---- a re-export of the SAME season corrects, never duplicates -------------------------
    corrected = payload(2026, {"id": "1", "name": "Chris", "team": "Berries",
                               "games": [line(5, 99), line(9, 12), line(78, 8)]})
    after = ga.merge(history, corrected, 2026)
    chris = games_of(after, "Chris")
    assert len(chris) == 3, f"a re-export duplicated nights: {len(chris)}"
    assert [g["pts"] for g in chris] == [99, 12, 8], "the re-export did not win for its own season"

    # ---- an EARLIER season is never touched by a later publish ----------------------------
    after = ga.merge(history, played, 2027)
    assert [g["pts"] for g in games_of(after, "Chris") if g["season"] == 2026] == [10, 12, 8],         "publishing 2027 altered 2026"

    # ---- the newer entry's own fields win, without costing him his games -------------------
    moved = payload(2027, {"id": "1", "name": "Chris", "team": "Clams", "since_day": 1,
                           "since_source": "stored", "games": []})
    after = ga.merge(history, moved, 2027)
    row = next(c for c in after["characters"] if c["name"] == "Chris")
    assert row["team"] == "Clams" and row["since_source"] == "stored", row
    assert len(row["games"]) == 3, "updating his details cost him his history"

    # ---- nothing archived yet is not an error ---------------------------------------------
    first = ga.merge([], played, 2027)
    assert len(games_of(first, "Chris")) == 2, first

    # ---- and against the REAL archived season, with the real rollover shape ----------------
    real = ga.archived_seasons("prep")
    if real:
        total = sum(len(c["games"]) for _s, p in real for c in p["characters"])
        wiped = payload(2027, *[{**{k: v for k, v in c.items() if k != "games"}, "games": []}
                                for _s, p in real for c in p["characters"]])
        out = ga.merge(real, wiped, 2027)
        kept = sum(len(c["games"]) for c in out["characters"])
        assert kept == total, f"the real history lost lines: {total} -> {kept}"
        print(f"    against the real archive: {total} lines survive the rollover")

    print("OK  games archive: history survives a rollover, a re-export corrects rather than "
          "duplicates, and an absent character keeps his games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
