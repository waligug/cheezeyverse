"""The roster guard has to run out of work, or it is not guarding - it is churning.

WHAT WENT WRONG. `protect_rosters` evicts anyone the manifest does not name, then refills short
teams from free agency. A refilled body is not in the manifest, so the NEXT run evicts it again,
refills again, and the pass has fresh work on every publish while nothing improves. That loop is
what emptied prep's rosters down to a team of six in 2029, and the comment in protect_rosters
records it.

It was closed for prep and college by `ageout.sync_manifest`, which registers who is really
rostered. But `ageout.apply` runs only for those two - AGE_CAPS has no 'pro' key - so PRO never
registered anything and went on churning. Measured on the live panel log, every publish:

    pro: 11 intruder(s) on rosters ... released 11, signed 0, backfilled 11

while prep said "already clean" and college released nobody. Eleven AI players replaced a week,
their stats and continuity thrown away each time, and under Full Finances each replacement paid a
fresh contract.

THE TEST IS CONVERGENCE, not a count. A guard that finds ten strangers today is fine; a guard
that finds ten every single time is broken, and only the second run can tell them apart.

    python tests/test_roster_converges.py
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def run():
    import protect_rosters
    from commissioner import ageout

    print("the guard registers what it signs, for EVERY league")
    src = inspect.getsource(protect_rosters.protect)
    check("it calls sync_manifest", "sync_manifest" in src, True)
    check("and writes the manifest back", "manifest.json" in src, True)
    # ONLY WHEN IT ACTUALLY SIGNED SOMEBODY, and never on a dry run - a dry run that rewrites
    # the manifest is a dry run that changed the universe.
    check("only after a real backfill", "if fills and not dry_run" in src, True)
    # AND IT MUST NOT BE ABLE TO KILL THE GUARD. An unregistered backfill is churn; a roster
    # guard that raises is a league nobody tidied at all.
    check("a failure to register is not fatal",
          "could not register the backfill" in src, True)

    print("registering is what makes a second pass find nothing")
    # sync_manifest is the piece that closes the loop, and it must never touch the two things
    # that are located BY NAME - characters and reserve seats - or a signup loses its seat.
    doc = inspect.getdoc(ageout.sync_manifest) or ""
    check("sync_manifest leaves reserve rows alone", "RESERVE ROWS ARE NEVER TOUCHED" in doc, True)
    src2 = inspect.getsource(ageout.sync_manifest)
    check("and it excludes the keep set", "keep" in src2, True)

    print("the intake draws from free agents, not from the draft pool")
    # `Team < 1` also catches -2, the game's own draft pool: 70 of college's 190 non-rostered
    # records and 65 of prep's. Recycling one into roster filler consumes a draft record for
    # good - and the pro pool is now where our outgoing college seniors are carried.
    intake = inspect.getsource(ageout.apply)
    check("free agents are selected by == -1", 'p.values["Team"] == -1' in intake, True)
    check("and not by the wider < 1, which takes draft records",
          'p.values["Team"] < 1 and p.name not in keep' in intake, False)
    # THE ROSTER GUARD ALREADY HAD THIS RIGHT and the two must not drift apart again.
    check("the roster guard agrees", 'p.values["Team"] == -1' in
          inspect.getsource(protect_rosters.protect), True)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  roster converges: a backfilled body is registered so the next pass finds nothing, "
          "the registration cannot abort the guard or fire on a dry run, and the intake takes "
          "free agents rather than eating the draft pool")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
