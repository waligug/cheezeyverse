"""Put a league.dat back the way it was. The undo button.

Every codec write copies the file into `backups/` first, so there are hundreds of them - but
until now nothing could put one back, which made them a pile of files rather than a safety net.
"Oh no" and "undo" are very different situations and this is the whole difference.

Backups written before 2026-09-18 are folders named only by the clock, and every league's file
is called `league.dat`, so they carry no record of which save they came from. This identifies
them by reading the file: team count and roster size are enough to tell Prep, College and Pro
apart, and the date is read off the folder name.

    python tools/restore_backup.py                      # list what is available
    python tools/restore_backup.py --league prep        # just that league's backups
    python tools/restore_backup.py --restore 20260917-233835-CV_Prep
    python tools/restore_backup.py --restore latest --league prep

Restoring is itself backed up: the file being replaced is copied to `backups/pre-restore-...`
first, so a restore can be undone by restoring that. The tool refuses to run while FBPB3 is
open, because the game holds league.dat in memory and writes it back when it closes - which
would silently undo the restore and leave you certain the tool is broken.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch                       # noqa: E402
from commissioner.codec.league_dat import CodecError, LeagueDat  # noqa: E402
from commissioner.universe import config as cfg                  # noqa: E402

BACKUPS = ROOT / "backups"


def game_is_running():
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe"],
                             capture_output=True, text=True)
        return "FBPB3.exe" in out.stdout
    except Exception:
        return False


def fingerprint(path):
    """Which league is this? Read it rather than trust the folder name."""
    try:
        L = LeagueDat(path)
    except (CodecError, OSError) as exc:
        return None, f"will not parse ({exc})"
    teams = L.teams()
    players = len(L.players)
    sizes = {len(v["ids"]) for v in teams.values()}
    for spec in cfg.LEAGUES:
        if len(teams) == len(spec.teams):
            # Prep and College have the same team count; player count separates them.
            live = ch.save_path(spec.key)
            if live.exists():
                try:
                    if abs(players - len(LeagueDat(live).players)) <= 12:
                        return spec.key, f"{len(teams)} teams, {players} players"
                except CodecError:
                    pass
    guess = next((s.key for s in cfg.LEAGUES if len(s.teams) == len(teams)), None)
    return guess, f"{len(teams)} teams, {players} players, rosters {sorted(sizes)}"


def when(folder):
    stamp = folder.name.split("-")[0] + folder.name.split("-")[1] if "-" in folder.name else folder.name
    try:
        return datetime.strptime(folder.name[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return datetime.fromtimestamp(folder.stat().st_mtime)


def candidates():
    rows = []
    for folder in sorted(BACKUPS.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        dat = folder / "league.dat"
        if not dat.exists():
            continue
        named = None
        for spec in cfg.LEAGUES:
            if folder.name.endswith(spec.save_name):
                named = spec.key
        rows.append({"id": folder.name, "path": dat, "named": named, "at": when(folder),
                     "size": dat.stat().st_size})
    return rows


def listing(args):
    rows = candidates()
    if not rows:
        print(f"no backups under {BACKUPS}")
        return 0
    shown = 0
    print(f"{'backup id':<34}{'league':<9}{'when':<18}{'size':>12}")
    for row in rows:
        league = row["named"]
        detail = ""
        if league is None and (args.identify or args.league):
            league, detail = fingerprint(row["path"])
        if args.league and league != args.league:
            continue
        print(f'{row["id"]:<34}{(league or "?"):<9}{row["at"]:%Y-%m-%d %H:%M}   '
              f'{row["size"]:>11,}' + (f"  {detail}" if detail else ""))
        shown += 1
        if shown >= args.limit:
            print(f"... {len(rows) - shown} more (use --limit)")
            break
    if not args.identify and not args.league:
        print("\nbackups named only by the clock show '?'. Add --identify to read each one "
              "(slower), or --league prep to filter.")
    print("\nrestore with:  python tools/restore_backup.py --restore <backup id>")
    return 0


def restore(args):
    if game_is_running():
        sys.exit("FBPB3 is open. It holds league.dat in memory and writes it back when it "
                 "closes, which would silently undo this restore. Close the game first.")

    rows = candidates()
    if args.restore == "latest":
        if not args.league:
            sys.exit("--restore latest needs --league, or there is no way to know latest of what")
        pool = [r for r in rows if (r["named"] or fingerprint(r["path"])[0]) == args.league]
        if not pool:
            sys.exit(f"no backup found for {args.league}")
        chosen = pool[0]
    else:
        chosen = next((r for r in rows if r["id"] == args.restore), None)
        if not chosen:
            sys.exit(f"no backup called {args.restore!r}. Run without --restore to list them.")

    league = args.league or chosen["named"] or fingerprint(chosen["path"])[0]
    if not league:
        sys.exit(f"cannot tell which league {chosen['id']} belongs to; pass --league")

    # Prove the backup is sound BEFORE touching the live file. Restoring a corrupt backup over
    # a working save would turn one bad day into two.
    found, detail = fingerprint(chosen["path"])
    if found is None:
        sys.exit(f"{chosen['id']} does not parse - refusing to restore it ({detail})")
    if found != league:
        sys.exit(f"{chosen['id']} looks like {found}, not {league} ({detail}). "
                 "Pass --force if you are certain.") if not args.force else None

    live = ch.save_path(league)
    spec = cfg.BY_KEY[league]
    print(f'restoring {chosen["id"]}  ->  {spec.save_name}')
    print(f'   backup : {detail}, {chosen["size"]:,} bytes, {chosen["at"]:%Y-%m-%d %H:%M}')
    if live.exists():
        now, now_detail = fingerprint(live)
        print(f'   current: {now_detail}, {live.stat().st_size:,} bytes')

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    # The restore is itself undoable.
    if live.exists():
        safety = BACKUPS / f'pre-restore-{datetime.now():%Y%m%d-%H%M%S}-{spec.save_name}'
        safety.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, safety / "league.dat")
        print(f"   the file being replaced is saved as {safety.name}")

    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(chosen["path"], live)

    after, after_detail = fingerprint(live)
    if after != league:
        sys.exit(f"restored, but the file now reads as {after} - something is wrong, and the "
                 "previous file is in backups/pre-restore-*")
    print(f"   restored and verified: {after_detail}")
    print("\nnow run:  python tools/verify_save.py")
    return 0


def main():
    ap = argparse.ArgumentParser(description="List and restore league.dat backups")
    ap.add_argument("--restore", metavar="ID", help='backup id, or "latest" with --league')
    ap.add_argument("--league", choices=[s.key for s in cfg.LEAGUES])
    ap.add_argument("--identify", action="store_true",
                    help="read every backup to work out which league it is (slower)")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="restore even if the backup looks like a different league")
    args = ap.parse_args()
    return restore(args) if args.restore else listing(args)


if __name__ == "__main__":
    sys.exit(main())
