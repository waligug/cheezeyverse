"""How many skill points a simmed week is worth, which depends on what level you play at.

WHY IT IS NOT A FLAT ONE
The cost curve charges by rating: 1 point a step under 50, 2 from 50-69, 3 from 70-84, 5 from
85 up. Characters sit roughly in those bands as they climb - prep in the first, college in the
second, the pros in the third - so a flat income means every promotion quietly halves what a
season is worth. `tools/progression_model.mjs` puts a character in the pros at about a 64 core
average, where a season's ~41 points buys barely +2 per core skill against about +7 in prep.
Paying 1/2/3 per week by level keeps a season worth roughly the same number of upgrades for a
whole career, which is what stops the back half of a career feeling like standing still.

WHY THE RULE LIVES HERE AND NOT IN SQL
The price curve is duplicated into `supabase/schema.sql` on purpose, because players submit
upgrade requests and the browser must not be able to name its own price. Weekly income is the
opposite: only the commissioner ever grants it, under the service key, so there is nothing to
defend against and no reason to pay for a second implementation. `grant_week_points` takes the
resolved rate as an argument instead, and this module is the only place that decides it - the
same reason `test_price_parity` exists is the reason this one is not duplicated.
"""
from __future__ import annotations

DEFAULT_PER_WEEK = 1

# A rookie deal, in points per simmed week, by where a man was taken. The pros pay 3 flat, so
# these are deliberately AROUND that number rather than far above it: the first pick earns a
# meaningful premium, the last man in still earns more than he did in college, and nobody is
# paid so much that a draft slot decides a career on its own.
#
# The shape is the trade this economy is built on. A high pick goes to a BAD team - the order is
# reverse standings - so the money and the minutes arrive together, which is what really happens
# and is what makes a late pick on a contender a genuine choice rather than a consolation.
# CUT HARD ON 2026-09-24 (Nate: "Should not be getting 100 points early ... reduce it a lot for everyone"). 
# Was ((1, 6), (3, 5), (8, 4), (20, 3)) / floor 3, against a pro rate of 3: a #1 pick earned 6 a
# week, twice the level, and the draft class sat on 100-300 points within one season.
ROOKIE_SCALE = ((3, 3), (20, 2))
ROOKIE_FLOOR = 2


# ---- the yearly contract payout ---------------------------------------------------------------
# A pro character is paid once a season for what the GAME thinks he is worth, and that payment
# replaces the flat offseason lump for his level. This is a different thing from ROOKIE_SCALE
# above: that is a weekly rate the commissioner assigns for where he was DRAFTED, this is an
# annual one read from the salary FBPB3 itself gave him. Both exist, deliberately.
#
# BANDED BY PERCENTILE, NOT BY DOLLARS. A literal "a million is a point" sounds right and is not:
# on any realistic scale it pays a minimum earner about one point a year against the fifteen he
# gets today, and hands a max player fifty - a 25-50x spread where Nate asked for 3-4x. And an
# absolute threshold rots, because a salary cap inflates every season while the bands would not.
# Where he sits among his own league is the durable question.
#
# The values are here and NOT in the settings table: a stale settings row silently overrides a
# changed default, which is exactly how college_development_bonus sat at 12 while the code said
# 20. These are meant to be edited and diffed.
# CUT HARD ON 2026-09-24 (Nate: "Should not be getting 100 points early ... reduce it a lot for everyone"). Was 10/17/26/36.
PAYOUT_BANDS = ((0.50, 3), (0.75, 5), (0.90, 8), (1.01, 12))
PAYOUT_FLOOR = 3           # a rostered man with no contract still gets the bottom band, because
                           # grant_points refuses an amount of 0 and would raise mid-offseason


def salary_distribution(salaries):
    """The band boundaries for one league, from its OWN rostered salaries this season.

    Recomputed every time rather than stored: the point of a percentile is that it moves with the
    league, and a boundary frozen in a settings row would quietly stop meaning what it says the
    first time the cap rises.

    Returns the salary at each band's upper edge, or None when there is nothing to rank - one
    league-wide salary, or none at all, is what Finances-off looks like and it must not be read
    as "everybody is a star".
    """
    live = sorted(int(s) for s in salaries if s and int(s) > 0)
    if len(set(live)) < 2:
        return None
    edges = [live[min(len(live) - 1, int(round(pct * (len(live) - 1))))]
             for pct, _pts in PAYOUT_BANDS]
    # AND REFUSE WHEN THE EDGES CANNOT TELL ANYONE APART, which the distinct-count test above
    # does not catch. "Every salary identical" is the obvious face of Finances-off; "300 of 301
    # identical" is the same absent market wearing two values, and it ranks catastrophically.
    # Measured on the live pro league at the moment a drafted character is first paid, 300 men
    # on the $1,000,000 import token plus one $5,000,000 rookie deal gave edges of
    # [1M, 1M, 1M, 5M] - so that rookie scored the 100th percentile and 36 points, the STAR
    # band, for a salary worth 17 once the league actually pays people. More than double, for
    # being the only man in it with a real contract.
    #
    # The test is on the OUTPUT rather than on a share of the input, because "most of the league
    # earns the minimum" is a real and common shape - a league of [1M x10, 2M x5, 4M] is poor,
    # not broken, and its edges still separate three tiers. What is broken is every band below
    # the top sharing one edge: that is not a ranking, it is one outlier and a flat line.
    if len(set(edges[:-1])) < 2:
        return None
    return edges


def annual_payout(salary, boundaries):
    """Points for one man's yearly salary. `boundaries` is what salary_distribution returned.

    No distribution - Finances off, or a league where everyone earns the same - pays the floor to
    everybody, which is the honest answer: the game is not yet saying anyone is worth more.
    """
    try:
        salary = int(salary or 0)
    except (TypeError, ValueError):
        salary = 0
    if not boundaries or salary <= 0:
        return PAYOUT_FLOOR
    for edge, points in zip(boundaries, (pts for _pct, pts in PAYOUT_BANDS)):
        if salary <= edge:
            return points
    return PAYOUT_BANDS[-1][1]


# What each band is CALLED, in the same order as PAYOUT_BANDS. A module constant rather than a
# local tuple because the website shows the label beside the money, and a second copy of these
# four words would drift from the arithmetic the moment somebody renamed one.
BAND_LABELS = ("league minimum", "rotation", "starter", "star")


def payout_band(salary, boundaries):
    """Which band this salary falls in, or None when the league has no salary scale yet.

    None is a real answer and not a failure: with Finances off every contract is the same token,
    `salary_distribution` refuses to rank one distinct value, and calling everybody a star (or
    everybody a minimum earner) would be an invention. The site reads None and says there is no
    scale rather than showing a label nothing earned.
    """
    # COERCED THE SAME WAY `annual_payout` COERCES, and that is the whole point of this block.
    # The first version tested `if salary and ...`, so a salary of 0 matched no edge, fell out of
    # the loop and was labelled with the LAST band - a contract-less player shown as a "star" on
    # his own page while annual_payout quietly paid him the floor. The label and the money have
    # to come apart nowhere.
    try:
        salary = int(salary or 0)
    except (TypeError, ValueError):
        salary = 0
    if not boundaries:
        return None
    if salary <= 0:
        return BAND_LABELS[0]
    for edge, label in zip(boundaries, BAND_LABELS):
        if salary <= edge:
            return label
    return BAND_LABELS[-1]


def payout_reason(salary, points, boundaries):
    """The ledger line. It names the money AND the band, because a number with no explanation in
    somebody's history is the thing the one-row-per-component rule exists to prevent."""
    # COERCED, BECAUSE THIS RUNS INSIDE THE OFFSEASON. A salary of None reached int() and raised
    # TypeError - in the middle of building a ledger row, after the league has already been
    # promoted, drafted and paid. Eleven of pro's sixty reserve seats carry no contract and a
    # bare row reads as exactly this, so it was reachable.
    try:
        salary = int(salary or 0)
    except (TypeError, ValueError):
        salary = 0
    if not boundaries:
        return f"contract payout ({points}, no salary scale yet)"
    return (f"contract payout: {payout_band(salary, boundaries)}, "
            f"${salary:,} a year")


def rookie_rate(pick):
    """Points per week for the man taken at `pick`. Undrafted or unknown pays the floor."""
    try:
        pick = int(pick)
    except (TypeError, ValueError):
        return ROOKIE_FLOOR
    if pick < 1:
        return ROOKIE_FLOOR
    for last, rate in ROOKIE_SCALE:
        if pick <= last:
            return rate
    return ROOKIE_FLOOR


def per_week(league, settings=None, contract=None):
    """Points per simmed week, falling back league rate -> flat rate -> 1.

    `contract` is a character's deal, if he has one: `{"rate": n, ...}`. A contract OVERRIDES the
    level rate, because that is the whole point of having one - a man who signed for more is paid
    more than the league's going rate, and a man who signed for less is paid less. A rate of 0 is
    honoured (it means a deal that pays nothing), which is why this tests for None rather than
    truthiness; anything unparseable is ignored and the level rate stands.

    Unset keys mean "no change": until somebody adds points_per_week_pro, the pros are paid
    whatever points_per_week says, exactly as before. That is deliberate - the keys can be
    added to a live universe without a migration, and removed again if the rates are wrong.
    """
    if contract:
        try:
            rate = int(contract.get("rate"))
        except (TypeError, ValueError, AttributeError):
            rate = None
        if rate is not None and rate >= 0:
            return rate
    s = settings or {}
    for key in (f"points_per_week_{league}" if league else None, "points_per_week"):
        if not key or key not in s:
            continue
        try:
            value = int(s[key])
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return DEFAULT_PER_WEEK


def reason(league, rate, base="week simmed", contract=None):
    """The ledger line. Says the rate whenever it is not the plain one.

    A friend who gets promoted sees his income triple with no explanation otherwise, and the
    ledger is the only place the universe ever explains itself to him. A contract says so by
    name, because "pro x3" and "pro x6" are the same sentence to somebody who does not know he
    signed a bigger deal than the man next to him.
    """
    if contract:
        team = (contract.get("team") or "").strip() if hasattr(contract, "get") else ""
        return f"{base} (contract x{rate}{', ' + team if team else ''})"
    if rate == DEFAULT_PER_WEEK or not league:
        return base
    return f"{base} ({league} x{rate})"


def contract_expired(deal, season):
    """Has this deal run out by `season`? Unknown season or unknown term means no.

    A deal signed in season S for Y years covers S through S+Y-1 and is over at S+Y. ROOKIE_YEARS
    was written into every rookie deal from the first draft and NOTHING EVER COUNTED IT DOWN, so
    a first-overall pick kept his 6-a-week rate for the rest of his career - the reward for being
    drafted high quietly became permanent.

    Missing or unreadable fields mean "cannot tell", and cannot-tell keeps paying him. The
    alternative is silently stopping somebody's income on a field nobody has checked, and a
    contract that pays too long is a smaller wrong than one that stops without warning.
    """
    if not hasattr(deal, "get") or season is None:
        return False
    try:
        start = int(deal["season_from"])
        years = int(deal["years"])
    except (KeyError, TypeError, ValueError):
        return False
    if years <= 0:
        return False
    return int(season) >= start + years


def current_contract(character, season=None):
    """The deal a character is playing under right now, or None.

    `season` expires it. Without one nothing expires, which is what every caller did before
    expiry existed and is still right for a dry run that only wants to see the terms.

    IT LIVES ON THE LEVEL, not on a column. `contract` is not in the store's SETTABLE_FIELDS and
    is not a column on `characters`, so a direct write raises; `level_history` is jsonb, is
    already written by `promote`, and is read back. That is also the truthful place for it - a
    deal belongs to the level he signed at, so it ends when that level ends.

    The open entry is the one with no `to_season`. A character who has moved on carries his old
    deals in his history, and they must not keep paying him.
    """
    if not hasattr(character, "get"):
        return None
    # Direct field first, so a store that ever does grow a column keeps working without a change
    # here, and so a caller can hand in a deal for a dry run.
    direct = character.get("contract")
    if direct:
        return direct
    league = character.get("league")
    for entry in reversed(list(character.get("level_history") or [])):
        if not isinstance(entry, dict) or entry.get("to_season") is not None:
            continue
        if league and entry.get("level") != league:
            continue
        deal = entry.get("contract")
        if deal and not contract_expired(deal, season):
            return deal
    return None


def contract_topups(characters, league, settings, weeks, season=None):
    """[(character, extra points, ledger line)] for anybody whose deal beats the league rate.

    WHY A TOP-UP AND NOT A RATE. `grant_week_points` is one RPC that pays every character in a
    league the SAME number, because until now that was true. Paying per character would mean a
    schema migration, and a deploy that lands before its migration does not pay the new rate - it
    fails the points step of every Sim Week after all the basketball has been played, which is
    exactly the failure `_grant_one_week` already carries a fallback for.

    So the league rate is paid as it always was, and the difference is granted separately with its
    own ledger row. Nobody is paid twice, the row says what it is for, and no database has to
    change for a contract to start paying.

    Only the difference, and only upward. A deal BELOW the league rate is left alone rather than
    clawed back: `grant_points` takes negatives, but taking points off somebody for signing a
    small contract is a punishment nobody agreed to, and the free-agency design pays for a cheap
    deal in minutes rather than in points.
    """
    weeks = max(1, int(weeks or 1))
    base = per_week(league, settings)
    if season is None:
        # The season the top-up is being paid FOR. Without it a rookie deal never expires, which
        # is the bug this argument exists to close, so read it from the settings the caller
        # already has rather than quietly paying forever.
        try:
            season = int((settings or {}).get("current_season"))
        except (TypeError, ValueError):
            season = None
    out = []
    for c in characters or []:
        contract = current_contract(c, season)
        if not contract:
            continue
        rate = per_week(league, settings, contract)
        extra = (rate - base) * weeks
        if extra <= 0:
            continue
        team = (contract.get("team") or "").strip()
        out.append((c, extra, f"contract top-up ({rate} a week{', ' + team if team else ''})"))
    return out
