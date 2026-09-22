"""Give each of OUR characters a deal that outlives the rollover he is sitting in.

WHAT IS ACTUALLY WRONG TODAY, measured on the live college save (2026-09-22):

    Chris Zimmer       MJW   NO CONTRACT AT ALL
    Liam Zimmel        KZO   NO CONTRACT AT ALL
    Zach Russell       FLN   1 year
    Tim Turner         SHB   1 year
    Johnny Gartholomew HEL   1 year
    Dodger Manson      YPS   1 year
    Gravy Jones        OSH   1 year

Two of the seven real people in this universe are on college rosters with an empty contract
block, and the other five hold a deal that expires at the very next rollover's free-agency stage.
Neither is visible anywhere: nothing reports a contract, and with Finances Off the game does not
complain. The day college or pro is switched to Full Finances, the first two are RELEASED as the
league loads and the other five are thrown open to the AI in the first offseason they see.

WHY A SEPARATE TOOL FROM `grant_contracts.py`. That one is deliberately narrow: it only ever ADDS
a contract to a row that has none, across every rostered player in a save, and explicitly never
changes an existing one. Both of those are right for what it does and wrong here - five of the
seven already have a deal, it is just too short, and this must touch nobody but our own people.

WHAT IT WILL NOT DO. It never shortens a deal, never changes a salary that already exists, never
touches a player who is not one of ours, and never runs while the game is open or a sim is in
flight. A character already on a long enough deal is left completely alone.

    python tools/fix_character_contracts.py                 # report only, writes nothing
    python tools/fix_character_contracts.py --apply
    python tools/fix_character_contracts.py --apply --league college
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch                              # noqa: E402
from commissioner import simweek                                       # noqa: E402
from commissioner.codec.league_dat import LeagueDat                    # noqa: E402
from commissioner.driver.fbpb3 import FBPB3                            # noqa: E402

BACKUPS = ROOT / "backups"


def survey(store, leagues=None):
    """[(character, league, player, current_years, wanted_years)] for everybody live."""
    rows = []
    saves = {}
    for c in store.characters():
        if c.get("status") not in ("active", "declared"):
            continue
        key = c.get("league")
        if not key or (leagues and key not in leagues):
            continue
        wanted = ch.LEVEL_CONTRACT_YEARS.get(key)
        if not wanted:
            continue
        try:
            L = saves.get(key) or saves.setdefault(key, LeagueDat(ch.save_path(key)))
            pl = L.find(f'{c["first_name"]} {c["last_name"]}', ch.codec_dob(c.get("game_dob")))
        except Exception as exc:                                       # noqa: BLE001
            print(f"   ! could not read {c['first_name']} {c['last_name']} in {key}: {exc}")
            continue
        deal = L.contract_of(pl)
        rows.append((c, key, pl, sum(1 for v in deal if v), wanted, deal, L))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    ap.add_argument("--league", action="append", default=None,
                    help="limit to one league key (repeatable)")
    ap.add_argument("--salary", type=int, default=ch.IMPORT_CONTRACT,
                    help="only used for a character who has no salary at all")
    a = ap.parse_args(argv)

    rows = survey(simweek.store(), a.league)
    if not rows:
        print("no live characters to check.")
        return 0

    print(f"{len(rows)} live character{'' if len(rows) == 1 else 's'}\n")
    short = []
    for c, key, pl, years, wanted, deal, _L in rows:
        name = f'{c["first_name"]} {c["last_name"]}'
        state = f"{years} yr" if years else "NO CONTRACT"
        if years >= wanted:
            print(f"   ok    {name:24} {key:8} {state}")
            continue
        short.append((c, key, pl, years, wanted, deal, _L))
        print(f"   SHORT {name:24} {key:8} {state}  ->  {wanted} yr")

    if not short:
        print("\nEverybody is on a deal that outlives the rollover. Nothing to do.")
        return 0
    print(f"\n{len(short)} to fix.")

    if not a.apply:
        print("\nReport only; nothing was written. Re-run with --apply.")
        return 0

    # THE SAVE MUST BE QUIET. A write into a .dat the game has open is lost when the game saves
    # over it, and a write in the middle of a sim races the commissioner's own writes.
    if FBPB3.is_running():
        print("REFUSING: FBPB3 is open - close it first.")
        return 1
    if simweek.interrupted_run():
        print("REFUSING: there is an interrupted run to reconcile first.")
        return 1
    if not simweek._SIM_LOCK.acquire(blocking=False):
        print("REFUSING: something else holds the save lock.")
        return 1
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        touched = {}
        for c, key, pl, _years, wanted, deal, L in short:
            if key not in touched:
                src = ch.save_path(key)
                dest = BACKUPS / f"contracts-{key}-{stamp}" / "league.dat"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                print(f"   backed up {key} -> {dest}")
                touched[key] = L
            # KEEP THE MONEY HE ALREADY HAS. The salary is somebody's existing deal and is not
            # this tool's business; only the TERM is wrong. A character with nothing at all gets
            # the same token every other codec-written contract in the universe carries.
            salary = next((v for v in deal if v), a.salary)
            L.set_contract(pl, [salary] * wanted)
            print(f'   {c["first_name"]} {c["last_name"]:22} {key:8} '
                  f'-> {wanted} yr x ${salary:,}')
        for key, L in touched.items():
            L.save()
            print(f"   wrote {ch.save_path(key)}")
    finally:
        simweek._SIM_LOCK.release()

    # READ IT BACK OFF DISK. An in-memory set_contract that never reached the file is the exact
    # failure this is meant to prevent, so the check has to reopen the save rather than trust the
    # object that just wrote it.
    print("\nre-reading:")
    bad = 0
    for c, key, _pl, _years, wanted, _deal, _L in short:
        again = LeagueDat(ch.save_path(key))
        pl = again.find(f'{c["first_name"]} {c["last_name"]}', ch.codec_dob(c.get("game_dob")))
        got = sum(1 for v in again.contract_of(pl) if v)
        ok = got >= wanted
        bad += 0 if ok else 1
        print(f'   {"ok   " if ok else "FAIL "} {c["first_name"]} {c["last_name"]:22} {got} yr')
    if bad:
        print(f"\n{bad} did not take. The backups above are the way back.")
        return 1
    print("\nAll of them now outlive the rollover they are sitting in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
