"""Run FBPB3's HTML Output for each league, then skin and stage the result under site/leagues/.

  python tools/generate_sites.py            # all three
  python tools/generate_sites.py prep pro
"""
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402
from commissioner.publish.publish import publish  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

keys = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]
FBPB3.kill()
g = FBPB3().launch()
for key in keys:
    save = cfg.BY_KEY[key].save_name
    print(f"loading {save} (row {g.save_rows().index(save)})", flush=True)
    g.load_save(save, wait=30)
    out = g.html_output(save)
    print(f"  {key}: {len(list(out.rglob('*.htm')))} pages -> {out}", flush=True)
g.exit_game(save=False)
time.sleep(1)
for row in publish(keys):
    print(f'skinned {row["league"]:8} {row["pages"]:4} pages -> {row["path"]}')
