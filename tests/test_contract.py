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
        L.sign(man, short)
        check("a signed player leaves with a contract", any(man.contract), f"{man.contract[:2]}")
        check("he is on the team", man.values["Team"] == short, f"{man.values['Team']}")

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


test_reads_what_the_game_exports()
test_writes_and_survives_a_reload()
test_it_refuses_what_would_corrupt_a_save()
test_signing_a_free_agent_pays_him()

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
