"""Release players the game's own AI signed onto our rosters during league creation.

Starting Stage is Preseason, so between building the league and the first save FBPB3 lets its AI
teams sign from the free-agent pool it generated. Those signings push a roster past 15, which makes
somebody inactive and breaks the arithmetic the reserve-slot ceiling depends on.

Anyone rostered who is not in `universe/manifest.json` is an intruder and goes back to free agency.

  python tools/release_intruders.py            # all three saves
  python tools/release_intruders.py college
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.codec.league_dat import LeagueDat  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
BACKUPS = ROOT / "backups"


def tidy(key, dry_run=False):
    spec = cfg.BY_KEY[key]
    path = DOCS / "leaguedata" / spec.save_name / "league.dat"
    manifest = json.loads((ROOT / "universe" / "manifest.json").read_text())
    ours = {(p["name"], p["dob"]) for p in manifest["players"] if p["league"] == key}

    L = LeagueDat(path)
    intruders = [p for p in L.players if p.values["Team"] >= 1 and (p.name, p.dob) not in ours]
    print(f"{key}: {len(intruders)} intruder(s) on rosters"
          + (": " + ", ".join(f"{p.name} (team {p.values['Team']})" for p in intruders[:6]) if intruders else ""))
    if not intruders or dry_run:
        return len(intruders)

    for p in intruders:
        L.release(L.find(p.name, p.dob))
    L.save(backup_dir=BACKUPS)

    check = LeagueDat(path)
    sizes = {t: len(v["ids"]) for t, v in check.teams().items()}
    bad = {t: n for t, n in sizes.items() if n != spec.roster_size}
    if bad:
        raise SystemExit(f"{key}: rosters still wrong after release: {bad}")
    print(f"   released; every team is now {spec.roster_size}")
    return len(intruders)


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]
    dry = "--dry-run" in sys.argv
    for k in keys:
        tidy(k, dry_run=dry)
