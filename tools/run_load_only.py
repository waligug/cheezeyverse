import sys, warnings, time
warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.driver.fbpb3 import FBPB3
g = FBPB3(); FBPB3.kill(); time.sleep(2); g.launch()
g.load_save("Chung_test")
print("loaded")
print(g.export_players("age_test2"))
g.exit_game(save=False)
print("exited")
