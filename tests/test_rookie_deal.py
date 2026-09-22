"""A drafted man is paid what his deal says, and the ledger says why.

Weekly income has always been one number per LEVEL - 1 in prep, 2 in college, 3 in the pros -
because `grant_week_points` is a single RPC that pays everybody in a league the same amount. A
contract is the first thing that pays one character differently from the man next to him.

WHY A TOP-UP RATHER THAN A RATE. Paying per character means changing that RPC, and a deploy that
lands before its migration does not pay the new rate: it fails the points step of every Sim Week
after all the basketball has been played. `_grant_one_week` already carries a fallback for exactly
that hazard. So the league rate is paid as it always was and the difference is granted separately
with its own ledger row - no migration, nobody paid twice, and the row explains itself.

The shape of the rookie scale is the economy's whole argument: the draft order is REVERSE
standings, so a high pick goes to a bad team. Money and minutes arrive together, which is what
makes a late pick on a contender a real choice rather than a consolation prize.

    python tests/test_rookie_deal.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import points  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


PRO = {"points_per_week_prep": 1, "points_per_week_college": 2, "points_per_week_pro": 3}


def run():
    print("the rookie scale rewards a high pick without deciding a career")
    rates = [points.rookie_rate(p) for p in (1, 2, 3, 4, 8, 9, 20, 21, 60)]
    check("it never increases with the pick number", rates == sorted(rates, reverse=True), True)
    check("first overall is the best deal", points.rookie_rate(1), max(rates))
    check("the last man in still beats college",
          points.rookie_rate(60) > PRO["points_per_week_college"], True)
    check("and nobody is paid more than double the league rate",
          points.rookie_rate(1) <= PRO["points_per_week_pro"] * 2, True)

    print("an unknown or nonsense pick is paid the floor, never zero")
    for bad in (None, "x", 0, -4, ""):
        check(f"pick {bad!r}", points.rookie_rate(bad), points.ROOKIE_FLOOR)

    print("a contract overrides the level rate, in both directions")
    check("no contract is the league rate", points.per_week("pro", PRO), 3)
    check("a big deal pays more", points.per_week("pro", PRO, {"rate": 6}), 6)
    check("a small deal pays less", points.per_week("pro", PRO, {"rate": 1}), 1)
    # A rate of 0 is a real deal that pays nothing, not a missing value - so it must survive
    # rather than falling through to the level rate.
    check("a deal worth nothing is honoured", points.per_week("pro", PRO, {"rate": 0}), 0)
    check("an unreadable rate falls back to the league",
          points.per_week("pro", PRO, {"rate": "lots"}), 3)
    check("so does a contract with no rate at all", points.per_week("pro", PRO, {}), 3)

    print("the ledger line says a contract paid it")
    line = points.reason("pro", 6, contract={"rate": 6, "team": "LCH"})
    check("it names the contract", "contract" in line, True)
    check("and the team", "LCH" in line, True)
    check("a plain week is unchanged", points.reason("prep", 1), "week simmed")
    check("a level rate still says the level", points.reason("pro", 3), "week simmed (pro x3)")

    print("only the difference is topped up, and only upward")
    people = [
        {"id": "big", "first_name": "Big", "last_name": "", "contract": {"rate": 6, "team": "LCH"}},
        {"id": "same", "first_name": "Same", "last_name": "", "contract": {"rate": 3}},
        {"id": "small", "first_name": "Small", "last_name": "", "contract": {"rate": 1}},
        {"id": "none", "first_name": "None", "last_name": ""},
    ]
    tops = points.contract_topups(people, "pro", PRO, weeks=1)
    check("only the big deal is topped up", [c["id"] for c, _, _ in tops], ["big"])
    check("by the difference, not the whole rate", tops[0][1], 3)
    check("the row explains itself", "contract top-up" in tops[0][2], True)

    print("and it scales with the weeks actually simmed")
    tops = points.contract_topups(people, "pro", PRO, weeks=4)
    check("four weeks pays four times the gap", tops[0][1], 12)

    print("a small deal is never clawed back")
    check("nobody is charged", all(extra > 0 for _, extra, _ in
                                   points.contract_topups(people, "pro", PRO, 4)), True)

    print("a contract in a cheaper league still only pays the difference")
    tops = points.contract_topups(
        [{"id": "x", "first_name": "X", "last_name": "", "contract": {"rate": 6}}],
        "college", PRO, weeks=1)
    check("college base is 2, so the gap is 4", tops[0][1], 4)

    print("no characters, no contracts, no crash")
    check("empty list", points.contract_topups([], "pro", PRO, 1), [])
    check("None", points.contract_topups(None, "pro", PRO, 1), [])


    print("the deal is found on the level he is playing at, not on a column")
    # It has to live there: `contract` is not in the store's SETTABLE_FIELDS and is not a column,
    # so a direct write raises and the deal would silently never exist. level_history is jsonb,
    # is already written by promote, and is read back.
    drafted = {"league": "pro", "level_history": [
        {"level": "college", "to_season": 2030, "contract": {"rate": 9}},
        {"level": "pro", "to_season": None, "contract": {"rate": 6, "team": "LCH"}}]}
    check("his open pro deal", points.current_contract(drafted), {"rate": 6, "team": "LCH"})
    check("and it pays", points.per_week("pro", PRO, points.current_contract(drafted)), 6)

    print("a deal he has finished with never keeps paying him")
    done = {"league": "pro", "level_history": [
        {"level": "college", "to_season": 2030, "contract": {"rate": 9}},
        {"level": "pro", "to_season": None}]}
    check("the closed college deal is ignored", points.current_contract(done), None)
    check("so he is paid the league rate",
          points.per_week("pro", PRO, points.current_contract(done)), 3)

    print("and a deal from a level he no longer plays at does not follow him")
    left = {"league": "pro", "level_history": [
        {"level": "college", "to_season": None, "contract": {"rate": 9}}]}
    check("wrong level is not his deal", points.current_contract(left), None)

    print("nothing to read is not an error")
    for blank in ({"league": "pro"}, {}, {"league": "pro", "level_history": []},
                  {"league": "pro", "level_history": [None, "junk"]}):
        check(f"{str(blank)[:34]}", points.current_contract(blank), None)
    check("a character that is not a mapping", points.current_contract(None), None)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  rookie deal: the scale rewards a high pick without runaway pay, a contract "
          "overrides the level rate, and only the upward difference is granted")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
