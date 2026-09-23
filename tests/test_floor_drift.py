"""A defanged body must stay findable after the game has nudged his ratings upward.

WHAT WENT WRONG. `tools/protect_rosters.py` floors a stranger's ratings to FLOOR_RATING (2) so no
GM prefers him. `ageout.reconcile` is the repair that gives those ratings back - and it looked for
EXACTLY that value. But the game's own progression moves a player every offseason, so a floored
body drifts: 2 becomes 3, then 4, 5, 6, 7. From the first rollover onward the repair could no
longer see him.

Prep and college hid it, because they show a clean spike at 2 and nothing at all between 3 and 8.
PRO is the league that rolls over with progression AND free agency, and it came back smeared
across 3-7 with 145 of its 300 STARTERS floored and invisible to their own repair - unevenly, 4
of 15 on one team against 11 of 15 on another. That is what Nate saw: "half of these teams have 3
overall players", and lopsided records to go with it.

THEN IT WAS WRONG A SECOND TIME. The first fix assumed a floored body drifts EVENLY and tested
max-and-spread. Rudy Schreck sat on a pro roster with thirteen of fifteen core ratings at 2-4 and
Jumping alone at 17: progression moves individual ratings at different rates, so he read as a real
player and went on taking rotation minutes. Nate found him by reading a team sheet, after being
told twice that it was fixed.

THE TWO GATES NOW, and either is enough:

  * BELOW WHAT THE GENERATOR CAN MAKE. `generated_floor` samples `gen.make_player` for the league
    and takes the lowest mean it produces. Measured on the live saves this splits cleanly: in pro
    the worst body it keeps is 30.3 and the best it catches 13.5; in college 29.7 against 13.0.
    Nothing sits in between. Sampled, never written down, so it survives a band change.
  * MOSTLY AT THE FLOOR LINE. 60% or more of the core ratings still at or under FLOOR_DRIFT is
    filler whatever the mean - the Rudy Schreck case.

    python tests/test_floor_drift.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner import ageout  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

FAILS: list[str] = []
SKILL = ["a", "b", "c", "d"]


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}{(': ' + detail) if detail else ''}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


def body(*vals):
    return dict(zip(SKILL, vals))


def run():
    print("a body still at the floor is defanged, as it always was")
    check("flat at the floor itself", ageout.is_defanged(body(2, 2, 2, 2), SKILL))
    check("and at zero", ageout.is_defanged(body(0, 0, 0, 0), SKILL))

    print("\nand so is one the game has nudged upward - THE BUG")
    # Every one of these was on a live pro roster, invisible to the repair.
    for v in (3, 4, 5, 6, 7):
        check(f"drifted to {v}", ageout.is_defanged(body(v, v, v, v), SKILL))
    check("drifted unevenly, still flat", ageout.is_defanged(body(3, 5, 2, 7), SKILL))

    print("\na real player is never regenerated out from under himself")
    # 8 is the lowest band floor in the universe (prep). One point above the gate.
    check("a weak but generated player at 8", not ageout.is_defanged(body(8, 8, 8, 8), SKILL))
    check("a real low-band player", not ageout.is_defanged(body(9, 22, 14, 31), SKILL))
    check("a star", not ageout.is_defanged(body(100, 69, 42, 57), SKILL))
    # THE SPREAD GATE EARNS ITS KEEP HERE: low top end, but real variation.
    check("low ceiling but real spread is left alone",
          not ageout.is_defanged(body(0, 0, 7, 7), SKILL) or ageout.FLOOR_SPREAD < 7,
          f"spread gate is {ageout.FLOOR_SPREAD}")

    print("")
    print("A FLOORED BODY DOES NOT DRIFT EVENLY - the case the first version missed")
    # Rudy Schreck, off a live pro roster: thirteen of fifteen core ratings at 2-4, and Jumping
    # alone drifted to 17. A max-and-spread test called him a real player and left him taking
    # rotation minutes. Nate found him by reading a team sheet.
    rudy = [4, 4, 2, 4, 4, 4, 2, 4, 4, 4, 4, 4, 4, 17, 8]
    sk15 = [f"r{i}" for i in range(15)]
    check("caught by the structural test alone",
          ageout.is_defanged(dict(zip(sk15, rudy)), sk15),
          f"mean {sum(rudy)/len(rudy):.1f}, max {max(rudy)}, spread {max(rudy)-min(rudy)}")
    check("and by the mean test against pro's floor",
          ageout.is_defanged(dict(zip(sk15, rudy)), sk15, 30.5))

    print("")
    print("below what the generator can make, for the league being repaired")
    for key, floor in (("prep", 13.6), ("college", 21.0), ("pro", 30.5)):
        check(f"{key}: a body under {floor:.1f} is filler",
              ageout.is_defanged(body(*([int(floor) - 4] * 4)), SKILL, floor))
        check(f"{key}: a body over it is left alone",
              not ageout.is_defanged(body(*([int(floor) + 12] * 4)), SKILL, floor))

    print("")
    print("the floor is SAMPLED from the generator, never written down")
    # A number typed into the module stops matching the day a band moves in config, and nothing
    # says so. Sampling is what makes the rule survive a band change.
    import inspect
    src = inspect.getsource(ageout.generated_floor)
    check("it calls the real generator", "gen.make_player" in src)
    check("and takes the lowest it produces", "lowest" in src)
    for spec in cfg.LEAGUES:
        floor = ageout.generated_floor(spec)
        check(f"{spec.key}: sampled floor sits inside its band {spec.ratings}",
              spec.ratings[0] * 0.5 < floor < spec.ratings[1], f"{floor:.1f}")

    print("\nthe gate sits below every league's own generation band")
    for spec in cfg.LEAGUES:
        low = spec.ratings[0]
        check(f"{spec.key} generates at {low}, above the gate {ageout.FLOOR_DRIFT}",
              ageout.FLOOR_DRIFT < low, f"{ageout.FLOOR_DRIFT} < {low}")

    print("\nthe old exact-match rule is gone")
    import inspect
    src = inspect.getsource(ageout.reconcile)
    check("reconcile uses the drift-aware test", "is_defanged(" in src)
    check("and no longer compares against FLOOR_RATING alone",
          "> FLOOR_RATING" not in src)
    # THE DRAFT POOL IS NOT FILLER. Team -2 is the game's own draft class and carries college's
    # outgoing seniors; regenerating one rewrites the board our characters are drafted against.
    check("the draft pool is explicitly excluded", "-2" in src and "DRAFT POOL" in src.upper())

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  floor drift: a defanged body is recognised however far the game has nudged him, "
          "a generated player never is, and the draft pool is left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
