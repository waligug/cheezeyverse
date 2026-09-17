"""Count rostered vs free-agent players in a save, without needing id recovery.

  python tools/quick_roster_check.py CV_Prep
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.codec import league_dat as ld  # noqa: E402

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")


def check(save):
    path = DOCS / "leaguedata" / save / "league.dat"
    orig = ld.LeagueDat._assign_ids
    ld.LeagueDat._assign_ids = lambda self, players: None
    try:
        L = ld.LeagueDat(path)
    finally:
        ld.LeagueDat._assign_ids = orig
    teams = Counter(p.values["Team"] for p in L.players)
    rostered = sum(n for t, n in teams.items() if t >= 1)
    print(f"{save}: {len(L.players)} players, {rostered} rostered across {sum(1 for t in teams if t >= 1)} teams, "
          f"{teams.get(-1, 0)} FA, {teams.get(-2, 0)} draft")
    print("   per team:", sorted((t, n) for t, n in teams.items() if t >= 1))
    return rostered


if __name__ == "__main__":
    for s in sys.argv[1:] or ["CV_Prep"]:
        check(s)
