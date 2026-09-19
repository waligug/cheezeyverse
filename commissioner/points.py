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


def per_week(league, settings=None):
    """Points per simmed week for `league`, falling back to the flat rate then to 1.

    Unset keys mean "no change": until somebody adds points_per_week_pro, the pros are paid
    whatever points_per_week says, exactly as before. That is deliberate - the keys can be
    added to a live universe without a migration, and removed again if the rates are wrong.
    """
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


def reason(league, rate, base="week simmed"):
    """The ledger line. Says the rate whenever it is not the plain one.

    A friend who gets promoted sees his income triple with no explanation otherwise, and the
    ledger is the only place the universe ever explains itself to him.
    """
    if rate == DEFAULT_PER_WEEK or not league:
        return base
    return f"{base} ({league} x{rate})"
