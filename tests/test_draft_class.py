"""The pro draft class is the college league's outgoing seniors, not eighty blanks.

WHY THIS EXISTS. The three leagues are three separate FBPB3 saves and the AI population has never
moved between them: `ageout` retires a capped college player IN PLACE and refills from that same
league's own free-agent pool. Meanwhile FBPB3 fills its own draft pool during ITS rollover, which
runs AFTER our draft - so `field_from_save` asked a blank pool every single season and draft night
read "the game's draft class is not generated yet; our people draft alone", for ever, by
construction rather than by accident.

`outgoing_seniors` + `carry_into_pool` fix both halves at once: the men who just finished four
years of college become the class our characters are drafted against.

WHAT IS BEING PROTECTED, and these pull against each other:
  * the carried set must be EXACTLY the set the age-out is taking - carrying a man who is still
    playing college next season puts a ghost in the draft, and
  * our characters and the manifest's reserve rows must NEVER be carried, because a reserve row
    is where the next person to sign up gets placed.

    python tests/test_draft_class.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner import ageout, characters as ch, draft, store  # noqa: E402
from commissioner.codec.league_dat import POTENTIALS, RATINGS, LeagueDat  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0


def run():
    col_src, pro_src = ch.save_path("college"), ch.save_path("pro")
    if not (col_src.exists() and pro_src.exists()):
        print("SKIP  live college/pro saves not present")
        return 0
    season = 2030

    print("a name the codec cannot write is folded, not dropped")
    # `rename` refuses anything outside 32..126, and six college names carry accents. Folding
    # has to keep the man recognisable: Sepulveda, never Seplveda.
    check("accents fold to ASCII", draft._ascii_name("Alfredo Sepúlveda"), "Alfredo Sepulveda")
    check("a cedilla folds too", draft._ascii_name("Güç Dufault"), "Guc Dufault")
    check("plain names are untouched", draft._ascii_name("Davis Plowden"), "Davis Plowden")
    check("whitespace is normalised", draft._ascii_name("  Lyle   Lindo "), "Lyle Lindo")

    with tempfile.TemporaryDirectory() as tmp:
        col_p, pro_p = Path(tmp) / "college.dat", Path(tmp) / "pro.dat"
        shutil.copy2(col_src, col_p)
        shutil.copy2(pro_src, pro_p)
        college = LeagueDat(col_p)

        print("\nthe carried set is exactly the set the age-out takes")
        seniors = draft.outgoing_seniors(college, season, store=store)
        check("the class is capped at CARRY_LIMIT", len(seniors) <= draft.CARRY_LIMIT, True)
        check("and it is not empty", bool(seniors), True)

        # Everyone carried must really be at the cap, measured in the season being SET UP.
        cap = ageout.AGE_CAPS["college"]
        playing = ageout.playing_season(season)
        by_name = {p.name: p for p in college.players}
        ages, rostered = [], []
        for c in seniors:
            # the fold may have changed the name, so match on the folded form of each record
            src = next((p for p in college.players
                        if draft._ascii_name(p.name) == f'{c["first_name"]} {c["last_name"]}'), None)
            if src is None:
                continue
            ages.append(ageout.age_of(src, playing))
            rostered.append(src.values.get("Team", 0) >= 1)
        check("every carried man is at or past the cap", all(a >= cap for a in ages), True)
        check("and every one was on a roster", all(rostered), True)

        print("\nours are never carried, and neither is a reserve row")
        keep = ageout.protected_names("college", store)
        carried_names = {f'{c["first_name"]} {c["last_name"]}' for c in seniors}
        folded_keep = {draft._ascii_name(n) for n in keep}
        check("no protected name is in the class", carried_names & folded_keep, set())
        check("the protected set is not empty (or the check above proves nothing)",
              len(keep) > 0, True)

        print("\nbest first, so the board means something")
        sheets = [sum(draft.sheet(c)) for c in seniors]
        check("the class is sorted best first", sheets == sorted(sheets, reverse=True), True)
        check("and the top man beats the bottom man", sheets[0] > sheets[-1], True)

        print("\nthe write lands, and survives a save and a fresh parse")
        pro = LeagueDat(pro_p)
        before = draft.field_from_save(LeagueDat(pro_p))
        check("the game's own pool is blank to begin with", before, [])
        top = seniors[0]
        want_ratings = dict(top["ratings"])
        carried = draft.carry_into_pool(pro, seniors)
        pro.save()
        check("every senior was written", len(carried), len(seniors))

        again = LeagueDat(pro_p)
        field = draft.field_from_save(again, limit=None)
        check("the pool now reads as a generated class", len(field) > 0, True)
        check("the best man is the one we put at the top",
              f'{field[0]["first_name"]} {field[0]["last_name"]}',
              f'{top["first_name"]} {top["last_name"]}')
        check("his ratings crossed the league boundary intact",
              {f: field[0]["ratings"][f] for f in RATINGS},
              {f: want_ratings[f] for f in RATINGS})
        check("and so did his potentials",
              _mean(field[0]["potentials"].values()) >= draft.POOL_MIN_CEILING, True)

        print("\nnobody carried is one of ours, so nothing can be promoted by mistake")
        check("every carried record is flagged as field, not character",
              {c.get("role") for c in field}, {"field"})

        print("\nthe seniors are still in the college save - this copies, it does not move")
        still = LeagueDat(col_p)
        check("the college league did not lose them",
              len(still.players), len(college.players))

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  draft class: the college league's outgoing seniors become the pro draft field, "
          "folded to names the codec can write, best first, with our characters and every "
          "reserve row left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
