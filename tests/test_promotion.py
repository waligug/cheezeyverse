"""Moving up a level: when it happens, what it costs, and what it pays.

Four rules changed together, and they only make sense together:

  PREP_LAST_AGE 18 -> 17     he is promoted after the season he turns 17, so prep is 14-17
  college chop 0.97 -> 1.00  moving up no longer takes 3% of every rating
  promotion_grant            paid instead, scaled to the season he is LEAVING
  bonus cap per level        10 prep, 15 college, 20 pro

THE ASSERTION THIS FILE EXISTS FOR is `test_the_grant_is_earned_not_flat`. A flat promotion
payment would hand the same sum to the character who won prep and the character who never
dressed, which is the opposite of the universe's own rule that minutes and points are earned.
The grant has to come out uneven or it is not doing its job.

`test_prep_last_age_is_not_the_filler_cap` is the one that guards against a repeat of how this
broke in the first place: PREP_LAST_AGE was 17 by design and was raised to 18 as a side effect
of giving the AI population an age ladder, which silently pushed every character's promotion
back a whole season. The two numbers are independent and must stay that way.

The export is written by this file. The real one needs a finished season, and the whole point of
these numbers is that they are checkable before one exists.

    python tests/test_promotion.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commissioner import offseason, seasonbonus  # noqa: E402
from commissioner import ageout  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def character(first, last, birth_year, league="prep", **kw):
    return {"id": f"{first}-{last}", "first_name": first, "last_name": last,
            "status": "active", "league": league,
            "game_dob": f"6/1/{birth_year}", **kw}


# --- the export ------------------------------------------------------------------------------
# Only the pages the bonus and the grant read. Standings give the team records, the leaders page
# the category totals, playoffs the bracket. Entities are the numeric ones FBPB3 writes.
def write_export(root, champion="Tulips", star="Star Man", bench="Bench Man"):
    d = Path(root) / "html"
    d.mkdir(parents=True, exist_ok=True)
    (d / "standings.htm").write_text(
        "<html><body><table>"
        "<tr><td>&nbsp;Team</td><td>&nbsp;W</td><td>&nbsp;L</td></tr>"
        "<tr><td>&nbsp;Tulips</td><td>&nbsp;28</td><td>&nbsp;2</td></tr>"
        "<tr><td>&nbsp;Berries</td><td>&nbsp;2</td><td>&nbsp;28</td></tr>"
        "</table></body></html>", encoding="latin-1")
    (d / "playoffs.htm").write_text(
        "<html><body>2029 Playoff Brackets 1st Round Conference Finals League Finals"
        f" #1 &#160; {champion} 1 &#160; #4 &#160; Spirits 0"
        f" #1 &#160; {champion} 1 &#160; #3 &#160; Clams 0"
        " #3 &#160; Clams 1 &#160; #2 &#160; Boulders 0"
        f" #1 &#160; {champion} 2 &#160; #2 &#160; Berries 1"
        " #2 &#160; Berries 1 &#160; #3 &#160; Rails 0"
        " #2 &#160; Berries 1 &#160; #4 &#160; Kings 0"
        " #4 &#160; Kings 1 &#160; #1 &#160; Generals 0</body></html>",
        encoding="latin-1")
    return d


def fake_cache(star, bench, champion="Tulips"):
    """The cache for_character builds, filled by hand so the readers are not under test here."""
    # A REAL FIELD, not two men. rank_in is a placing among everybody in `totals`, so a
    # two-player league puts the worst man in the league inside its top 25 and the grant looks
    # far flatter than it is. Thirty filler lines sit between the two, which is what makes the
    # top-25 component actually select.
    totals = {
        star: {"team": champion, "G": 30, "PTS": 900, "REB": 400, "AST": 200,
               "STL": 60, "BLK": 40},
        bench: {"team": "Berries", "G": 3, "PTS": 4, "REB": 2, "AST": 1,
                "STL": 0, "BLK": 0},
    }
    for i in range(30):
        totals[f"Filler {i}"] = {"team": "Berries", "G": 30, "PTS": 300 + i,
                                 "REB": 150 + i, "AST": 80 + i, "STL": 20 + i, "BLK": 10 + i}
    return {
        "teams": {"Tulips": 30, "Berries": 30},
        "totals": totals,
        "elite": {"PTS": 500, "REB": 250, "AST": 150, "STL": 40, "BLK": 30},
        "awards": {}, "season_awards": [], "honours": [],
        "playoffs": {champion, "Berries"}, "champion": champion,
    }


def test_prep_last_age_promotes_after_seventeen():
    """He plays 14, 15, 16, 17 and moves up after the fourth."""
    print("when a character is promoted")
    check("PREP_LAST_AGE", offseason.PREP_LAST_AGE, 17)
    people = [character("Old", "Enough", 2012), character("Too", "Young", 2013)]
    out = offseason.movers(people, 2029)          # ages 17 and 16
    check("the seventeen year old moves", [c["last_name"] for c in out["college"]], ["Enough"])
    check("the sixteen year old stays", [c["last_name"] for c in out["stay"]], ["Young"])


def test_prep_last_age_is_not_the_filler_cap():
    """The rule that broke this once. Characters and AI bodies leave prep for different reasons.

    PREP_LAST_AGE promotes a character; ageout.AGE_CAPS releases a filler. Tying them together is
    what silently cost every character a season when the age ladder was added, and it would also
    shrink the prep population by a whole year group - which is what caused the roster collapse.
    """
    print("the two age rules are independent")
    check("filler cap is still 19", ageout.AGE_CAPS["prep"], 19)
    check("and is not the promotion age", ageout.AGE_CAPS["prep"] == offseason.PREP_LAST_AGE,
          False)


def test_college_costs_no_ratings_and_the_pros_still_do():
    """Moving up to college is free; the jump to the pros is not."""
    print("what moving up costs")
    check("college factor", offseason.LEVEL_CONVERSION["college"], 1.00)
    check("pro factor", offseason.LEVEL_CONVERSION["pro"], 0.94)
    ratings = {"Inside": 70, "JumpShot": 45, "FT": 30, "Handling": 9}
    check("college keeps every rating", offseason.convert(ratings, 1.00), ratings)
    dropped = offseason.convert(ratings, 0.94)
    check("the pro step still takes some", sum(dropped.values()) < sum(ratings.values()), True)
    # and nothing is taken below the floor, where a percentage stops meaning anything
    check("nothing drops below the floor",
          min(dropped.values()) >= offseason.CONVERSION_FLOOR, True)
    check("a rating already at the floor is left exactly alone",
          offseason.convert({"Handling": offseason.CONVERSION_FLOOR}, 0.94)["Handling"],
          offseason.CONVERSION_FLOOR)


def test_the_grant_is_earned_not_flat(root):
    """The whole point. A title-winning season and a benched one must not pay the same."""
    print("the grant is scaled to the prep season")
    star, bench = "Star Man", "Bench Man"
    html = write_export(root, star=star, bench=bench)
    cache = fake_cache(star, bench)
    good = seasonbonus.promotion_grant(star, html, None, dict(cache))
    poor = seasonbonus.promotion_grant(bench, html, None, dict(cache))
    g, p = sum(v for _, v in good), sum(v for _, v in poor)
    print(f"        {star}: +{g}   {bench}: +{p}")
    check("the good season pays more", g > p, True)
    check("and by a margin worth seeing", g - p >= 10, True)
    check("the benched man is NOT paid a top-25 line",
          any("top 25" in r for r, _ in poor), False)
    check("the poor season still pays a base", p >= seasonbonus.GRANT["grant_base"], True)
    check("the champion's title is named",
          any("won the league" in r for r, _ in good), True)
    check("the benched man gets the development line",
          any("development" in r for r, _ in poor), True)
    check("nobody who never played here is paid",
          seasonbonus.promotion_grant("Ghost Man", html, None, dict(cache)), [])


def test_the_grant_is_not_squeezed_by_the_season_bonus_cap(root):
    """It is paid once in a career; capping it would flatten the thing it exists to make uneven."""
    print("the grant is uncapped")
    star = "Star Man"
    html = write_export(root, star=star)
    good = seasonbonus.promotion_grant(star, html, None, fake_cache(star, "Bench Man"))
    total = sum(v for _, v in good)
    check("it exceeds the prep season-bonus cap",
          total > seasonbonus.BONUS_CAP_BY_LEVEL["prep"], True)


def test_the_bonus_cap_rises_with_the_level():
    """Both stat components are ranked WITHIN a league, so a mover's earned bonus falls."""
    print("the season-bonus cap per level")
    check("prep", seasonbonus.cap_for("prep"), 10)
    check("college", seasonbonus.cap_for("college"), 15)
    check("pro", seasonbonus.cap_for("pro"), 20)
    check("an unknown level falls back", seasonbonus.cap_for(None), 10)
    # Deliberate live tuning still wins, so a universe can be corrected without a deploy.
    check("an explicit setting overrides", seasonbonus.cap_for("pro", {"bonus_cap": 4}), 4)


def effective_development_bonus(settings):
    """What _run_offseason will actually pay - the same expression it uses."""
    return int((settings or {}).get("college_development_bonus",
                                    offseason.COLLEGE_DEVELOPMENT_BONUS))


def test_a_college_season_seen_through_still_pays():
    """Staying pays in points, leaving pays in time. Raising the grant must not invert that.

    ASSERTED ON THE EFFECTIVE VALUE, not the constant. _run_offseason reads
    settings.get("college_development_bonus", COLLEGE_DEVELOPMENT_BONUS), so a settings row wins
    and the constant only applies when none exists - which is how raising it 12 -> 20 was a
    no-op on a universe that had a row of 12, while a test pinned to the constant passed.
    """
    print("the college development bonus")
    check("the constant", offseason.COLLEGE_DEVELOPMENT_BONUS, 20)
    check("with no row, that is what is paid", effective_development_bonus({}), 20)
    check("a row wins, which is the trap", effective_development_bonus(
        {"college_development_bonus": 12}), 12)
    # AND THE ROW THAT ACTUALLY SHIPS. Checking a hypothetical dict is what let the real one sit
    # at 12 while the constant said 20 - so read localstore's own defaults, which is the store
    # the rehearsal and any local universe run on.
    from commissioner import localstore
    check("localstore's shipped row", effective_development_bonus(localstore.DEFAULTS), 20)
    # Staying must still beat the cheapest way of leaving, or the whole thing inverts.
    check("a college season seen through beats nothing",
          effective_development_bonus(localstore.DEFAULTS) > 0, True)


def test_the_browser_agrees_with_the_engine():
    """site/js/rules.js carries its own copy, and it is what a player reads BEFORE he clicks.

    The file's own header says it must stay in step with offseason.py. Nothing checked, so when
    the college conversion went 0.97 -> 1.00 the browser went on telling people that moving up
    costs 3% of every rating while the engine charged nothing. Latent today - only declareWarning
    consumes it, and it passes 'pro' - but the whole reason this table exists in the browser is
    to price an irreversible choice in advance, which is the worst place to be wrong.
    """
    print("the browser's copy of the conversion table")
    js = (Path(__file__).resolve().parents[1] / "site" / "js" / "rules.js").read_text(
        encoding="utf-8")
    import re
    for league, expected in offseason.LEVEL_CONVERSION.items():
        m = re.search(rf"{league}:\s*([0-9.]+)", js.split("LEVEL_CONVERSION", 1)[1][:120])
        check(f"rules.js {league}", float(m.group(1)) if m else None, float(expected))
    for name, value in (("EARLY_PENALTY_PER_YEAR", offseason.EARLY_PENALTY_PER_YEAR),
                        ("EARLY_PENALTY_MAX", offseason.EARLY_PENALTY_MAX),
                        ("COLLEGE_MAX_YEARS", offseason.COLLEGE_MAX_YEARS),
                        ("CONVERSION_FLOOR", offseason.CONVERSION_FLOOR)):
        m = re.search(rf"export const {name} = ([0-9.]+)", js)
        check(f"rules.js {name}", float(m.group(1)) if m else None, float(value))


def test_a_whole_class_does_not_land_on_one_team():
    """pick_slot spreads when it is told who is already where. promote never told it.

    The signup path has passed `busy` since the first five friends landed on two teams, but
    promote took the first free slot in league order - so on 2026-09-21 all seven were promoted
    together and every one of them went to MJW. They had to be swapped apart by hand.

    This drives pick_slot exactly the way promote now does: one tally, counted up after each
    placement. Without the counting the loop picks the same team every time, which is the bug.
    """
    print("a promoted class spreads across the league")
    from commissioner import characters as chmod
    teams = [f"T{i:02d}" for i in range(16)]
    slots = []
    for t in teams:                       # three reserve rows a team, as the universe is built
        for n in range(3):
            slots.append(SimpleNamespace(name=f"{t}-res{n}", dob="1/1/2012", team=t))
    busy = {t: 0 for t in teams}
    divisions = {t: i // 4 for i, t in enumerate(teams)}

    landed = []
    for _ in range(7):
        slot = chmod.pick_slot(slots, None, busy=busy, divisions=divisions)
        assert slot is not None, "ran out of slots"
        slots = [s for s in slots if s is not slot]
        busy[slot.team] = busy.get(slot.team, 0) + 1
        landed.append(slot.team)
    check("seven characters, seven teams", len(set(landed)), 7)

    # and the control: without counting them, they stack - which is exactly what happened live
    slots2 = []
    for t in teams:
        for n in range(3):
            slots2.append(SimpleNamespace(name=f"{t}-res{n}", dob="1/1/2012", team=t))
    flat = {t: 0 for t in teams}
    stacked = []
    for _ in range(7):
        slot = chmod.pick_slot(slots2, None, busy=flat, divisions=divisions)
        slots2 = [s for s in slots2 if s is not slot]
        stacked.append(slot.team)
    check("without the tally they stack", len(set(stacked)) < 7, True)


def test_promote_accepts_and_uses_a_tally():
    """The signature the caller relies on, so the tally cannot be quietly dropped again."""
    print("promote takes a busy tally")
    import inspect
    sig = inspect.signature(offseason.promote)
    check("promote has a busy parameter", "busy" in sig.parameters, True)
    src = inspect.getsource(offseason.promote)
    check("and passes it to pick_slot", "busy=busy" in src, True)


def main():
    with tempfile.TemporaryDirectory() as root:
        test_prep_last_age_promotes_after_seventeen()
        test_prep_last_age_is_not_the_filler_cap()
        test_college_costs_no_ratings_and_the_pros_still_do()
        test_the_grant_is_earned_not_flat(Path(root) / "a")
        test_the_grant_is_not_squeezed_by_the_season_bonus_cap(Path(root) / "b")
        test_the_bonus_cap_rises_with_the_level()
        test_a_college_season_seen_through_still_pays()
        test_the_browser_agrees_with_the_engine()
        test_a_whole_class_does_not_land_on_one_team()
        test_promote_accepts_and_uses_a_tally()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  promotion: a character moves up after his age-17 season, independently of the "
          "filler cap; college costs him no ratings and the pro step still does; the grant is "
          "earned rather than flat and is not squeezed by the season-bonus cap, which now rises "
          "with the level")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
