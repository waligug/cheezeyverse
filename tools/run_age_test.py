"""Load Chung_test after the age edit, export players + MDB, exit without saving."""
import sys, warnings, time
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.driver.fbpb3 import FBPB3

g = FBPB3()
FBPB3.kill(); time.sleep(2)
g.launch()
print("launched")
g.load_save("Chung_test")
g.screenshot(r"C:\claude\hoops-universe\tmp\age_loaded.png")
print("loaded")
print(g.export_players("age_test"))
g.output_mdb()
print("mdb written")
g.screenshot(r"C:\claude\hoops-universe\tmp\age_after.png")
g.exit_game(save=False)
print("done")
