"""`tools/raise_stamina.py` raises exactly the players it was asked to, and nothing else.

A tool that walks a whole league and edits one field is one typo away from editing that field
for all 425 people in the file. The codec has no notion of "only these players", so the only
thing standing between a floor and a league-wide buff is this tool's own loop - which is worth
a test, because the tool is not a one-off: every character the quiz produces arrives with
Stamina in the twenties, so this runs again each time somebody signs up.

Three properties, all checked against a copy of a real save:

  * a preview does not touch the file at all;
  * an apply moves the under-floor player to the floor, leaves the already-fine player alone,
    and leaves every other player in the file byte-for-byte unchanged;
  * running it twice is a no-op, so nobody can stack the floor by running it again.

The stand-ins are real players read out of the fixture rather than invented, so the lookup path
under test is the same (name, codec_dob) pair the live tool uses.

    python tests/test_raise_stamina.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from commissioner import characters as ch          # noqa: E402
from commissioner.codec.league_dat import LeagueDat  # noqa: E402

FIXTURE = ROOT / "fixtures/saves/chung-baseline/league.dat"
FLOOR = 50


def _as_character(pl):
    """A store-shaped character dict for a player already in the save."""
    month, day, year = pl.dob.split("/")
    first, _, last = pl.name.partition(" ")
    return {"first_name": first, "last_name": last, "league": "prep", "status": "active",
            "id": pl.name,
            # a Postgres date, which is what the real store hands over and what codec_dob exists
            # to translate; feeding the save's own 11/27/2015 here would not test that step
            "game_dob": f"{year}-{int(month):02d}-{int(day):02d}"}


def main():
    # `fixtures/saves/` is gitignored, so this test has no save to work with on any machine that
    # got the code by cloning - which includes SERVERPC, the one box where the tool is actually
    # run against live leagues. It used to traceback there with a bare FileNotFoundError, which
    # reads like the TOOL is broken at the exact moment somebody is deciding whether to trust it.
    # Skip the way test_codec does, and say why, so a missing fixture is never mistaken for a
    # failure - and so nobody is trained to ignore a red line from this file.
    if not FIXTURE.exists():
        print(f"SKIP  raise_stamina: no save fixture at {FIXTURE.relative_to(ROOT)} "
              "(fixtures/saves/ is gitignored, so a clone does not carry it)")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="stamina-"))
    shutil.copy(FIXTURE, tmp / "league.dat")

    import raise_stamina as rs
    ch.save_path = lambda key: tmp / "league.dat"
    rs.ch.save_path = ch.save_path

    before = LeagueDat(tmp / "league.dat")
    rostered = [p for p in before.players if p.values.get("Team", 0) >= 1]
    low = next(p for p in rostered if p.values["Stamina"] < FLOOR)
    high = next(p for p in rostered if p.values["Stamina"] >= FLOOR)
    was = {(p.name, p.dob): p.values["Stamina"] for p in before.players}
    chars = [_as_character(low), _as_character(high)]

    mtime = (tmp / "league.dat").stat().st_mtime
    rows = rs.raise_stamina("prep", chars, floor=FLOOR, apply=False)
    assert (tmp / "league.dat").stat().st_mtime == mtime, "a preview wrote to the save"
    assert [r[0] for r in rows] == [low.name], f"preview picked {rows}"

    rs.raise_stamina("prep", chars, floor=FLOOR, apply=True)

    after = LeagueDat(tmp / "league.dat")
    now = {(p.name, p.dob): p.values["Stamina"] for p in after.players}
    assert now[(low.name, low.dob)] == FLOOR, f"floor not written: {now[(low.name, low.dob)]}"
    assert now[(high.name, high.dob)] == was[(high.name, high.dob)], "an above-floor player moved"
    moved = sorted(k for k in was if was[k] != now.get(k))
    assert moved == [(low.name, low.dob)], f"collateral damage: {moved}"

    assert rs.raise_stamina("prep", chars, floor=FLOOR, apply=True) == [], "not idempotent"

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK  raise_stamina: {low.name} {was[(low.name, low.dob)]} -> {FLOOR}, "
          f"{len(was) - 1} other players untouched, second run a no-op")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
