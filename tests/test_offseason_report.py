"""The offseason has to arrive in Discord as news, not as a log line.

It is the most dramatic thing that happens in this universe - everybody ages, some get promoted,
some are drafted, some retire - and it used to be reported as counts. "3 grew" tells seven
friends nothing about themselves. Everything in the message names somebody.

Two things this pins that are easy to lose:

  * THE BIGGEST MOVER IS ONE NAMED PERSON. "Everyone improved" is a report; "Johnny improved
    most" is a competition, and a competition is what this is.
  * SOMEBODY WHO WENT NOWHERE IS SAID OUT LOUD. He is exactly who needs to know, and he will
    never work it out from a list of winners.

`season_movers` reads `rating_snapshots`, which every Sim Week has written all year and nothing
has ever read back. It compares the first and last snapshot of the season being closed, using
the mean of the ratings rather than FBPB3's own Overall - CONVENTIONS records that field as
unverified and mostly zero.

STAMINA IS NOT IN THAT MEAN. It was set administratively to a flat 70 for everybody, which is
worth more sheet average than a whole season of earned movement, so including it would rank
"most improved" by who started with the worst conditioning.

    python tests/test_offseason_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import RATINGS  # noqa: E402
from commissioner.offseason import (  # noqa: E402
    MOVER_FIELDS, _offseason_report, season_movers)


def _sheet(value):
    return {r: value for r in RATINGS}


class FakeStore:
    """Just enough store to answer snapshots(), the way the real one does: oldest first."""

    def __init__(self, rows):
        self.rows = rows

    def snapshots(self, character_id=None, league=None):
        return [r for r in self.rows if r["character_id"] == character_id]


def main():
    chars = [{"id": "a", "first_name": "Johnny", "last_name": "Gartholomew", "status": "active"},
             {"id": "b", "first_name": "Tim", "last_name": "Turner", "status": "active"},
             {"id": "c", "first_name": "Gone", "last_name": "Away", "status": "retired"},
             {"id": "d", "first_name": "Brand", "last_name": "New", "status": "active"}]
    store = FakeStore([
        {"character_id": "a", "season": 2026, "week": 1, "ratings": _sheet(26)},
        {"character_id": "a", "season": 2026, "week": 22, "ratings": _sheet(33)},
        {"character_id": "b", "season": 2026, "week": 1, "ratings": _sheet(25)},
        {"character_id": "b", "season": 2026, "week": 22, "ratings": _sheet(25)},
        # last season's rows must not be mistaken for this one's
        {"character_id": "a", "season": 2025, "week": 1, "ratings": _sheet(5)},
        # one snapshot is not a line, so he is not a mover
        {"character_id": "d", "season": 2026, "week": 22, "ratings": _sheet(30)},
    ])

    movers = season_movers(chars, store, 2026)
    assert [m["name"] for m in movers] == ["Johnny Gartholomew", "Tim Turner"], movers
    assert movers[0]["gain"] == 7.0 and movers[0]["from"] == 26.0, movers[0]
    assert movers[1]["gain"] == 0.0, movers[1]
    # a retired character is not in the running, and neither is somebody with a single snapshot
    assert not any(m["name"].startswith(("Gone", "Brand")) for m in movers), movers
    # 2025's row must not drag Johnny's starting point down to 5
    assert movers[0]["from"] == 26.0, "a previous season's snapshot leaked into this one"

    # STAMINA IS EXCLUDED. On 2026-09-19 every character's Stamina was set administratively to a
    # flat 70, which is worth +2.0 to +2.8 of sheet average against 0.0 to 1.4 actually earned
    # across the whole season. Left in, "most improved" ranks by who started with the worst
    # conditioning and stops naming the man who genuinely went nowhere.
    only_stamina = FakeStore([
        {"character_id": "a", "season": 2026, "week": 1, "ratings": {**_sheet(20), "Stamina": 12}},
        {"character_id": "a", "season": 2026, "week": 22, "ratings": {**_sheet(20), "Stamina": 70}},
    ])
    moved = season_movers([chars[0]], only_stamina, 2026)
    assert moved and moved[0]["gain"] == 0.0,         f"a stamina-only change was counted as improvement: {moved}"
    assert "Stamina" not in MOVER_FIELDS and len(MOVER_FIELDS) == len(RATINGS) - 1

    # a store with no history at all must not raise - the offseason has to finish regardless
    class Bare:
        pass
    assert season_movers(chars, Bare(), 2026) == []

    class Angry:
        def snapshots(self, character_id=None, league=None):
            raise RuntimeError("database is having a day")
    assert season_movers(chars, Angry(), 2026, log=lambda *_a: None) == []

    # ---- the message ---------------------------------------------------------------------
    result = {"season": 2026,
              "grew": [{"name": "Chris Zimmer", "inches": 2, "height": 76},
                       {"name": "Zach Russell", "inches": 1, "height": 63}],
              "movers": movers, "promoted": ["Chris Zimmer to college"], "drafted": [],
              "retired": ["Old Man"], "paid": 7, "season_bonus": 13, "failed": []}
    text = _offseason_report(result)

    assert "Most improved: Johnny Gartholomew" in text, text
    assert "+7.0" in text, text
    assert "no movement at all: Tim Turner" in text, "the man who went nowhere was not told"
    assert "6'4\"" in text, f"height not rendered in feet and inches: {text}"
    assert "Chris Zimmer +2" in text, text
    assert "Moved up" in text and "Retired" in text, text
    assert "13 season-bonus" in text, text
    # Discord rejects anything over 2000 characters outright, and notify trims at 1900
    assert len(text) < 1900, len(text)

    # an offseason where nothing happened should still say something, and not crash
    quiet = _offseason_report({"season": 2027, "grew": [], "movers": [], "promoted": [],
                               "drafted": [], "retired": [], "failed": []})
    assert "Offseason 2027" in quiet, quiet

    print("OK  offseason report: names the mover, names who stalled, fits in a Discord message")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
