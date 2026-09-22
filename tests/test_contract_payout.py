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


# WHAT FBPB3 ACTUALLY PAYS, measured rather than imagined. `LEAGUE` above is a made-up shape that
# looks like a real one; this is the real one, read off CV_FinTest after a season and a rollover
# with Full Finances on - 308 rostered men, 84 distinct salaries. Printed by
# `python tools/salary_report.py CV_FinTest`.
#
# Kept as deciles rather than 308 literals because the decile profile IS the thing being asserted:
# the league is brutally bottom-heavy - 70% of it is under $1.9M - and then jumps five-fold into a
# short tail of max deals. A payout curve that behaves on an evenly spread invented league and
# not on this one is a curve that has never been tested.
MEASURED = [482_464, 482_464, 482_464, 818_920, 1_085_545, 1_460_090,
            1_510_876, 1_813_051, 9_201_974, 15_870_542, 22_218_759]


def measured_league():
    """308 salaries interpolated from the measured deciles, so the shape is the real shape."""
    out = []
    for i in range(len(MEASURED) - 1):
        lo, hi = MEASURED[i], MEASURED[i + 1]
        for k in range(31):                      # ~31 men per decile, 310 all told
            out.append(int(lo + (hi - lo) * k / 31))
    return out


def test_the_bands_behave_on_the_league_the_game_really_built():
    """The distribution is bottom-heavy and long-tailed. The curve has to survive that shape."""
    print("the measured pro league")
    league = measured_league()
    b = points.salary_distribution(league)
    low = points.annual_payout(MEASURED[0], b)
    high = points.annual_payout(MEASURED[-1], b)
    print(f"        $482,464 -> {low}    $22,218,759 -> {high}")
    check("the minimum earner is on the floor", low, points.PAYOUT_FLOOR)
    check("the max earner is on the top band", high, points.PAYOUT_BANDS[-1][1])
    check("still 3-4x on real money", 3.0 <= high / low <= 4.0, True)

    # THE CONSEQUENCE WORTH STATING OUT LOUD: the first band is "below the median", so by
    # construction half the league is on the floor. That is the design - a payout keyed to where
    # a man sits among his own peers pays the bottom half the bottom rate - but it is the first
    # thing somebody will call a bug, so it is asserted rather than left to be discovered.
    # Deliberately p40 and not the median itself: the median IS the first boundary, so which
    # side of it that one salary falls on is a rounding question, not a design one, and pinning
    # a test to it would make the band edges untouchable.
    check("a below-median earner is paid the floor",
          points.annual_payout(MEASURED[4], b), points.PAYOUT_FLOOR)
    paid = [points.annual_payout(s, b) for s in league]
    check("and that really is about half of them",
          0.4 <= paid.count(points.PAYOUT_FLOOR) / len(paid) <= 0.6, True)
    check("every band is actually used", len(set(paid)), len(points.PAYOUT_BANDS))
    check("nobody is paid nothing", min(paid) > 0, True)


def test_a_character_is_never_signed_to_a_one_year_deal():
    """One year expires at the next rollover's FREE AGENCY, in the same offseason it was signed.

    Only the DRAFT ever stated a term. The prep->college promotion, the website signup and the
    rehearsal all left `contract_years` unset, which `stamp_character` reads as "fill a bare row
    with one year" - and all seven characters are in college on exactly that deal right now.

    The constants are re-typed in characters.py because offseason imports it, so this is the pin
    that stops the two copies drifting.
    """
    print("the term a character's deal runs for")
    from commissioner import offseason
    check("every level is covered", sorted(ch.LEVEL_CONTRACT_YEARS), ["college", "prep", "pro"])
    # ONE YEAR ON PURPOSE. Free agency is the only thing that ever prices a man at what he is
    # worth - the measured spread is $482K to $22.2M - so a real character expires with everyone
    # else and gets paid on his merits. A multi-year deal at the $1,000,000 import token is not
    # protection, it is being locked out of that pricing once a year for as long as it runs.
    check("a real character is never locked in",
          set(ch.LEVEL_CONTRACT_YEARS.values()), {1})
    # But NEVER zero: an empty block is the release-on-load case, which is a different failure.
    check("and never left with an empty block",
          all(y >= 1 for y in ch.LEVEL_CONTRACT_YEARS.values()), True)
    # The draft is the exception - a rookie term keeps him on the team that picked him.
    check("the draft still states a longer term of its own",
          offseason.ROOKIE_YEARS > ch.LEVEL_CONTRACT_YEARS["pro"], True)
    # An AI body the roster guard signs wants the opposite treatment and has its own number.
    from commissioner.codec import league_dat as _lg
    check("an AI backfill is not on the character rule",
          _lg.SIGNING_YEARS > ch.LEVEL_CONTRACT_YEARS["pro"], True)
    # A term longer than the file can hold would be clipped silently.
    from commissioner.codec import league_dat as lg
    check("all of them fit in the contract block",
          all(y <= lg.CONTRACT_YEARS for y in ch.LEVEL_CONTRACT_YEARS.values()), True)

    # promote() must state a term even when no deal is passed - that is the college path.
    import inspect
    src = inspect.getsource(offseason.promote)
    check("promote falls back to the level's own term",
          "LEVEL_CONTRACT_YEARS" in src, True)
    check("and the prep->college promotion states one",
          "contract_years=ch.LEVEL_CONTRACT_YEARS[\"college\"]" in
          inspect.getsource(offseason._run_offseason), True)


def test_the_label_and_the_money_never_come_apart():
    """The website shows the BAND; the ledger pays the POINTS. They must describe the same man.

    `payout_band` first read `if salary and int(salary) <= edge`, so a salary of 0 matched no
    edge, fell out of the loop and was labelled with the LAST band. A contract-less player - and
    there are 45 of them on pro rosters today - would have read "star" on his own career page
    while `annual_payout` quietly paid him the floor. The same slip was already in
    `payout_reason`, which would have written "star, $0 a year" into a permanent ledger row.
    """
    print("the band agrees with the payment")
    b = points.salary_distribution(LEAGUE)
    for odd in (0, None, "", "junk", -5):
        check(f"{odd!r} is the bottom band, not the top",
              points.payout_band(odd, b), points.BAND_LABELS[0])
        check(f"{odd!r} is paid the floor", points.annual_payout(odd, b), points.PAYOUT_FLOOR)
        check(f"{odd!r} is not called a star in the ledger either",
              "star" in points.payout_reason(odd, points.PAYOUT_FLOOR, b), False)
    # And across the real league, the man on the top band is the man paid the top points.
    for salary in measured_league():
        top_label = points.payout_band(salary, b) == points.BAND_LABELS[-1]
        top_money = points.annual_payout(salary, b) == points.PAYOUT_BANDS[-1][1]
        if top_label != top_money:
            check(f"${salary:,} label and money disagree", top_label, top_money)
            break
    else:
        check("every salary in the measured league agrees", True, True)
    check("no band label without a scale", points.payout_band(5_000_000, None), None)
    check("one label per band", len(points.BAND_LABELS), len(points.PAYOUT_BANDS))


def main():
    test_the_spread_is_what_was_asked_for()
    test_a_real_distribution_separates_people()
    test_finances_off_pays_everybody_the_same()
    test_a_bare_row_still_gets_paid()
    test_the_boundaries_move_with_the_league()
    test_the_ledger_line_says_why()
    test_a_stamped_character_never_lands_on_a_bare_row()
    test_the_bands_behave_on_the_league_the_game_really_built()
    test_a_character_is_never_signed_to_a_one_year_deal()
    test_the_label_and_the_money_never_come_apart()
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
