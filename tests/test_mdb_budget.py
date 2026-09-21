"""The Output MDB budget has to exceed the league it is given, or that league never exports.

WHAT WENT WRONG. `output_mdb`'s own docstring records the measurements taken on 2026-09-20:
prep 19.5s, college 170s, PRO 211s. `d3d0a2eb2` then bounded every league at
`timeout=45, max_seconds=120` to shorten publishing - below pro's measured time - so pro's export
could not finish inside its own budget and failed on every run from that commit onward:

    no MDB for pro (Output MDB made no file progress for 45s ...); head-to-head will not update

Pro's LeagueOutput.mdb still read 14:03 hours later while prep's and college's were current, so
head-to-head and everything else built from that table was quietly stale for pro alone.

A budget that is smaller than the thing it measures is not a budget, it is an outage on a timer,
and nothing failed loudly enough to say so - the export step catches the error and carries on,
because a missing optional table must never take down a finished week.

    python tests/test_mdb_budget.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import simweek  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

# Seconds each league's Output MDB actually took, measured 2026-09-20 on the live saves.
MEASURED = {"prep": 19.5, "college": 170.0, "pro": 211.0}

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def run():
    print("every league has a budget, and every budget clears the measured time")
    for key, seconds in MEASURED.items():
        stall, cap = simweek.MDB_BUDGET[key]
        check(f"{key}: absolute cap {cap}s clears its measured {seconds}s", cap > seconds, True)
        # Headroom, not a coin flip: a save grows, and the machine has other work on it.
        check(f"{key}: with at least 25% headroom", cap >= seconds * 1.25, True)
        check(f"{key}: the no-progress budget is the tighter of the two", stall < cap, True)

    print("every configured league is covered")
    for spec in cfg.LEAGUES:
        check(f"{spec.key} has a budget", spec.key in simweek.MDB_BUDGET, True)

    print("the regression itself: the old flat bound would have failed pro and college")
    old_stall, old_cap = 45, 120
    check("old cap was below pro's measured time", old_cap < MEASURED["pro"], True)
    check("and below college's", old_cap < MEASURED["college"], True)
    check("pro's cap is no longer below it", simweek.MDB_BUDGET["pro"][1] > MEASURED["pro"], True)
    check("college's cap is no longer below it",
          simweek.MDB_BUDGET["college"][1] > MEASURED["college"], True)
    check("and prep, which always passed, still has room",
          simweek.MDB_BUDGET["prep"][1] > MEASURED["prep"], True)
    check("the old no-progress budget is not reused as-is for pro",
          simweek.MDB_BUDGET["pro"][0] > old_stall, True)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  mdb budget: each league's absolute cap clears its measured export time with "
          "headroom, and the flat 120s bound that silently starved pro is gone")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
