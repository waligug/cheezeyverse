"""Connect to FBPB3 and dump the main window's menu and control tree."""
import sys, time, warnings
warnings.filterwarnings("ignore")
from pywinauto import Application

EXE = r"C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3\FBPB3.exe"
try:
    app = Application(backend="win32").connect(path=EXE, timeout=1)
except Exception:
    app = Application(backend="win32").start(EXE, work_dir=r"C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3")
    time.sleep(6)
main = app.window(class_name="ThunderRT6MDIForm")

def walk(items, depth=0):
    for it in items:
        print("  " * depth + repr(it.text()), "enabled" if it.is_enabled() else "disabled")
        sub = it.sub_menu()
        if sub:
            walk(sub.items(), depth + 1)

if "menu" in sys.argv:
    walk(main.menu().items())
else:
    main.print_control_identifiers(depth=int(sys.argv[1]) if len(sys.argv) > 1 else 3)
