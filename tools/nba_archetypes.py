"""Turn FBPB3's own real-NBA roster file into the builder's archetype list.

WHY THIS FILE EXISTS. "Who would I play like?" is only answerable if the comparison is on the
same scale as the character being built, and it is: FBPB3 ships
`PlayerFiles/2015 Season Start.csv`, 579 real NBA players rated in exactly the fields the codec
writes - DeMarcus Cousins at InsideScoring 98 and JumpShot 26, Steven Adams at 36 and 14. No
scraping, no mapping, no second rating scale to reconcile.

STYLE, NOT LEVEL. A fifteen-year-old with everything in the twenties is not "most like" the
worst player in the NBA, which is what a plain nearest-neighbour on raw ratings would answer -
every character would match the same handful of end-of-bench players and the feature would be
worthless. So each player is reduced to a SHAPE: his ratings centred and scaled against his own
average, which says what he is good at RELATIVE TO HIMSELF. Cousins then reads as "scores
inside, cannot shoot, rebounds" whether he is rated 98 or 45, and so does a prep character built
the same way.

    python tools/nba_archetypes.py            # writes site/data/nba-archetypes.json
    python tools/nba_archetypes.py --show "Johnny Gartholomew"
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# One implementation of the matching, not two. The commissioner needs it at runtime for the
# Discord card, this file needs it to build the data in the first place, and two copies of a
# similarity measure is how the site and the save came to disagree about potentials.
from commissioner.archetypes import SHAPE, most_like, shape_of, similarity  # noqa: E402
SOURCE = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\PlayerFiles"
              r"\2015 Season Start.csv")
DEST = ROOT / "site" / "data" / "nba-archetypes.json"

def load_source(path=SOURCE):
    rows = []
    with open(path, encoding="latin-1", newline="") as fh:
        for row in csv.DictReader(fh):
            first, last = (row.get("FirstName") or "").strip(), (row.get("LastName") or "").strip()
            if not last:
                continue
            shape = shape_of(row)
            if shape is None:
                continue
            rows.append({
                "name": f"{first} {last}".strip(),
                "team": (row.get("Team") or row.get("Te") or "").strip(),
                "pos": (row.get("Position") or "").strip(),
                "ht": int(row.get("Height") or 0),
                "wt": int(row.get("Weight") or 0),
                "shape": [round(v, 4) for v in shape],
                # a few raw numbers, so the builder can say WHY he is the match
                "top": sorted(((k, int(row.get(k) or 0)) for k in SHAPE),
                              key=lambda kv: -kv[1])[:3],
            })
    return rows


def main():
    if not SOURCE.exists():
        print(f"no source roster at {SOURCE}")
        return 1
    players = load_source()
    if "--show" in sys.argv:
        name = sys.argv[sys.argv.index("--show") + 1]
        sys.path.insert(0, str(ROOT))
        from commissioner import store
        c = next(x for x in store.characters() if f'{x["first_name"]} {x["last_name"]}' == name)
        print(f"{name} plays most like:")
        for m in most_like(c.get("ratings") or {}, 5, pool=players):
            best = ", ".join(f"{k} {v}" for k, v in m["top"])
            print(f'  {m["score"]:.3f}  {m["name"]:<22} {m["pos"]:<3} {m["team"]:<4} ({best})')
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps({
        "source": "FBPB3 PlayerFiles/2015 Season Start.csv",
        "fields": SHAPE,
        "players": players,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"{len(players)} real players -> {DEST} ({DEST.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
