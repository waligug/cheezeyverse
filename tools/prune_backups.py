"""Age out routine save backups. Never touches the ones that are somebody's only way back.

`backups/` had grown to 3.3 GB across 481 folders with nothing pruning it, because every codec
write copies the save first and a Sim Week does that several times.

WHAT IS PROTECTED, and why it is protected by NAME rather than by age:

  *offseason*   taken immediately before a rollover, the one step that cannot be undone from
                the panel. The 2029 rollover crashed halfway and these were the only thing
                standing between a half-finished transition and a lost universe.
  *rehearsal*   the archived throwaway universes the season-end work was proved against, and
                the nearest thing we have to a codec fixture set.
  finished-*    anything holding a finished-season export.

Everything else is a routine snapshot of a save that still exists, and the newest few are worth
more than all the rest put together - a mistake is noticed in minutes, not months.

SAFE BY DEFAULT: it prints and deletes nothing unless `--apply` is given, and it always keeps
`--keep` of the most recent per league however old they are, so a quiet month cannot empty the
folder.

    python tools/prune_backups.py                 # what it would remove
    python tools/prune_backups.py --days 14       # a tighter window
    python tools/prune_backups.py --apply         # actually remove them
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"

# Protected by name. A folder matching any of these is never a candidate, at any age.
PROTECTED = ("offseason", "rehearsal", "finished")

# "20260921-204507-229931-offseason-CV_Prep" -> the league it belongs to
LEAGUE = re.compile(r"CV_(Prep|College|Pro)", re.I)


def league_of(name):
    m = LEAGUE.search(name)
    return m.group(1).lower() if m else "other"


def protected(name):
    low = name.lower()
    return any(word in low for word in PROTECTED)


def folder_size(path):
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def survey(days, keep):
    """(candidates, kept) - what would go and what would stay, newest first per league."""
    cutoff = time.time() - days * 86400
    by_league = {}
    for d in sorted(BACKUPS.iterdir()) if BACKUPS.exists() else []:
        if not d.is_dir() or protected(d.name):
            continue
        try:
            when = d.stat().st_mtime
        except OSError:
            continue
        by_league.setdefault(league_of(d.name), []).append((when, d))

    candidates, kept = [], []
    for _league, rows in sorted(by_league.items()):
        rows.sort(key=lambda r: r[0], reverse=True)       # newest first
        for i, (when, d) in enumerate(rows):
            # The newest `keep` of each league survive whatever their age: a mistake is noticed
            # in minutes, and an empty folder after a quiet month helps nobody.
            (kept if i < keep or when >= cutoff else candidates).append((when, d))
    return candidates, kept


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="keep everything newer than this")
    ap.add_argument("--keep", type=int, default=10, help="newest per league to keep regardless")
    ap.add_argument("--apply", action="store_true", help="actually delete")
    args = ap.parse_args(argv)

    if not BACKUPS.exists():
        print(f"no {BACKUPS}")
        return 0

    protected_dirs = [d for d in BACKUPS.iterdir() if d.is_dir() and protected(d.name)]
    candidates, kept = survey(args.days, args.keep)
    freed = sum(folder_size(d) for _w, d in candidates)

    print(f"{BACKUPS}")
    print(f"  protected (never touched): {len(protected_dirs)}")
    print(f"  keeping                  : {len(kept)} "
          f"(newer than {args.days}d, or the newest {args.keep} per league)")
    print(f"  would remove             : {len(candidates)}  ~{freed / 1e9:.2f} GB")
    for when, d in candidates[:5]:
        print(f"     {time.strftime('%Y-%m-%d', time.localtime(when))}  {d.name}")
    if len(candidates) > 5:
        print(f"     ... and {len(candidates) - 5} more")

    if not args.apply:
        print("\nnothing was deleted. Re-run with --apply to remove them.")
        return 0

    removed = 0
    for _when, d in candidates:
        try:
            shutil.rmtree(d)
            removed += 1
        except OSError as exc:
            print(f"  ! could not remove {d.name}: {exc}")
    print(f"\nremoved {removed} folder(s), about {freed / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
