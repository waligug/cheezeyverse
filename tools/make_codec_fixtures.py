"""Capture a save and the game's own CSV of it, so the codec's READING can be checked.

WHAT IS AND IS NOT ALREADY COVERED. `tests/test_codec.py` proves the codec's WRITES round-trip -
rating edits, team swaps, release and sign, renames - against a disposable copy of the live save,
and those run on every machine. That is the half with no undo and it is guarded.

What has never run here is the other half: does the codec READ a save the same way the game does?
That comparison needs a save paired with the game's own export of it, and the only such pair in
the repo is the "chung" one, whose .dat files are gitignored and have never existed on SERVERPC.
So `test_baseline_matches_game_exports` and `test_aged_save_matches_game_exports` have skipped
here since the machine was set up - on the one box that ships codec changes.

This builds the missing pair from our own universe instead of the vanished Chung one:

    fixtures/saves/<name>/league.dat   a copy of the save, taken with the game closed
    fixtures/exports/<name>.csv        FBPB3's own Players export of that same save

Take TWO, and the names the tests already expect are the right ones to use:
  * a young league, for the fresh-save shape (ratings block preceded by zeros)
  * an aged one - several seasons in, with per-season archive rows, non-contiguous ids,
    retirements and rookies. CV_Pro after four seasons is exactly that.

IT DRIVES THE GAME, so it takes the save lock and refuses if anything else is running. It writes
nothing back: the save is copied out, and the export is a menu action that produces a new file.

    python tools/make_codec_fixtures.py --save CV_Pro --name cv-pro-aged
    python tools/make_codec_fixtures.py --save CV_Prep --name cv-prep --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import simweek                                   # noqa: E402
from commissioner.driver.fbpb3 import FBPB3, DOCS                  # noqa: E402

SAVES = ROOT / "fixtures" / "saves"
EXPORTS = ROOT / "fixtures" / "exports"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--save", default="CV_Pro", help="save folder name, e.g. CV_Pro")
    ap.add_argument("--name", default=None, help="fixture name (default: derived from --save)")
    ap.add_argument("--dry-run", action="store_true", help="say what it would do")
    args = ap.parse_args(argv)

    name = args.name or args.save.lower().replace("_", "-")
    src = DOCS / "leaguedata" / args.save / "league.dat"
    if not src.exists():
        print(f"no such save: {src}")
        return 1

    dest_dat = SAVES / name / "league.dat"
    dest_csv = EXPORTS / f"{name}.csv"
    print(f"save   {src}")
    print(f"  -> {dest_dat}")
    print(f"  -> {dest_csv}")
    if args.dry_run:
        print("\ndry run; nothing was written")
        return 0

    if FBPB3.is_running():
        print("REFUSING: FBPB3 is open - close it first, the save must be quiet to copy")
        return 1
    if simweek.interrupted_run():
        print("REFUSING: there is an interrupted run to reconcile first")
        return 1
    if not simweek._SIM_LOCK.acquire(blocking=False):
        print("REFUSING: something else holds the save lock")
        return 1
    try:
        # THE SAVE IS READ FIRST, with the game closed, so the .dat and the .csv describe the
        # same moment - exporting first would let anything the game does on the way out land
        # between them.
        #
        # BUT IT IS STAGED, NOT INSTALLED. The export can fail; it is a GUI driven by simulated
        # clicks. Writing the save into place first would leave a new league.dat beside the
        # PREVIOUS run's .csv, and a fixture whose two halves disagree is worse than none: every
        # later failure gets argued about instead of fixed. Both halves land together or neither
        # does.
        # STAGE THE .dat, do not install it. The export can fail - it is a GUI driven by
        # simulated clicks - and writing the save into place first would leave a new league.dat
        # beside the PREVIOUS run's .csv. That is the disagreeing pair this file's own comment
        # forbids, and it is worse than having no fixture: every later failure gets argued about
        # instead of fixed. Both halves are moved into place together, or neither is.
        staged_dat = dest_dat.with_suffix(".dat.staged")
        staged_dat.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, staged_dat)
        print(f"staged {staged_dat.stat().st_size:,} bytes")

        try:
            game = FBPB3().launch()
            try:
                game.load_save(args.save)
                produced = game.export_players(name)
            finally:
                try:
                    game.exit_game(save=False)
                except Exception:                                   # noqa: BLE001
                    FBPB3.kill()
        except Exception as exc:                                    # noqa: BLE001
            staged_dat.unlink(missing_ok=True)
            print(f"export failed, so nothing was installed: {exc}")
            print("the previous fixture pair, if any, is untouched.")
            return 1

        EXPORTS.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, dest_csv)
        staged_dat.replace(dest_dat)
        print(f"exported {dest_csv.stat().st_size:,} bytes from {produced}")
    finally:
        simweek._SIM_LOCK.release()

    rows = sum(1 for _ in dest_csv.open(encoding="latin-1")) - 1
    print(f"\n{name}: save + {rows} exported player rows")
    print("Point test_codec at it, or add it beside the chung pair the two skipped tests want.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
