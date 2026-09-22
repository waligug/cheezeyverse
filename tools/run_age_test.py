"""Load Chung_test after the age edit, export players + MDB, exit without saving.

A Phase 0 probe, kept deliberately: it was deleted once as superseded and restored as an
unintended removal, so it is repaired here rather than deleted again.

IT NEEDS A `Chung_test` SAVE, which SERVERPC does not have - the universe here is CV_Prep,
CV_College and CV_Pro. For the same job against a save that does exist, use
`tools/make_codec_fixtures.py`, which captures the save and the export together and is what
proved the codec reads both shapes correctly.
"""
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402

SAVE = "Chung_test"
SHOTS = r"C:\claude\hoops-universe\tmp"

g = FBPB3()
FBPB3.kill()
time.sleep(2)
g.launch()
print("launched")
try:
    g.load_save(SAVE)
    g.screenshot(rf"{SHOTS}\age_loaded.png")
    print("loaded")
    print(g.export_players("age_test"))
    # output_mdb has taken the save name since it learned to check the file it produced;
    # calling it bare raised TypeError, so this script had not run past the export since.
    g.output_mdb(SAVE)
    print("mdb written")
    g.screenshot(rf"{SHOTS}\age_after.png")
finally:
    # Without this a failure left FBPB3.exe running, and the next launch() refuses with
    # "N FBPB3 processes running" - a second, confusing failure on top of the first.
    try:
        g.exit_game(save=False)
    except Exception:                                               # noqa: BLE001
        FBPB3.kill()
print("done")
