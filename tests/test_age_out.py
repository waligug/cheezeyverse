"""The AI population ages out, a new class arrives, and our people are never touched.

Nothing ever aged an AI player out. `PREP_LAST_AGE` looks like it does, but `movers()` walks the
STORE, so it only ever moved our characters. Two seasons in, prep was running 15-19 against the
14-17 band it was generated with, and the 2027 prep scoring title went to an eighteen-year-old.

THE ASSERTION THIS FILE EXISTS FOR is the one about the season being SET UP. The first version of
this test checked the cap in the season just finished - the same season the code was checking -
so it passed while the code was wrong, and every 17-year-old sailed through the rollover to play
the next season at 18. A test that shares the bug's assumption cannot see the bug. So the age
checks below are all measured in `playing_season(season)`, deliberately, and one of them pins
that function on its own.

It runs the real thing against a COPY of the real save, because the codec is the one part of this
system with no undo, and a fixture would not have caught the other two bugs found writing it:
accented names abort `rename`, and every Player handle goes stale the moment anything splices.

PREP IS TESTED IN FULL and college by plan only. Every edit re-parses all 425 player records, so
a full pass on both leagues costs this suite about fifteen minutes. Prep is the league with our
characters in it and the one the bug was found in.

    python tests/test_age_out.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import ageout  # noqa: E402
from commissioner.codec.league_dat import POTENTIALS, RATINGS, LeagueDat  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
FAILS = []


def ok(cond, msg):
    if not cond:
        FAILS.append(msg)


def rostered(L):
    ids = set()
    for info in L.teams().values():
        ids.update(info["ids"])
    return [p for p in L.players if p.id in ids]


def check_prep(finished, work):
    key = "prep"
    spec = cfg.BY_KEY[key]
    src = DOCS / "leaguedata" / spec.save_name / "league.dat"
    if not src.exists():
        print(f"SKIP  {key}: no save at {src}")
        return False
    copy = work / "prep.dat"
    shutil.copy2(src, copy)

    play = ageout.playing_season(finished)
    cap, intake_age = ageout.AGE_CAPS[key], ageout.INTAKE_AGE[key]
    before = LeagueDat(copy)
    keep = ageout.protected_names(key, None)
    before_rostered = rostered(before)

    # ---- a dry run must not touch the file ------------------------------------------------
    stamp = (copy.stat().st_mtime, copy.stat().st_size)
    ageout.apply(key, finished, store=None, save_path=copy, dry_run=True, log=lambda m: None, seed=3)
    ok((copy.stat().st_mtime, copy.stat().st_size) == stamp, f"{key}: a dry run wrote to the save")

    result = ageout.apply(key, finished, store=None, save_path=copy, dry_run=False,
                          log=lambda m: None, seed=3)
    after = LeagueDat(copy)
    after_rostered = rostered(after)

    # ---- THE ONE THAT MATTERS: nobody plays the coming season over the cap ------------------
    over = sorted({ageout.age_of(p, play) for p in after_rostered
                   if ageout.age_of(p, play) >= cap})
    ok(not over,
       f"{key}: players would be {over} during season {play}, and the cap is {cap}. Measuring "
       f"the cap in the season just finished ({finished}) is what let an 18-year-old win the "
       "2027 scoring title after surviving a rollover.")
    ages = Counter(ageout.age_of(p, play) for p in after_rostered)
    ok(min(ages) >= intake_age,
       f"{key}: somebody is younger than the {intake_age} the intake arrives at: {dict(sorted(ages.items()))}")
    ok(set(ages) <= set(range(intake_age, cap)),
       f"{key}: season {play} ages are {dict(sorted(ages.items()))}, not the {intake_age}-{cap - 1} band")

    # ---- rosters stay full -----------------------------------------------------------------
    sizes = Counter(len(v["ids"]) for v in after.teams().values())
    ok(set(sizes) == {spec.roster_size},
       f"{key}: rosters are no longer all {spec.roster_size}: {dict(sizes)}. A short roster is "
       "what the AI fills with an adult free agent.")
    ok(len(after.players) == len(before.players),
       f"{key}: the file gained or lost records ({len(before.players)} -> {len(after.players)})")

    # ---- our people are untouched ----------------------------------------------------------
    after_names = {p.name for p in after.players}
    before_names = {p.name for p in before.players}
    lost = [n for n in keep if n in before_names and n not in after_names]
    ok(not lost, f"{key}: reserve slots were recycled away: {lost[:5]}")
    was = {p.name for p in before_rostered if p.name in keep}
    now = {p.name for p in after_rostered}
    dropped = sorted(was - now)
    ok(not dropped,
       f"{key}: protected players were released: {dropped[:5]}. The next person to sign up "
       "would fail to be placed.")
    touched = {r["name"] for r in result["retired"]} | {a["recycled_from"] for a in result["arrived"]}
    ok(not (touched & keep), f"{key}: the age-out touched protected rows: {sorted(touched & keep)[:5]}")

    # ---- the retired cannot be signed back --------------------------------------------------
    for row in result["retired"][:8]:
        try:
            pl = after.find(row["name"], row["dob"])
        except Exception:
            continue
        ok(pl.values["Team"] < 1, f"{key}: {row['name']} was retired but is still on a team")
        highest = max(pl.values.get(f, 0) for f in RATINGS)
        ok(highest <= ageout.FLOOR_RATING,
           f"{key}: {row['name']} left with a rating of {highest}; a GM will sign him back")
        ok(max(pl.values.get(f, 0) for f in POTENTIALS) <= ageout.FLOOR_POTENTIAL,
           f"{key}: {row['name']} kept his potential; the AI signs for upside")

    # ---- the newcomers are real players -----------------------------------------------------
    for row in result["arrived"][:8]:
        pl = after.find(row["name"], row["dob"])
        ok(ageout.age_of(pl, play) == intake_age,
           f"{key}: {row['name']} arrived at {ageout.age_of(pl, play)} for season {play}, "
           f"not {intake_age}")
        ok(pl.values["Team"] >= 1, f"{key}: {row['name']} was created but never signed")
        ok(max(pl.values.get(f, 0) for f in RATINGS) > ageout.FLOOR_RATING,
           f"{key}: {row['name']} arrived at the defang floor - a recycled corpse, not an intake")
        ok(60 <= pl.values["Height"] <= 90, f"{key}: {row['name']} is {pl.values['Height']} inches")

    print(f"  prep: {len(result['retired'])} retired, {len(result['arrived'])} arrived; "
          f"season {play} ages {dict(sorted(ages.items()))}, rosters {dict(sizes)}")
    return True


def main():
    try:
        from commissioner.simweek import store
        finished = int(store().get_settings().get("current_season", 0))
    except Exception:
        finished = 0
    if not finished:
        print("SKIP  could not read the current season from the store")
        return 0

    # The rollover sets up the NEXT season, and that is the one the cap belongs to. Pinned on its
    # own, because every other assertion here would still pass if this were off by one - which is
    # exactly how the first version of this file passed against broken code.
    ok(ageout.playing_season(2027) == 2028,
       f"playing_season(2027) is {ageout.playing_season(2027)}; a rollover after 2027 sets up 2028")

    work = Path(tempfile.mkdtemp(prefix="age-out-"))
    try:
        ran = check_prep(finished, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not ran:
        print("SKIP  no prep save to test against")
        return 0

    # College by plan only - see the module docstring on runtime.
    play = ageout.playing_season(finished)
    p = ageout.plan("college", finished, store=None)
    cap = ageout.AGE_CAPS["college"]
    ok(p["capped"] and p["season"] == play,
       f"college: the plan is measured in season {p.get('season')}, not the {play} being set up")
    ok(p["intake"] == len(p["retiring"]),
       f"college: {len(p['retiring'])} would leave but only {p['intake']} would arrive; "
       "the rosters would not come back to full")
    ok(all(r["age"] >= cap for r in p["retiring"]),
       "college: the plan would retire somebody under the cap")
    print(f"  college (plan only): {len(p['retiring'])} would retire at {cap}+ in season {play}, "
          f"{p['intake']} would arrive from a pool of {p['pool']}")

    # pro must be refused outright rather than quietly doing something surprising
    out = ageout.apply("pro", finished, store=None, log=lambda m: None)
    ok(out["capped"] is False, "pro was given an age cap; a pro career ends by retirement")

    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  age-out: nobody plays the coming season over the cap, the intake restores the "
          "band, rosters stay full, our characters and every unclaimed reserve slot survive, "
          "the retired are defanged so no GM signs them back, and a dry run writes nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
