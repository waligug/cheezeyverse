"""Declaring for the draft must not quietly cost a character the season he just played.

`_season_bonuses` skipped anybody whose status was not exactly "active". Declaring for the draft
is what SETS the status to "declared", so the one character the function most needed to price -
the man about to be promoted - was the one it walked past. He lost the season bonus for a season
he really played AND the promotion grant computed in the same loop, which is the largest payment
an offseason makes.

THE ASSERTION THIS FILE EXISTS FOR is `test_a_drafted_character_is_paid_his_grant`. A character
reaches pro through the DRAFT and nowhere else, so he lands in `result["drafted"]`, not
`result["promoted"]`. Both the payment gate and the report read `promoted` alone - so the grant
was computed for every drafted character in the universe's history and paid to none of them, with
nothing in the log to say a payment had been skipped.

    python tests/test_declared_is_paid.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import offseason  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def test_a_drafted_character_is_paid_his_grant():
    print("who counts as having moved up")
    result = {
        "promoted": [{"character": {"id": "up"}}],
        "drafted": [{"character": {"id": "pick1"}, "pick": 1},
                    {"character": {"id": "pick2"}, "pick": 2}],
    }
    moved = offseason._moved_up(result)
    check("the prep intake counts", "up" in moved, True)
    # THE BUG: the draft is the only road into pro, and these were invisible to the payment gate.
    check("and so does everybody drafted", {"pick1", "pick2"} <= moved, True)
    check("three people moved", len(moved), 3)


def test_a_pick_that_failed_is_not_paid_for_moving():
    """run_draft records the failure and leaves him where he was. Paying him is the other error."""
    print("a pick that did not land")
    moved = offseason._moved_up({"drafted": [
        {"character": {"id": "ok"}, "pick": 1},
        {"character": {"id": "broken"}, "pick": 2, "error": "no reserve slot"}]})
    check("the one that landed is paid", "ok" in moved, True)
    check("the one that failed is not", "broken" in moved, False)


def test_nothing_odd_in_the_result_raises():
    print("a malformed result")
    for junk in ({}, {"promoted": None, "drafted": None},
                 {"drafted": ["not a dict", None, {}, {"character": None},
                              {"character": {}}]}):
        check(f"{str(junk)[:36]}", offseason._moved_up(junk), set())


def test_a_declared_character_still_earns_his_season():
    """He played the whole season. Declaring is a decision about NEXT season."""
    print("the status filter")
    import inspect
    src = inspect.getsource(offseason._season_bonuses)
    check("declared is not skipped", '("active", "declared")' in src, True)
    check("and it is a membership test, not an equality one",
          'status") != "active"' in src, False)
    # Retired and pending did not play this season and must stay out.
    check("retired is still excluded", '"retired"' in src, False)


def main():
    test_a_drafted_character_is_paid_his_grant()
    test_a_pick_that_failed_is_not_paid_for_moving()
    test_nothing_odd_in_the_result_raises()
    test_a_declared_character_still_earns_his_season()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  declaring costs nothing: a character who declared still earns the season he "
          "played, and a character who reached pro through the draft is paid the promotion "
          "grant that the payment gate could not previously see")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
