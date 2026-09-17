"""probe_step.py <name> [x y] [wait]: optionally click a window-relative point, then dump the screen."""
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402
from tools.probe_newgame import dump  # noqa: E402

name = sys.argv[1]
g = FBPB3().launch()
if len(sys.argv) >= 4:
    g.click((int(sys.argv[2]), int(sys.argv[3])), float(sys.argv[4]) if len(sys.argv) > 4 else 2.5)
time.sleep(1)
dump(g, name)
for w in g.app.windows(visible_only=True):
    if w.class_name() not in ("ThunderRT6MDIForm", "ThunderRT6Main", "Static"):
        print("EXTRA WINDOW", w.class_name(), repr(w.window_text()))
