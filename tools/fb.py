"""Interactive FBPB3 probe: `shot [name]`, `ctrls`, `click X Y` (window-relative), `wins`, `key KEYS`."""
import sys, time, warnings
warnings.filterwarnings("ignore")
from pywinauto import Application, mouse, keyboard
from PIL import ImageGrab

EXE = r"C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3\FBPB3.exe"
SHOTS = r"C:\Users\Nate\AppData\Local\Temp\claude\C--claude\e00cd8fd-feba-4c6d-b935-8096e6fd7796\scratchpad"
app = Application(backend="win32").connect(path=EXE, timeout=5)
main = app.window(class_name="ThunderRT6MDIForm")
cmd, args = sys.argv[1], sys.argv[2:]

def shot(name="shot"):
    w = app.top_window()
    w.set_focus(); time.sleep(0.4)
    r = main.rectangle()
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(f"{SHOTS}\{name}.png")
    print("saved", name, "top:", repr(w.window_text()), w.class_name())

if cmd == "shot":
    shot(args[0] if args else "shot")
elif cmd == "wins":
    for w in app.windows():
        if w.is_visible():
            print(hex(w.handle), repr(w.window_text()), w.class_name(), w.rectangle())
elif cmd == "ctrls":
    r0 = main.rectangle()
    for c in main.descendants():
        if c.is_visible() and "Timer" not in c.class_name():
            r = c.rectangle()
            print(repr(c.window_text())[:40], c.class_name(), (r.left - r0.left, r.top - r0.top, r.width(), r.height()))
elif cmd == "click":
    r0 = main.rectangle()
    main.set_focus(); time.sleep(0.2)
    mouse.click(coords=(r0.left + int(args[0]), r0.top + int(args[1])))
    time.sleep(float(args[2]) if len(args) > 2 else 2)
    shot("after")
elif cmd == "key":
    main.set_focus(); keyboard.send_keys(args[0]); time.sleep(1.5); shot("after")
