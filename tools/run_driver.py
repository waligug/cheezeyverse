"""run_driver.py <method> [args...] -- call one FBPB3 driver method on the running game."""
import sys
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.driver.fbpb3 import FBPB3
g = FBPB3().launch()
m = getattr(g, sys.argv[1])
args = [int(a) if a.lstrip("-").isdigit() else a for a in sys.argv[2:]]
print(m(*args))
