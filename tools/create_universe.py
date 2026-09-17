"""Create the three Cheezeyverse saves by driving FBPB3's New Game wizard.

  python tools/create_universe.py            # all three
  python tools/create_universe.py prep       # just one

Each save holds exactly one league. Existing saves are refused rather than overwritten.
"""
import shutil
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.driver.fbpb3 import DOCS, FBPB3  # noqa: E402
from commissioner.driver.newgame import PRESTIGE, NewGame, league_rows  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402


def create(key, force=False):
    spec = cfg.BY_KEY[key]
    target = DOCS / "leaguedata" / spec.save_name
    if target.exists():
        if not force:
            raise SystemExit(f"{spec.save_name} already exists; pass --force to replace it")
        shutil.rmtree(target)
    league_file = f"CV-{key.capitalize()}.csv"
    roster_file = f"CV-{key.capitalize()}-Rosters.csv"
    rows = league_rows(DOCS)
    if league_file not in rows:
        raise SystemExit(f"{league_file} is not in the Available Leagues list; run generate_universe.py")

    FBPB3.kill()
    g = FBPB3().launch()
    try:
        NewGame(g).create(save_name=spec.save_name, first_season=cfg.START_YEAR,
                          league_row=rows.index(league_file), prestige=PRESTIGE[spec.prestige - 1],
                          roster_file=roster_file, starting_stage="Preseason")
    finally:
        try:
            g.exit_game(save=False)
        except Exception:
            FBPB3.kill()
    time.sleep(1)
    print(f"created {spec.save_name} ({spec.name})")
    return spec.save_name


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    for k in args or [s.key for s in cfg.LEAGUES]:
        create(k, force=force)
