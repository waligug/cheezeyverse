"""Give every rostered player a contract, so Full Finances does not release him on load.

WHY THIS HAS TO EXIST. CONVENTIONS.md:73 - with Full Finances, a player with no contract is
RELEASED the moment the league loads. Our players get into a save through the codec, which until
now could not write money, so every character we ever stamped in and every body the roster guard
backfilled carries nothing. On the live pro save that is 45 players on real rosters, 11 of them
the reserve seats a drafted character is stamped into.

WHAT IT DOES NOT TOUCH. Free agents and the draft pool are left alone. A free agent with no
contract is just a free agent - the release rule only bites a player who is ON a roster - and
writing money onto a draft-pool row is a change whose effect nobody has measured. Narrow on
purpose: this is reversible in the sense that it only ever ADDS a contract to somebody who had
none, and it never changes one that already exists.

    python tools/grant_contracts.py CV_FinTest              # report only, writes nothing
    python tools/grant_contracts.py CV_FinTest --apply
    python tools/grant_contracts.py CV_Pro --apply --backup backups

The save is named by its leaguedata folder, never by league key, because the whole point is to
work on a clone that is not the live universe.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import LeagueDat  # noqa: E402
from commissioner.driver.fbpb3 import DOCS  # noqa: E402

# What 255 of the 530 pro players already carry: `universe/generate.py` IMPORT_CONTRACT. Matching
# it keeps one number in the league rather than inventing a second, and 15 x $1M is $15M against
# a $63M cap, so no team can be pushed over by this.
SALARY = 1_000_000
# MORE THAN ONE YEAR, and this is the whole lesson of the day. A one-year deal stops a
# player being released ON LOAD and does nothing about the ROLLOVER: measured on a clone
# 2026-09-22, every one-year contract in the league expired together at the offseason and
# all 511 players became free agents at once. So a one-year grant is a release postponed
# by a season, and postponed trouble is the kind nobody connects to its cause. Matches the
# codec's own SIGNING_YEARS so the two cannot drift.
YEARS = 3


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("save", help="leaguedata folder name, e.g. CV_FinTest")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    ap.add_argument("--salary", type=int, default=SALARY)
    ap.add_argument("--years", type=int, default=YEARS)
    ap.add_argument("--backup", default=None, help="folder to copy the save into first")
    a = ap.parse_args(argv)

    path = DOCS / "leaguedata" / a.save / "league.dat"
    if not path.exists():
        print(f"no such save: {path}")
        return 2
    print(f"save: {path}")

    L = LeagueDat(path)
    rostered = [p for p in L.players if p.values["Team"] > 0]
    bare = [p for p in rostered if not any(p.contract)]
    pool = [p for p in L.players if p.values["Team"] <= 0 and not any(p.contract)]

    print(f"  players            {len(L.players)}")
    print(f"  on a roster        {len(rostered)}")
    print(f"  rostered, NO deal  {len(bare)}   <- these are released on a Full-Finances load")
    print(f"  pool, NO deal      {len(pool)}   (free agents and draft; left alone on purpose)")

    if not bare:
        print("\nnothing to do: every rostered player already has a contract")
        return 0

    by_team = {}
    for p in bare:
        by_team.setdefault(p.values["Team"], []).append(p)
    print(f"\n  affected teams: {len(by_team)}")
    for t in sorted(by_team):
        names = ", ".join(f"{p.first} {p.last}" for p in by_team[t][:3])
        more = f" +{len(by_team[t]) - 3} more" if len(by_team[t]) > 3 else ""
        print(f"    team {t:>3}  {len(by_team[t]):2}  {names}{more}")

    if not a.apply:
        print(f"\nreport only. Re-run with --apply to write ${a.salary:,} x {a.years}yr "
              f"to {len(bare)} players.")
        if a.years < 2:
            print("   NOTE: a one-year deal expires at the very next rollover's free agency.")
        return 0

    deal = [a.salary] * a.years
    for p in bare:
        L.set_contract(p, deal)
    # save() re-parses and compares every field INCLUDING the contracts, so a bad offset or a
    # truncated write fails here rather than surfacing as a released player days later.
    L.save(backup_dir=a.backup)
    print(f"\nwrote ${a.salary:,} x {a.years}yr to {len(bare)} players; save() verified the reread.")

    again = LeagueDat(path)
    still = [p for p in again.players if p.values["Team"] > 0 and not any(p.contract)]
    print(f"rostered players still without a contract: {len(still)}")
    return 0 if not still else 1


if __name__ == "__main__":
    raise SystemExit(main())
