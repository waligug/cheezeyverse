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
from commissioner import characters as ch
from commissioner.universe import config as cfg  # noqa: E402

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")


def verify(key):
    spec = cfg.BY_KEY[key]
    path = DOCS / "leaguedata" / spec.save_name / "league.dat"
    manifest = json.loads((ROOT / "universe" / "manifest.json").read_text())
    want = [p for p in manifest["players"] if p["league"] == key]
    reserves = [p for p in want if p["role"] == "reserve"]

    # A slot a character has claimed no longer answers to its manifest name - he does. Swap the
    # claimed rows for the characters holding them, or every created player reads as a missing one.
    try:
        from commissioner import simweek
        for c in simweek.store().characters():
            slot = c.get("claimed_slot") or {}
            # DECLARED IS STILL HERE. A character who has declared for the draft has not left
            # his level - he is playing it out - and he still holds the reserve row he was
            # stamped onto, renamed to him. Skipping him meant this went looking for the
            # filler's original name, found nothing, and reported a seat as lost: on
            # 2026-09-22 college read "47 of 48 findable, first missing Teddy Regner", which is
            # the seat Dodger Manson is sitting in. Same blind spot that once hid a declared
            # character from the draft itself.
            if not slot or c.get("status") not in ("active", "declared"):
                continue
            claimed = {"league": slot.get("league"), "team": slot.get("team"), "role": "reserve",
                       "name": f'{c["first_name"]} {c["last_name"]}',
                       "dob": ch.codec_dob(c.get("game_dob") or slot.get("dob")),
                       "position": c.get("position"), "uniform": slot.get("uniform")}
            for pool in (want, reserves):
                for i, row in enumerate(pool):
                    if row["name"] == slot.get("name") and row["dob"] == slot.get("dob")                             and row["league"] == c.get("league"):
                        pool[i] = claimed
    except Exception:
        pass

    L = LeagueDat(path)
    stamp = L.season_day()
    evolved = bool(stamp and int(stamp[1]) > int(cfg.START_YEAR))
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
    roster_ok = (all(spec.roster_size - 1 <= n <= 20 for n in sizes) if evolved
                 else set(sizes) == {spec.roster_size})
    check(f"{key}: rosters are valid" if evolved else f"{key}: every roster is {spec.roster_size}",
          roster_ok, dict(sizes))
    matched_names = sum(1 for w in want if w["name"] in by_name)
    check(f"{key}: original population accounted for" if evolved else f"{key}: our players imported",
          evolved or matched_names == len(want),
          f"{matched_names} of {len(want)} remain; retirements are expected" if evolved else
          f"{matched_names} of {len(want)} matched by name")
    check(f"{key}: birthdays restored" if not evolved else f"{key}: surviving original birthdays match",
          evolved or len(found_all) == len(want),
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
