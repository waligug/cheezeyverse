"""Preview or repair Training Camps regression from pre/post-camp save backups.

Usage:
    python tools/repair_rollover_sheets.py prep BACKUP/league.dat
    python tools/repair_rollover_sheets.py prep PRE/league.dat --post-camp POST/league.dat --run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import seasonflow, simweek  # noqa: E402
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402
from commissioner.saveguard import SAVE_LOCK  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("league", choices=("prep", "college", "pro"))
    parser.add_argument("backup", type=Path)
    parser.add_argument("--post-camp", type=Path,
                        help="captured post-camp save; its positive gains will be retained")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    backup = args.backup.resolve()
    if not backup.is_file():
        parser.error(f"backup does not exist: {backup}")
    post_camp = args.post_camp.resolve() if args.post_camp else None
    if post_camp and not post_camp.is_file():
        parser.error(f"post-camp backup does not exist: {post_camp}")
    if not SAVE_LOCK.acquire(False):
        raise SystemExit("a sim/offseason/backup is using the saves")
    try:
        if FBPB3.is_running():
            raise SystemExit("FBPB3 is running; no save was opened")
        if simweek.interrupted_run():
            raise SystemExit("an interrupted run must be reconciled first")
        rows = seasonflow.restore_character_sheets(
            simweek.store(), args.league, backup, dry_run=not args.run,
            post_camp_path=post_camp)
    finally:
        SAVE_LOCK.release()
    for row in rows:
        changes = row["changed"]
        print(f'{row["name"]}: {len(changes)} field(s) changed')
        for field, (was, wanted) in changes.items():
            print(f"  {field}: {was} -> {wanted}")
    print(("REPAIRED" if args.run else "PREVIEW") + f": {sum(bool(r['changed']) for r in rows)} player(s)")


if __name__ == "__main__":
    main()
