"""Freeze a save's roster and contract state to JSON, so a before/after diff is exact.

Built for the Finances experiment. "Did anybody get released?" is not a question to answer by
looking at a screen: with Full Finances the game releases contract-less players AS IT LOADS, so
the damage is done before anything is visible, and it shows up as a team quietly being one man
short rather than as an error.

    python tools/roster_snapshot.py CV_FinTest before.json
    python tools/roster_snapshot.py CV_FinTest after.json
    python tools/roster_snapshot.py --diff before.json after.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import LeagueDat  # noqa: E402
from commissioner.driver.fbpb3 import DOCS  # noqa: E402


def snapshot(save):
    L = LeagueDat(DOCS / "leaguedata" / save / "league.dat")
    teams = {str(t): info["size"] for t, info in L.teams().items()}
    people = {}
    for p in L.players:
        people[f"{p.name}|{p.dob}"] = {
            "team": p.values["Team"],
            "inactive": p.values["Inactive"],
            "contract": p.contract,
        }
    return {"save": save, "players": len(L.players), "teams": teams, "people": people}


def diff(a, b):
    bad = 0
    print(f"players: {a['players']} -> {b['players']}")
    if a["players"] != b["players"]:
        print("   !! the record count changed")
        bad += 1

    for t in sorted(set(a["teams"]) | set(b["teams"]), key=lambda x: int(x)):
        x, y = a["teams"].get(t), b["teams"].get(t)
        if x != y:
            print(f"   !! team {t} roster {x} -> {y}")
            bad += 1
    if not bad:
        print("every team has the same number of players as before")

    gone = [k for k in a["people"] if k not in b["people"]]
    new = [k for k in b["people"] if k not in a["people"]]
    if gone:
        print(f"   !! {len(gone)} players vanished from the file: {gone[:5]}")
        bad += 1
    if new:
        print(f"   !! {len(new)} players appeared: {new[:5]}")
        bad += 1

    released, signed, money = [], [], []
    for k, av in a["people"].items():
        bv = b["people"].get(k)
        if not bv:
            continue
        if av["team"] > 0 and bv["team"] <= 0:
            released.append((k, av["team"], av["contract"][0]))
        elif av["team"] <= 0 < bv["team"]:
            signed.append((k, bv["team"]))
        elif av["team"] != bv["team"]:
            signed.append((k, f'{av["team"]}->{bv["team"]}'))
        if av["contract"] != bv["contract"]:
            money.append((k, av["contract"][:2], bv["contract"][:2]))

    print(f"\nRELEASED off a roster : {len(released)}")
    for k, t, c in released[:12]:
        print(f'   {k.split("|")[0]:26} was on team {t:>3}, contract {c}')
    if len(released) > 12:
        print(f"   ...and {len(released) - 12} more")
    print(f"moved or signed       : {len(signed)}")
    for k, t in signed[:8]:
        print(f'   {k.split("|")[0]:26} -> {t}')
    print(f"contract changed      : {len(money)}")
    for k, x, y in money[:8]:
        print(f'   {k.split("|")[0]:26} {x} -> {y}')
    return bad or released


def main(argv):
    if argv and argv[0] == "--diff":
        a = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        b = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        return 1 if diff(a, b) else 0
    save, out = argv[0], argv[1]
    snap = snapshot(save)
    Path(out).write_text(json.dumps(snap, indent=1), encoding="utf-8")
    rostered = sum(1 for v in snap["people"].values() if v["team"] > 0)
    bare = sum(1 for v in snap["people"].values() if v["team"] > 0 and not any(v["contract"]))
    print(f"{save}: {snap['players']} players, {rostered} rostered, "
          f"{bare} rostered with no contract -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
