"""Show - or run - the age-out that the rollover performs on the AI population.

The offseason does this by itself. This is for looking at it first, which is worth doing once
before a rollover you care about, because it names everybody who is about to stop playing.

    python tools/age_out.py                      # what would happen, all leagues
    python tools/age_out.py --season 2027        # against a season other than the store's
    python tools/age_out.py prep --names         # list every player who would go
    python tools/age_out.py prep --run           # actually do it (the offseason normally does)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import ageout  # noqa: E402
from commissioner.simweek import store  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("leagues", nargs="*", default=None,
                    help="prep and/or college (pro has no cap); default both")
    ap.add_argument("--season", type=int, default=None, help="default: the store's current season")
    ap.add_argument("--names", action="store_true", help="name everyone who would go")
    ap.add_argument("--run", action="store_true", help="WRITE the saves instead of reporting")
    args = ap.parse_args()

    st = store()
    season = args.season or int(st.get_settings().get("current_season", 0))
    if not season:
        print("no season: pass --season")
        return 2
    keys = args.leagues or ["prep", "college"]

    print(f"season {season}\n")
    for key in keys:
        if key not in ageout.AGE_CAPS:
            print(f"{key}: no age cap - a pro career ends by retirement, not by graduating\n")
            continue
        p = ageout.plan(key, season, store=st)
        cap, intake = ageout.AGE_CAPS[key], ageout.INTAKE_AGE[key]
        print(f"{key}: cap {cap}, intake at {intake}")
        print(f"   {len(p['retiring'])} of {p['rostered']} rostered players are {cap} or older")
        print(f"   {p['intake']} newcomers would be recycled from a pool of {p['pool']}")
        if args.names:
            for r in sorted(p["retiring"], key=lambda r: (-r["age"], r["name"])):
                print(f"      {r['age']}  {r['name']:28s} team {r['team']}")
        if args.run:
            print("   running:")
            out = ageout.apply(key, season, store=st, log=lambda m: print(f"    {m.strip()}"))
            print(f"   -> {len(out['retired'])} retired, {len(out['arrived'])} joined")
        print()
    if not args.run:
        print("nothing was written. The rollover does this itself; --run forces it early.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
