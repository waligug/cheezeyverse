"""step.py <x> <y> <wait> <shotname>: click a main-window-relative point (skip with x<0),
wait for a dialog or the timeout, report extra windows, and screenshot to tmp/."""
import sys, time
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.driver.fbpb3 import FBPB3
S = r"C:\claude\hoops-universe\tmp"
g = FBPB3().launch()
x, y, wait, name = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
if x >= 0:
    try:
        g.click((x, y), 0.5)
    except Exception as e:
        print("CLICK FAILED:", type(e).__name__)
end = time.monotonic() + wait
while time.monotonic() < end:
    time.sleep(1)
    if g.app.windows(class_name="#32770", visible_only=True):
        break
for w in g.app.windows(visible_only=True):
    if w.class_name() not in ("ThunderRT6MDIForm", "ThunderRT6Main", "Static"):
        dlg = g.app.window(handle=w.handle)
        print("WINDOW", w.class_name(), repr(w.window_text()), [(c.class_name(), c.window_text()) for c in dlg.children()][:12])
g.screenshot(S + "\\" + name + ".png")
print("shot", name)
