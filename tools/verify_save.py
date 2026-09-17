"""Verify a freshly created Cheezeyverse save against the generator's manifest.

  python tools/verify_save.py prep

Checks the one thing that silently goes wrong: whether FBPB3 actually imported our roster file.
If Initial Player Source was left on Fictional the save looks perfectly healthy - right team count,
full rosters - but not one of our reserve slots exists, and no character could ever be created.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.codec.league_dat import LeagueDat  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")


def verify(key):
    spec = cfg.BY_KEY[key]
    path = DOCS / "leaguedata" / spec.save_name / "league.dat"
    manifest = json.loads((ROOT / "universe" / "manifest.json").read_text())
    want = [p for p in manifest["players"] if p["league"] == key]
    reserves = [p for p in want if p["role"] == "reserve"]

    L = LeagueDat(path)
    teams = L.teams()
    by_name_dob = {(p.name, p.dob): p for p in L.players}
    by_name = {p.name for p in L.players}
    found = [r for r in reserves if (r["name"], r["dob"]) in by_name_dob]
    found_all = [p for p in want if (p["name"], p["dob"]) in by_name_dob]
    sizes = Counter(len(v["ids"]) for v in teams.values())
    abbrevs = {t.abbrev for t in spec.teams}
    on_team = Counter()
    for p in L.players:
        on_team[p.T1] += 1

    ok = True
    def check(label, cond, detail=""):
        nonlocal ok
        print(f"{'PASS' if cond else 'FAIL'}  {label} {detail}")
        ok = ok and cond

    check(f"{key}: file parses", len(L.players) > 0, f"{len(L.players)} players")
    check(f"{key}: team count", len(teams) == len(spec.teams), f"{len(teams)} of {len(spec.teams)}")
    check(f"{key}: every roster is {spec.roster_size}", set(sizes) == {spec.roster_size}, dict(sizes))
    check(f"{key}: our players imported", sum(1 for w in want if w["name"] in by_name) == len(want),
          f"{sum(1 for w in want if w['name'] in by_name)} of {len(want)} matched by name")
    check(f"{key}: birthdays restored", len(found_all) == len(want),
          f"{len(found_all)} of {len(want)} matched by name+DOB (run tools/stamp_dobs.py if this fails)")
    check(f"{key}: every reserve slot is findable", len(found) == len(reserves),
          f"{len(found)} of {len(reserves)}")

    if found:
        sample = found[0]
        pl = by_name_dob[(sample["name"], sample["dob"])]
        print(f"      sample reserve {pl.name} dob {pl.dob} team {pl.values['Team']} "
              f"Inside {pl.values['InsideScoring']} PotInside {pl.values['PotInside']}")
    missing = [r for r in reserves if (r["name"], r["dob"]) not in by_name_dob]
    if missing:
        print(f"      first missing reserves: {[m['name'] for m in missing[:5]]}")
    print(f"      abbrevs expected: {len(abbrevs)}")
    return ok


if __name__ == "__main__":
    keys = sys.argv[1:] or ["prep", "college", "pro"]
    results = {k: verify(k) for k in keys}
    print()
    print("ALL PASS" if all(results.values()) else "FAILED: " + ", ".join(k for k, v in results.items() if not v))
    sys.exit(0 if all(results.values()) else 1)
