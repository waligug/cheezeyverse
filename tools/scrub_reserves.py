"""Find dormant reserve slots that are not dormant, and put them back.

A reserve slot exists to be ignored: rated 3-12 so FBPB3's coach gives it no minutes until a
real person claims it. When a character leaves one - promoted, retired or deleted - the slot is
handed back its name and birthday, and until 2026-09-18 that was all: it kept the departed
character's RATINGS. The result is a filler who is secretly as good as the player who used to
hold him, taking rotation minutes from real characters, one more of them after every departure.

The code no longer does that (characters.reset_reserve, called from both the offseason and the
roster guard). This cleans up the ones already in the saves.

    python tools/scrub_reserves.py                # report only
    python tools/scrub_reserves.py --fix
    python tools/scrub_reserves.py --fix prep

A slot a live character currently holds is never touched - the store says which those are, and
this refuses to run if it cannot ask.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch                      # noqa: E402
from commissioner.codec.league_dat import POTENTIALS, RATINGS  # noqa: E402
from commissioner.universe import config as cfg                # noqa: E402
import json                                                    # noqa: E402

MANIFEST = json.loads((ROOT / "universe" / "manifest.json").read_text(encoding="utf-8"))

# A slot is "not dormant" if any ability rating is meaningfully above the reserve ceiling.
# The band is 3-12; a little headroom absorbs the game's own progression of a genuinely
# untouched filler over a season or two, so this only catches real contamination.
TOLERANCE = 8


def main():
    fix = "--fix" in sys.argv
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]

    from commissioner import simweek
    try:
        chars = simweek.store().characters()
    except Exception as exc:
        sys.exit(f"cannot read the characters from the store ({exc}). Refusing to run: without "
                 "them this could scrub a slot a live character is holding.")
    claimed = {(c["claimed_slot"].get("name"), c["claimed_slot"].get("dob"))
               for c in chars if c.get("claimed_slot")}
    if claimed:
        print(f"{len(claimed)} slot(s) currently held by live characters; those are left alone")

    ceiling = ch.RESERVE_RATINGS[1] + TOLERANCE
    total_bad = 0

    for key in keys:
        L = ch.open_league(key)
        rows = [r for r in MANIFEST["players"] if r["league"] == key and r["role"] == "reserve"]
        dirty = []
        for row in rows:
            if (row["name"], row["dob"]) in claimed:
                continue
            try:
                pl = L.find(row["name"], ch.codec_dob(row["dob"]))
            except Exception:
                continue          # not in the save under its manifest name: a live character has it
            worst = max(pl.values[f] for f in RATINGS if f not in ch.NOT_ABILITY)
            if worst > ceiling:
                pot = max(pl.values[f] for f in POTENTIALS)
                dirty.append((row, pl, worst, pot))

        print(f"\n{key}: {len(rows)} reserve slot(s), {len(dirty)} contaminated")
        for row, pl, worst, pot in dirty[:10]:
            print(f'   {row["name"]:<22} best rating {worst:>3}, best potential {pot:>3}'
                  f'  (dormant is <= {ch.RESERVE_RATINGS[1]})')
        if len(dirty) > 10:
            print(f"   ... and {len(dirty) - 10} more")
        total_bad += len(dirty)

        if dirty and fix:
            # One at a time, re-finding by name: rename splices the file and every offset
            # captured before it has moved.
            done = 0
            for row, _, _, _ in dirty:
                try:
                    pl = L.find(row["name"], ch.codec_dob(row["dob"]))
                    ch.reset_reserve(L, pl, row)
                    done += 1
                except Exception as exc:
                    print(f'   ! {row["name"]}: {exc}')
            L.save(backup_dir=ROOT / "backups")
            print(f"   scrubbed {done} back to dormant and saved")

    print()
    if not total_bad:
        print("every unclaimed reserve slot is properly dormant")
        return 0
    if fix:
        print(f"scrubbed {total_bad} slot(s). Now run: python tools/verify_save.py")
        return 0
    print(f"{total_bad} slot(s) need scrubbing. Re-run with --fix")
    return 1


if __name__ == "__main__":
    sys.exit(main())
