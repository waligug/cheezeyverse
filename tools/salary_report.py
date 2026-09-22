"""What the game is actually paying people, read straight off a save.

WHY IT MATTERS TWICE OVER. A salary decides whether a player survives a load at all (no contract
means released under Full Finances), and it is the input to the annual contract payout, which
bands players by percentile within the league's own distribution. A league where every contract
is the same number is not a distribution - it is Finances being off wearing a hat - so this
reports the SPREAD, not just the total.

    python tools/salary_report.py CV_Pro
    python tools/salary_report.py CV_FinTest --by-team
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import LeagueDat  # noqa: E402
from commissioner.driver.fbpb3 import DOCS  # noqa: E402


def money(n):
    return f"${n:,}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("save")
    ap.add_argument("--by-team", action="store_true")
    a = ap.parse_args(argv)

    path = DOCS / "leaguedata" / a.save / "league.dat"
    if not path.exists():
        print(f"no such save: {path}")
        return 2
    L = LeagueDat(path)
    print(f"{a.save}: {len(L.players)} players, season/day {L.season_day()}")

    rostered = [p for p in L.players if p.values["Team"] > 0]
    pool = [p for p in L.players if p.values["Team"] <= 0]
    print(f"  on a roster {len(rostered)}    free agents / draft {len(pool)}")

    bare = [p for p in rostered if not any(p.contract)]
    print(f"  rostered with NO contract: {len(bare)}"
          + ("   <- a Full-Finances load releases these" if bare else "   (safe to load)"))

    pay = [p.contract[0] for p in rostered if p.contract[0] > 0]
    if not pay:
        print("\n  nobody on a roster is being paid anything.")
        return 0

    distinct = sorted(set(pay))
    print(f"\n  paid players {len(pay)}   DISTINCT salaries {len(distinct)}")
    if len(distinct) == 1:
        # This is the degenerate case the payout banding has to survive: one salary is not a
        # ranking, it is the import constant nobody has overwritten yet.
        print(f"  every single one is {money(distinct[0])} - there is no distribution here")
    else:
        print(f"  low {money(min(pay))}   median {money(int(statistics.median(pay)))}   "
              f"high {money(max(pay))}")
        print(f"  mean {money(int(statistics.mean(pay)))}   total {money(sum(pay))}")
        buckets = collections.Counter()
        for v in pay:
            buckets[round(v, -6)] += 1
        print("  by $1M bucket:", {money(k): n for k, n in sorted(buckets.items())[:12]})

    years = collections.Counter(sum(1 for y in p.contract if y > 0) for p in rostered)
    print(f"  contract LENGTHS (years with money): {dict(sorted(years.items()))}")

    if a.by_team:
        print("\n  payroll by team")
        by = collections.defaultdict(list)
        for p in rostered:
            by[p.values["Team"]].append(p.contract[0])
        for t in sorted(by):
            vals = by[t]
            print(f"    team {t:>3}  {len(vals):2} men  payroll {money(sum(vals)):>14}  "
                  f"top {money(max(vals))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
