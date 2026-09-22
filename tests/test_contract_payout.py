"""A pro is paid once a season for what the GAME thinks he is worth.

Pro's economy was flat - 3 a week and a 15-point lump, the same for the man who won the title and
the man who never dressed. FBPB3 has carried seven years of salary per player the whole time.
This is that signal finally being spent: the yearly value of his contract, banded, REPLACING the
flat lump for pro alone.

THE ASSERTION THIS FILE EXISTS FOR is `test_finances_off_pays_everybody_the_same`. With Finances
off every contract in the universe is a token $1,000,000 - one distinct value across 955 players.
A payout that ranked that would declare all of them stars, or all of them minimum earners,
depending on which way the comparison fell. There is no scale yet, and the honest answer is the
floor for everyone until the game says otherwise.

`test_a_bare_row_still_gets_paid` is the other one worth keeping: store.grant_points REFUSES an
amount of zero, so a band that could return 0 would raise in the middle of an offseason that has
already promoted and drafted everybody.

WHY PERCENTILES AND NOT DOLLARS. A literal "a million is a point" pays a minimum earner one point
a year against the fifteen he gets today and a max player fifty - 25-50x, where the whole design
asks for 3-4x. And a dollar threshold rots: the cap inflates every season and the bands would not.

    python tests/test_contract_payout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commissioner import characters as ch  # noqa: E402
from commissioner import points  # noqa: E402
from commissioner.universe import generate  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


# A league that looks like a real one: a long tail of minimum men and a few very large deals.
LEAGUE = ([1_000_000] * 40 + [2_500_000] * 20 + [6_000_000] * 12
          + [15_000_000] * 6 + [30_000_000] * 3 + [45_000_000])


def test_the_spread_is_what_was_asked_for():
    """3-4x between the best-paid and the worst. Not 1.5x, and emphatically not 25x."""
    print("the spread")
    bands = [pts for _pct, pts in points.PAYOUT_BANDS]
    check("bands rise", bands == sorted(bands), True)
    check("top over bottom", round(bands[-1] / bands[0], 1), 3.6)
    check("within 3-4x", 3.0 <= bands[-1] / bands[0] <= 4.0, True)


def test_a_real_distribution_separates_people():
    """The whole point: the man on a max deal must not be paid like the man on a minimum."""
    print("a real salary distribution")
    b = points.salary_distribution(LEAGUE)
    check("there is a scale", b is not None, True)
    low = points.annual_payout(1_000_000, b)
    high = points.annual_payout(45_000_000, b)
    print(f"        minimum {low}   max {high}")
    check("the max earner is paid more", high > low, True)
    check("by 3-4x", 3.0 <= high / low <= 4.0, True)
    # and the middle really is in the middle
    mid = points.annual_payout(6_000_000, b)
    check("a middling salary lands between", low <= mid <= high, True)


def test_finances_off_pays_everybody_the_same():
    """One distinct salary across the league is not a ranking. It is Finances being off."""
    print("no salary scale yet")
    check("no distribution from one value", points.salary_distribution([1_000_000] * 300), None)
    check("no distribution from nothing", points.salary_distribution([]), None)
    check("no distribution from zeros", points.salary_distribution([0, 0, 0]), None)
    everybody = {points.annual_payout(1_000_000, None) for _ in range(5)}
    check("everybody gets one figure", len(everybody), 1)
    check("and it is the floor", everybody.pop(), points.PAYOUT_FLOOR)


def test_a_bare_row_still_gets_paid():
    """grant_points refuses an amount of 0, so no band may ever return one."""
    print("a character with no contract")
    b = points.salary_distribution(LEAGUE)
    for salary in (0, None, "", "junk"):
        check(f"salary {salary!r} pays the floor", points.annual_payout(salary, b),
              points.PAYOUT_FLOOR)
    check("no band is zero", all(pts > 0 for _pct, pts in points.PAYOUT_BANDS), True)


def test_the_boundaries_move_with_the_league():
    """A percentile that stopped moving would be a dollar threshold wearing a disguise.

    The cap inflates every season. If the same salary keeps paying the same band while everyone
    around him doubles, the payout has quietly become a fixed number and stopped meaning "how
    good is he, here, now".
    """
    print("the bands follow the league")
    poor = points.salary_distribution([1_000_000] * 10 + [2_000_000] * 5 + [4_000_000])
    rich = points.salary_distribution([10_000_000] * 10 + [20_000_000] * 5 + [40_000_000])
    check("a 4M deal is a star in a poor league", points.annual_payout(4_000_000, poor),
          points.PAYOUT_BANDS[-1][1])
    check("and the minimum in a rich one", points.annual_payout(4_000_000, rich),
          points.PAYOUT_BANDS[0][1])


def test_the_ledger_line_says_why():
    """A number with no explanation in somebody's history is what the per-component rule avoids."""
    print("the ledger reason")
    b = points.salary_distribution(LEAGUE)
    top = points.payout_reason(45_000_000, points.annual_payout(45_000_000, b), b)
    bottom = points.payout_reason(1_000_000, points.annual_payout(1_000_000, b), b)
    print(f"        {top}")
    print(f"        {bottom}")
    check("names the money", "45,000,000" in top, True)
    check("names the band", "star" in top and "minimum" in bottom, True)
    check("says so when there is no scale",
          "no salary scale" in points.payout_reason(0, points.PAYOUT_FLOOR, None), True)


def test_a_stamped_character_never_lands_on_a_bare_row():
    """Eleven of pro's sixty reserve seats have no contract, and a seat is where a draftee goes.

    Under Full Finances the game releases a contract-less player when the league loads, so a
    character could be drafted, placed, announced, and be a free agent by the next Sim Week with
    nothing saying why. The token the game's own import path uses is the filler, and the two
    values must stay the same number.
    """
    print("the value a stamped row is given")
    check("characters matches generate", ch.IMPORT_CONTRACT, generate.IMPORT_CONTRACT)
    check("and it is a real contract", ch.IMPORT_CONTRACT > 0, True)


def main():
    test_the_spread_is_what_was_asked_for()
    test_a_real_distribution_separates_people()
    test_finances_off_pays_everybody_the_same()
    test_a_bare_row_still_gets_paid()
    test_the_boundaries_move_with_the_league()
    test_the_ledger_line_says_why()
    test_a_stamped_character_never_lands_on_a_bare_row()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  contract payout: a pro is paid by where his salary sits among his own league, "
          "3.6x from minimum to max, the bands move as the league's money moves, a league with "
          "no salary scale pays everybody the floor rather than inventing a ranking, and nothing "
          "can ever pay zero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
