"""Load Chung_test and export the player file. Nothing else.

The smaller of the two Phase 0 probes, kept for the same reason as run_age_test.py: both were
deleted once as superseded and restored as an unintended removal.

IT NEEDS A `Chung_test` SAVE, which SERVERPC does not have. Use tools/make_codec_fixtures.py
against CV_Prep or CV_Pro instead.
"""
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402

g = FBPB3()
FBPB3.kill()
time.sleep(2)
g.launch()
try:
    g.load_save("Chung_test")
    print("loaded")
    print(g.export_players("age_test2"))
finally:
    # A failure used to leave FBPB3.exe running, and the next launch() refuses with
    # "N FBPB3 processes running" - a second failure on top of the first.
    try:
        g.exit_game(save=False)
    except Exception:                                               # noqa: BLE001
        FBPB3.kill()
print("exited")
