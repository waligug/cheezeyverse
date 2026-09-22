"""The contract field: the only 32-bit value in a player record, and the one that decides survival.

WHY THIS FIELD MATTERS. With Full Finances, FBPB3 RELEASES a player who has no contract the moment
the league loads (CONVENTIONS.md:73). On the live pro save 45 rostered players and 11 of the 60
reserve seats carry nothing, so turning Finances on without writing contracts first would empty
those rows on the next load - and a reserve seat is exactly where a drafted character is stamped.

WHY IT IS TESTED AGAINST THE GAME'S OWN EXPORT rather than against itself. There is no spec for
this format. The offset was found by probing for `generate.IMPORT_CONTRACT` (1000000), which 255
of the 530 pro players carry, and the FIRST attempt was one byte low - it returned 256000000,
which is the same bytes shifted and looks entirely plausible on its own. Only a diff against the
League Editor's own CSV caught it. So the export comparison IS the test; a round-trip alone would
have happily agreed with a wrong offset.

    python tests/test_contract.py
"""
from __future__ import annotations

import csv
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import (  # noqa: E402
    CONTRACT_MAX, CONTRACT_YEARS, CodecError, LeagueDat)

SAVE = ROOT / "fixtures/saves/cv-pro-aged/league.dat"
EXPORT = ROOT / "fixtures/exports/cv-pro-aged.csv"

failures: list[str] = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        failures.append(name)


def _export_contracts():
    """{(first, last, (m, d, y)): [seven ints]} straight off the game's own player export."""
    out = {}
    for r in csv.DictReader(open(EXPORT, encoding="latin-1")):
        mo, dd, yy = r["DOB"].strip().split("/")
        key = (r["FirstName"].strip().lower(), r["LastName"].strip().lower(),
               (int(mo), int(dd), int(yy)))
        out[key] = [int((r.get(f"Contract{i}") or "0").strip() or 0)
                    for i in range(1, CONTRACT_YEARS + 1)]
    return out


def _key(p):
    return (p.first.strip().lower(), p.last.strip().lower(),
            (p.values["BirthMonth"], p.values["BirthDay"], p.values["BirthYear"]))


def test_reads_what_the_game_exports():
    """Every contract year of every player, against the League Editor's own CSV of the same save."""
    if not (SAVE.exists() and EXPORT.exists()):
        print("SKIP  cv-pro-aged fixture pair missing")
        return
    L = LeagueDat(SAVE)
    want = _export_contracts()
    matched = [p for p in L.players if _key(p) in want]
    check("every player in the save is in the export",
          len(matched) == len(L.players), f"{len(matched)}/{len(L.players)}")
    agree = [p for p in matched if p.contract == want[_key(p)]]
    check("all seven contract years match the game, for every player",
          len(agree) == len(matched), f"{len(agree)}/{len(matched)}")

    # The shape of the problem this field exists to solve - if these ever reach zero, the
    # Finances work is done; if they grow, something is creating contract-less players again.
    bare = [p for p in L.players if not any(p.contract)]
    rostered_bare = [p for p in bare if p.values["Team"] > 0]
    check("the fixture still has contract-less players to reason about", bare, f"{len(bare)} bare")
    print(f"      (of them {len(rostered_bare)} are on a roster - those are the ones a "
          "Full-Finances load would release)")


def test_writes_and_survives_a_reload():
    if not SAVE.exists():
        print("SKIP  cv-pro-aged save fixture missing")
        return
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "league.dat"
        shutil.copy2(SAVE, work)
        L = LeagueDat(work)
        before_len = len(L.data)
        bare = [p for p in L.players if not any(p.contract) and p.values["Team"] > 0]
        if not bare:
            print("SKIP  no contract-less rostered player in the fixture")
            return
        target = bare[0]
        L.set_contract(target, [1000000])
        check("set_contract fills the remaining years with zero",
              target.contract == [1000000] + [0] * (CONTRACT_YEARS - 1), f"{target.contract}")
        L.save()

        back = LeagueDat(work)
        got = back.find(target.name, target.dob)
        check("it is still there after a save and a fresh parse",
              got.contract == [1000000] + [0] * (CONTRACT_YEARS - 1), f"{got.contract}")
        check("the file did not change length", len(back.data) == before_len)
        # NOTHING ELSE MOVED. An int32 write into a record of int16s is the one edit in this
        # codec that could plausibly land in a neighbour's field.
        origin = LeagueDat(SAVE)
        moved = [a.name for a, b in zip(origin.players, back.players) if a.contract != b.contract]
        check("exactly one player's contract changed", moved == [target.name], f"{moved}")
        drifted = [a.name for a, b in zip(origin.players, back.players) if a.values != b.values]
        check("and no int16 field anywhere was disturbed", not drifted, f"{drifted[:4]}")


def test_it_refuses_what_would_corrupt_a_save():
    if not SAVE.exists():
        print("SKIP  cv-pro-aged save fixture missing")
        return
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "league.dat"
        shutil.copy2(SAVE, work)
        L = LeagueDat(work)
        p = L.players[0]
        for bad, why in (([-1], "a negative salary"),
                         ([CONTRACT_MAX + 1], "more money than the field holds"),
                         ([1] * (CONTRACT_YEARS + 1), "an eighth contract year"),
                         (["lots"], "a salary that is not a number")):
            try:
                L.set_contract(p, bad)
                check(f"refuses {why}", False, "it was accepted")
            except CodecError:
                check(f"refuses {why}", True)

        # THE TRAP THIS GUARD EXISTS FOR: `set()` packs int16. Routing a salary through it does
        # not raise on its own - 1000000 & 0xffff is 16960 - so it would write a plausible-looking
        # wrong number into the file and nothing downstream would question it.
        try:
            L.set(p, "Contract1", 1000000)
            check("set() refuses a contract instead of truncating it", False, "it truncated")
        except CodecError as exc:
            check("set() refuses a contract instead of truncating it", "set_contract" in str(exc))

        # A zero contract is a legal thing to write - it is how you clear one - and must not be
        # confused with the range guard above.
        L.set_contract(p, [])
        check("clearing a contract is allowed", p.contract == [0] * CONTRACT_YEARS, f"{p.contract}")


def test_signing_a_free_agent_pays_him():
    """The roster backfill signs from a pool that is 150-for-150 contract-less.

    Without this the guard would top up a short team every publish with players the next load
    releases again - the league shedding and re-signing the same men for ever, one team at a
    time, and nothing anywhere reporting it.
    """
    if not SAVE.exists():
        print("SKIP  cv-pro-aged save fixture missing")
        return
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "league.dat"
        shutil.copy2(SAVE, work)
        L = LeagueDat(work)
        pool = [p for p in L.players if p.values["Team"] <= 0 and not any(p.contract)]
        check("the free-agent pool really is unpaid", len(pool) > 50, f"{len(pool)} bare")

        short = min(L.teams().items(), key=lambda kv: kv[1]["size"])[0]
        man = pool[0]
        # sign_many IS THE PRODUCTION PATH - protect_rosters' backfill and ageout's intake both
        # use it, and nothing outside tests calls sign(). The first version of this test drove
        # sign(), so the guard could be (and was) unreachable while this still passed.
        L.sign_many([(man, short)])
        man = L.find(man.name, man.dob)
        check("a signed player leaves with a contract", any(man.contract), f"{man.contract[:2]}")
        check("he is on the team", man.values["Team"] == short, f"{man.values['Team']}")
        # AND FOR LONGER THAN ONE YEAR. One year expires at the next rollover's free agency, so
        # a one-year backfill is a release-on-load postponed by an offseason, not prevented.
        check("and for more than a single year",
              sum(1 for y in man.contract if y > 0) > 1,
              f"{sum(1 for y in man.contract if y > 0)}yr")
        check("sign() carries the same guarantee",
              "_ensure_paid" in __import__("inspect").getsource(LeagueDat.sign), True)

        # AND AN EXISTING DEAL IS NEVER OVERWRITTEN. A player the game already pays keeps his
        # number; signing must not quietly reprice somebody.
        paid = [p for p in L.players if p.values["Team"] <= 0 and any(p.contract)]
        if paid:
            rich = paid[0]
            L.set_contract(rich, [4_250_000])
            L.sign(rich, short)
            check("signing does not reprice a man who already had a deal",
                  rich.contract[0] == 4_250_000, f"{rich.contract[:2]}")

        L.save()
        back = LeagueDat(work)
        again = back.find(man.name, man.dob)
        check("and it survives the save", any(again.contract), f"{again.contract[:2]}")


def test_a_drafted_man_gets_a_deal_that_outlasts_the_rollover():
    """A one-year rookie deal expires in the offseason it is signed.

    run_draft happens INSIDE the offseason and the game's own rollover runs straight after it.
    With Finances on that rollover includes FREE AGENCY, where every expiring contract is thrown
    open at once - measured on a clone 2026-09-22, all 511 players became free agents together.
    So a character stamped with one year would be a free agent minutes after being drafted, and
    the AI would place him wherever it liked. "Drafted #1 by LCH" has to survive the same night.

    THE CASE THAT MATTERS IS A SEAT THAT IS NOT BARE. 49 of pro's 60 reserve rows already carry a
    deal and every one of them is a single year, so a gate of "only fill an empty contract" left
    the rookie term unwritten on 82% of the places a pick can land. The first version of this
    test could not see that, because it called check(name, got, want) against a helper whose
    signature is check(name, cond, detail) - every assertion printed its expected value and
    compared nothing at all.
    """
    if not SAVE.exists():
        print("SKIP  cv-pro-aged save fixture missing")
        return
    from commissioner import characters as ch
    from commissioner.codec.league_dat import RATINGS

    def years_of(contract):
        return sum(1 for y in contract if y > 0)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "league.dat"
        shutil.copy2(SAVE, work)
        L = LeagueDat(work)

        rostered = [p for p in L.players if p.values["Team"] > 0]
        bare = [p for p in rostered if not any(p.contract)]
        held = [p for p in rostered if years_of(p.contract) == 1]
        check("the fixture has rows that already hold a one-year deal", bool(held),
              f"{len(held)} of {len(rostered)} rostered")
        if not (bare and held):
            print("SKIP  the fixture lacks both a bare row and a held one")
            return

        used = 0

        def stamp(seat, years):
            nonlocal used
            used += 1
            slot = type("S", (), {"name": seat.name, "dob": seat.dob,
                                  "height": 0, "weight": 0})()
            return ch.stamp_character(L, slot, {
                "first_name": "Rookie", "last_name": "Number%d" % used,
                "dob": seat.dob, "height_inches": 86, "position": "C",
                "ratings": {f: 70 for f in RATINGS}, "potentials": {},
                "contract_years": years})

        # THE REGRESSION. A seat that already holds one year must still end up on four.
        occupied = held[0]
        before = list(occupied.contract)
        pl = stamp(occupied, 4)
        got = L.contract_of(pl)
        check("a stated term overwrites the seat's existing deal",
              years_of(got) == 4, f"was {years_of(before)}yr, now {years_of(got)}yr")
        check("and every year of it is paid the same",
              len(set(y for y in got if y > 0)) == 1, f"{got[:4]}")

        pl = stamp(bare[0], 4)
        check("a bare seat also gets the full term",
              years_of(L.contract_of(pl)) == 4, f"{L.contract_of(pl)[:4]}")

        pl = stamp(bare[1] if len(bare) > 1 else held[1], None)
        check("no term stated still leaves him paid",
              years_of(L.contract_of(pl)) >= 1, f"{L.contract_of(pl)[:2]}")

        pl = stamp(bare[2] if len(bare) > 2 else held[2], 99)
        check("a silly term is clipped to what the field holds",
              years_of(L.contract_of(pl)) == CONTRACT_YEARS,
              f"{years_of(L.contract_of(pl))}")

        L.save()
        back = LeagueDat(work)
        still = back.find("Rookie Number1", occupied.dob)
        check("the term survives a save", years_of(still.contract) == 4,
              f"{still.contract[:4]}")


def test_a_rookie_is_paid_like_one_rather_than_given_a_token():
    """Four years of the $1M import token is a cage, not a rookie deal.

    Free agency is the only event that ever prices a character properly - the AI paid $482,464 to
    $22,218,759 on the clone, median $1,460,090 - so a man held on the league's setup token skips
    being valued for as many years as his deal runs. A rookie scale keeps him on the team that
    drafted him AND pays him like a first-rounder.
    """
    from commissioner.offseason import (rookie_salary, ROOKIE_SALARY_MIN, ROOKIE_SALARY_TOP,
                                        ROOKIE_SALARY_PICKS, ROOKIE_GAME_YEARS, ROOKIE_YEARS)
    from commissioner.characters import IMPORT_CONTRACT, LEVEL_CONTRACT_YEARS

    # THE TWO TERMS ARE DIFFERENT ON PURPOSE and confusing them is how somebody ends up locked
    # out of free agency for four years. ROOKIE_YEARS is skill points; ROOKIE_GAME_YEARS is the
    # contract FBPB3 sees, and it is short so the league prices him.
    check("the game contract is short", ROOKIE_GAME_YEARS == 1, f"{ROOKIE_GAME_YEARS}")
    check("the points rookie scale is not", ROOKIE_YEARS > 1, f"{ROOKIE_YEARS}")
    check("and every level default is short too",
          set(LEVEL_CONTRACT_YEARS.values()) == {1}, f"{LEVEL_CONTRACT_YEARS}")

    scale = [rookie_salary(n) for n in range(1, ROOKIE_SALARY_PICKS + 2)]
    check("it never rises with the pick number", scale == sorted(scale, reverse=True), "")
    check("first overall is the top of the scale", rookie_salary(1) == ROOKIE_SALARY_TOP,
          f"${rookie_salary(1):,}")
    check("and it is well clear of the setup token",
          rookie_salary(1) > IMPORT_CONTRACT * 4, f"${rookie_salary(1):,}")
    check("a late pick falls to the league minimum",
          rookie_salary(ROOKIE_SALARY_PICKS) == ROOKIE_SALARY_MIN,
          f"${rookie_salary(ROOKIE_SALARY_PICKS):,}")
    check("nobody is ever paid nothing", min(scale) > 0, f"${min(scale):,}")
    # The taper is geometric: the gap at the top of the board must be worth more than the gap at
    # the bottom, or the scale says a lottery pick and a second-rounder are nearly the same man.
    check("the top of the board is worth more than the bottom",
          (rookie_salary(1) - rookie_salary(2)) > (rookie_salary(15) - rookie_salary(16)), True)
    for bad in (None, 0, -3, "x"):
        check(f"pick {bad!r} still pays the minimum", rookie_salary(bad) == ROOKIE_SALARY_MIN, "")

    # AND IT STAYS INSIDE THE EXCEPTION A CAPPED-OUT TEAM CAN STILL USE. The draft order is
    # REVERSE standings, so a high pick goes to a bad team - but after free agency every team in
    # the clone was $65-78M against a $63,482,168 cap, because Bird rights let you exceed it to
    # re-sign your own. The number that actually decides whether a rookie can be paid is the
    # mid-level exception, measured at $5,468,453 on the live save, not the cap.
    MID_LEVEL = 5_468_453
    check("even the first pick fits inside the mid-level exception",
          rookie_salary(1) <= MID_LEVEL, f"${rookie_salary(1):,} vs ${MID_LEVEL:,}")
    check("and he is a real fraction of it, not a rounding error",
          rookie_salary(1) > MID_LEVEL * 0.5, f"${rookie_salary(1):,}")


test_reads_what_the_game_exports()
test_writes_and_survives_a_reload()
test_it_refuses_what_would_corrupt_a_save()
test_signing_a_free_agent_pays_him()
test_a_drafted_man_gets_a_deal_that_outlasts_the_rollover()
test_a_rookie_is_paid_like_one_rather_than_given_a_token()

if failures:
    print("\nFAILED: " + ", ".join(failures))
elif not SAVE.exists():
    print("\nSKIPPED: the cv-pro-aged fixture is gitignored and absent; the contract field was "
          "never opened. On SERVERPC this must not skip.")
else:
    print("\nOK  contract: read matches the game's own export for every player and every year, "
          "a write survives a reload without disturbing a neighbouring field, and the edits "
          "that would corrupt a save are refused")
sys.exit(1 if failures else 0)
