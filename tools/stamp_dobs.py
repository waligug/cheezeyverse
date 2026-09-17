"""Restore the true birthdays FBPB3 clamped away during the player-file import.

The importer refuses to create a player younger than 16: it keeps the day and month from the CSV
but pulls the birth **year** forward until the player is 16 at the league's first season. For the
Prep league that turns every 14- and 15-year-old into a 16-year-old, which is the whole premise
gone.

The binary editor has no such floor (Phase 0 confirmed the game's own export reporting a rostered,
active 12-year-old), so this walks the save and writes each player's manifest DOB back. Run it once
per save, right after the save is created and before anything is simulated.

  python tools/stamp_dobs.py prep
  python tools/stamp_dobs.py            # all three
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.codec.league_dat import CodecError, LeagueDat  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
BACKUPS = ROOT / "backups"


def stamp(key, dry_run=False):
    spec = cfg.BY_KEY[key]
    path = DOCS / "leaguedata" / spec.save_name / "league.dat"
    manifest = json.loads((ROOT / "universe" / "manifest.json").read_text())
    want = {p["name"]: p for p in manifest["players"] if p["league"] == key}

    L = LeagueDat(path)
    by_name = {}
    for p in L.players:
        by_name.setdefault(p.name, []).append(p)

    changed, ambiguous, missing = 0, [], []
    for name, entry in want.items():
        hits = by_name.get(name, [])
        if not hits:
            missing.append(name)
            continue
        if len(hits) > 1:
            # the import keeps day and month, so the right record is the one that still matches them
            month, day, year = (int(v) for v in entry["dob"].split("/"))
            hits = [h for h in hits if h.values["BirthMonth"] == month and h.values["BirthDay"] == day]
            if len(hits) != 1:
                ambiguous.append(name)
                continue
        pl = hits[0]
        month, day, year = (int(v) for v in entry["dob"].split("/"))
        if (pl.values["BirthMonth"], pl.values["BirthDay"], pl.values["BirthYear"]) == (month, day, year):
            continue
        if not dry_run:
            L.set(pl, "BirthMonth", month)
            L.set(pl, "BirthDay", day)
            L.set(pl, "BirthYear", year)
        changed += 1

    print(f"{key}: {changed} birthdays to restore, {len(ambiguous)} ambiguous, {len(missing)} missing")
    if ambiguous:
        print("   ambiguous:", ambiguous[:5])
    if missing:
        print("   missing:", missing[:5])
    if dry_run or not changed:
        return changed

    L.save(backup_dir=BACKUPS)
    check = LeagueDat(path)
    ages = Counter()
    bad = []
    for name, entry in want.items():
        month, day, year = (int(v) for v in entry["dob"].split("/"))
        hits = [p for p in check.players if p.name == name
                and (p.values["BirthMonth"], p.values["BirthDay"]) == (month, day)]
        if not hits:
            bad.append(name)
            continue
        if hits[0].values["BirthYear"] != year:
            bad.append(name)
        ages[cfg.START_YEAR - hits[0].values["BirthYear"]] += 1
    if bad:
        raise CodecError(f"{key}: {len(bad)} birthdays did not stick, e.g. {bad[:5]}")
    print(f"   verified, ages now {dict(sorted(ages.items()))}")
    return changed


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]
    dry = "--dry-run" in sys.argv
    for k in keys:
        stamp(k, dry_run=dry)
