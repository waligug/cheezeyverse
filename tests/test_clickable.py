"""A sim that cannot click must refuse, not click anyway.

THE FAILURE THIS PREVENTS. The driver clicks FBPB3 by POSITION - it has to, because the
owner-drawn VB6 controls have no other handle - so every coordinate in fbpb3.py is a promise
that a particular pixel is on screen and belongs to the game. Two things on this machine break
that promise while looking perfectly healthy:

  a locked or disconnected session   renders nothing; real mouse input lands nowhere
  a desktop too small for the window the game window is 1019x762 and the console with no
                                     monitor attached is 1024x768. Take the taskbar's forty
                                     pixels and the bottom row of buttons - LOAD, PROCESS ALL,
                                     SIM TO PLAYOFFS - sits underneath it. A click meant for
                                     LOAD lands on the taskbar and the driver waits out a load
                                     that never started.

Neither is a slower sim. Both are a sim that does something else, to a save with no undo.

AND THE EXTENT MUST STAY TRUE. CLICK_EXTENT is the rectangle the guard demands, and it is only
right for as long as it really is the furthest thing anybody clicks. So this reads every
coordinate out of the driver and checks - a new button further out fails here rather than in
front of a save.

    python tests/test_clickable.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.driver import fbpb3  # noqa: E402
from commissioner.driver.fbpb3 import CLICK_EXTENT, DriverError, FBPB3  # noqa: E402

DRIVER = ROOT / "commissioner" / "driver" / "fbpb3.py"


def coordinates():
    """{name: (x, y)} for every two-integer constant in the driver - the things it clicks."""
    tree = ast.parse(DRIVER.read_text(encoding="utf-8"))
    found = {}

    def collect(body, prefix=""):
        for node in body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Tuple):
                nums = [v.value for v in node.value.elts
                        if isinstance(v, ast.Constant) and isinstance(v.value, int)]
                if len(nums) == 2 and isinstance(node.targets[0], ast.Name):
                    found[prefix + node.targets[0].id] = tuple(nums)
            elif isinstance(node, ast.ClassDef):
                collect(node.body, f"{node.name}.")

    collect(tree.body)
    return found


class FakeWindow:
    def __init__(self, left, top, right, bottom):
        self._r = type("R", (), {"left": left, "top": top, "right": right, "bottom": bottom})()
        self.handle = 1
        self.moved_to = None

    def rectangle(self):
        return self._r

    def move_window(self, x, y):
        self.moved_to = (x, y)
        w = self._r.right - self._r.left
        h = self._r.bottom - self._r.top
        self._r = type("R", (), {"left": x, "top": y, "right": x + w, "bottom": y + h})()


def guard(window, work, foreground=42, move=True):
    """Run assert_clickable against a made-up desktop. Returns True, or the DriverError."""
    game = FBPB3.__new__(FBPB3)
    game.main = window
    import types
    fake_gui = types.SimpleNamespace(GetForegroundWindow=lambda: foreground)
    fake_api = types.SimpleNamespace(
        MonitorFromWindow=lambda *_: 1,
        GetMonitorInfo=lambda *_: {"Work": work},
    )
    fake_con = types.SimpleNamespace(MONITOR_DEFAULTTONEAREST=2)
    real = {k: sys.modules.get(k) for k in ("win32gui", "win32api", "win32con")}
    sys.modules["win32gui"], sys.modules["win32api"], sys.modules["win32con"] = \
        fake_gui, fake_api, fake_con
    try:
        return game.assert_clickable(move=move)
    except DriverError as exc:
        return exc
    finally:
        for k, v in real.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def main():
    need_w, need_h = CLICK_EXTENT

    # ---- the extent really is the furthest thing clicked -----------------------------------
    points = coordinates()
    assert len(points) > 20, f"only found {len(points)} coordinates - the reader is broken"
    outside = {n: p for n, p in points.items() if p[0] > need_w or p[1] > need_h}
    assert not outside, \
        f"these are outside CLICK_EXTENT {CLICK_EXTENT}, so the guard would pass a desktop " \
        f"that cannot click them: {outside}"
    furthest = max(points.values(), key=lambda p: p[0])[0], max(points.values(), key=lambda p: p[1])[1]
    assert need_w - furthest[0] < 80 and need_h - furthest[1] < 80, \
        f"CLICK_EXTENT {CLICK_EXTENT} is far bigger than anything clicked {furthest} - it " \
        "would refuse desktops that work perfectly well"

    # ---- a desktop with room is fine ---------------------------------------------------------
    big = (0, 0, 2560, 1400)
    assert guard(FakeWindow(0, 0, 1019, 762), big) is True, "a 2560x1400 desktop must pass"

    # ---- a window pushed off the edge is MOVED, not refused ----------------------------------
    win = FakeWindow(2000, 1000, 3019, 1762)
    assert guard(win, big) is True, "a window that merely sits too low should be moved back"
    assert win.moved_to == (0, 0), f"it was not moved: {win.moved_to}"

    # ---- the headless console, which is the real case ----------------------------------------
    # 1024x768 with a 40px taskbar: 728 of usable height against 670 needed from the window's
    # top-left - which fits only if the window is at the very top, and the window is 762 tall.
    console = (0, 0, 1024, 728)
    ok = guard(FakeWindow(0, 0, 1019, 762), console)
    assert ok is True, f"a window at the top-left of a 1024x728 work area does fit: {ok}"
    # ...but drop it by fifty pixels, where Windows actually puts a restored window, and it does
    # not - and no move can save it, because the work area is only 728 tall.
    low = guard(FakeWindow(0, 60, 1019, 822), (0, 0, 1024, 700), move=False)
    assert isinstance(low, DriverError), "a window whose buttons are under the taskbar must refuse"
    assert "too small" in str(low), low

    # ---- a locked session refuses before it looks at anything else ---------------------------
    locked = guard(FakeWindow(0, 0, 1019, 762), big, foreground=0)
    assert isinstance(locked, DriverError), "a locked desktop must refuse"
    assert "not rendering" in str(locked), locked
    assert "install_session_keeper" in str(locked), \
        "the refusal should name the fix, not just the problem"

    # ---- and launch() must actually call it --------------------------------------------------
    src = DRIVER.read_text(encoding="utf-8")
    launch = src[src.index("def launch("):src.index("def assert_clickable(")]
    assert "self.assert_clickable()" in launch, \
        "launch() no longer checks - every driver path starts there, which is why the check " \
        "lives in it rather than in each caller"

    print(f"OK  clickable guard: {len(points)} coordinates all inside {CLICK_EXTENT}, a locked "
          "desktop and a too-small one both refuse, and a misplaced window is moved first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
