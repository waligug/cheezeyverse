"""The save's own day counter, which is where a character's arrival day comes from.

FBPB3 keeps the current season day and year in league.dat's header. head-to-head needs that day:
a character takes over a reserve slot that has been playing all season, so only the games from
the day he was stamped in are his. It used to be reconstructed by adding up the run log, which is
wrong the moment a run is missing, rewound by restore_backup, or simmed by a tool that logs
somewhere else.

THERE IS NO FIXED OFFSET - the pair moved 68 bytes in one week of real saves - so it is found by
its shape: >= 32 zero bytes, the day, the year, then -1 -1 -1 0. Checked on 2026-09-19 against
every backup of all three live leagues: exactly one match each, and the days came out as the run
log's own sequence, 1, 8, 15, 22, 29, 36, 57, 78, 99, 106, 113, 120, 127, 155.

The bytes here are synthetic on purpose: this pins the RULE, and the shape was measured against
real saves. tools/verify_save.py sees the real ones.

    python tests/test_season_day.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import find_season_day  # noqa: E402


def blob(day, year, *, zeros=64, before=b"", after=b""):
    """A header-shaped run of bytes with one day/year pair in it."""
    return (before + bytes(zeros) + struct.pack("<2h", day, year)
            + struct.pack("<4h", -1, -1, -1, 0) + after)


def main():
    assert find_season_day(blob(155, 2026)) == (155, 2026)
    assert find_season_day(blob(1, 2026)) == (1, 2026), "day one is a real day"
    assert find_season_day(blob(244, 2031)) == (244, 2031), "the postseason runs past day 200"

    # REFUSES TO GUESS. Two candidates cannot be told apart, and a wrong day hands one
    # character another's games while looking entirely plausible.
    two = blob(155, 2026) + blob(31, 2026)
    assert find_season_day(two) is None, "two candidates must not silently pick one"
    assert find_season_day(b"") is None
    assert find_season_day(bytes(500)) is None, "all zeros is not a day"

    # The zero run is what separates the header pair from ordinary data that happens to be
    # followed by -1 -1 -1 0, which is why it is part of the signature.
    noisy = b"\x05\x00" * 40 + struct.pack("<2h", 90, 2026) + struct.pack("<4h", -1, -1, -1, 0)
    assert find_season_day(noisy) is None, "a pair with data in front of it is not the header"

    # Implausible values are rejected rather than returned: 0 is not a day, and 1899 is not one
    # of our seasons.
    assert find_season_day(blob(0, 2026)) is None
    assert find_season_day(blob(401, 2026)) is None
    assert find_season_day(blob(155, 1899)) is None

    # `limit` stops the search before the player records, where -1 -1 -1 0 is commonplace.
    early = blob(155, 2026)
    assert find_season_day(early + blob(9, 2026), limit=len(early)) == (155, 2026)

    print("OK  season day: found by shape, refuses to guess, and stops before the players")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
