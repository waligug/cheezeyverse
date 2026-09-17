"""Drive FBPB3 (VB6, owner-drawn UI) with pywinauto.

Owner-drawn buttons are clicked at window-relative coordinates measured on the 1019x762 main window
(see CONVENTIONS.md). Real Win32 controls (combo boxes, text boxes, message boxes) are driven directly.
"""
from __future__ import annotations

import ctypes
import subprocess
from contextlib import contextmanager
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="32-bit application")
from pywinauto import Application, mouse  # noqa: E402

GAME_DIR = Path(r"C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3")
EXE = GAME_DIR / "FBPB3.exe"
DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")

# window-relative click targets
TOP_TOOLS = (281, 25)
TOP_SAVE = (207, 25)
TOP_EXIT = (955, 25)
TITLE_LOAD_CAREER = (457, 663)
LOAD_FIRST_ROW_Y, LOAD_ROW_H, LOAD_ROW_X = 170, 18, 300
LOAD_BUTTON = (804, 662)
TOOLS_OUTPUT_MDB = (260, 222)
TOOLS_LEAGUE_EDITOR = (808, 150)
EDITOR_EXPORT = (465, 662)
PLAYER_FILE_SAVE = (917, 662)
EDITOR_EXIT = (917, 662)
SAVE_NAME_OK = (622, 495)
HOTSEAT_SIM_DAY = (794, 585)
NAV_HOT_SEAT = (55, 95)


class DriverError(Exception):
    pass


class FBPB3:
    def __init__(self):
        self.app = None
        self.main = None

    # ---- process ---------------------------------------------------------------------------
    def launch(self, timeout=60):
        pids = self.pids()
        if len(pids) > 1:
            raise DriverError(f"{len(pids)} FBPB3 processes running ({pids}); kill() first")
        try:
            self.app = Application(backend="win32").connect(path=str(EXE), timeout=1)
        except Exception:
            self.app = Application(backend="win32").start(str(EXE), work_dir=str(GAME_DIR))
        self.main = self.app.window(class_name="ThunderRT6MDIForm")
        self.main.wait("visible", timeout=timeout)
        time.sleep(3)
        return self

    @staticmethod
    def kill():
        subprocess.run(["taskkill", "/IM", "FBPB3.exe", "/F"], capture_output=True)
        time.sleep(2)

    @staticmethod
    def pids():
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        return [line.split(",")[1].strip('"') for line in out.splitlines() if line.startswith('"FBPB3')]

    @staticmethod
    def is_running():
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe"], capture_output=True, text=True).stdout
        return "FBPB3.exe" in out

    # ---- primitives --------------------------------------------------------------------------
    def click(self, xy, wait=1.5, real=True):
        """Post a click to the innermost child window under a main-window-relative point.
        Uses window messages, so it works while FBPB3 is covered by other windows."""
        r0 = self.main.rectangle()
        sx, sy = r0.left + xy[0], r0.top + xy[1]
        if real:  # VB6 owner-drawn labels/grids only react to real mouse input; posted messages are ignored
            with self._foreground():  # the click lands on whatever window is topmost, so force ours up
                mouse.click(coords=(sx, sy))
            time.sleep(wait)
            return
        best = None
        for c in self.main.descendants():
            if not c.is_visible():
                continue
            r = c.rectangle()
            if r.left <= sx < r.right and r.top <= sy < r.bottom:
                area = r.width() * r.height()
                if best is None or area < best[0]:
                    best = (area, c, r)
        if best is None:
            target, rr = self.main, r0
        else:
            _, target, rr = best
        cx, cy = sx - rr.left, sy - rr.top
        if target.class_name() in ("MDIClient", "ThunderRT6FormDC", "ThunderRT6MDIForm"):
            # windowless VB6 labels (e.g. the Tools screen) are only hit-tested from real mouse input,
            # so the window has to be genuinely on top for the duration of the click
            with self._foreground():
                mouse.click(coords=(sx, sy))
        else:
            target.click(coords=(cx, cy))
        time.sleep(wait)

    @contextmanager
    def _foreground(self):
        """Force the game window to the top (real mouse clicks land on whatever is topmost)."""
        import win32con
        import win32gui
        hwnd = self.main.handle
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
        try:
            win32gui.BringWindowToTop(hwnd)
            time.sleep(0.3)
            yield
        finally:
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)

    def dismiss_message(self, title=None, button="OK", timeout=30):
        """Wait for a standard message box, return its text, and press a button."""
        end = time.time() + timeout
        while time.time() < end:
            for w in self.app.windows(class_name="#32770", visible_only=True):
                if title and w.window_text() != title:
                    continue
                dlg = self.app.window(handle=w.handle)
                text = " ".join(c.window_text() for c in dlg.children() if c.class_name() == "Static")
                dlg.child_window(title_re=f"&?{button}", class_name="Button").click()
                time.sleep(0.5)
                return text
            time.sleep(0.5)
        raise DriverError(f"no message box {title!r} within {timeout}s")

    def screenshot(self, path):
        """Capture the main window even when other windows cover it (PrintWindow)."""
        import win32gui
        import win32ui
        from PIL import Image
        hwnd = self.main.handle
        r = self.main.rectangle()
        w, h = r.width(), r.height()
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        img.save(path)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)

    # ---- workflows -------------------------------------------------------------------------
    LOAD_BTN_RECT = (748, 650, 113, 25)  # window-relative; disabled until a row is selected

    def _load_button(self):
        r0 = self.main.rectangle()
        x, y, w, h = self.LOAD_BTN_RECT
        for c in self.main.descendants(class_name="ThunderRT6UserControlDC"):
            r = c.rectangle()
            if (r.left - r0.left, r.top - r0.top, r.width(), r.height()) == (x, y, w, h):
                return c
        raise DriverError("LOAD button not found on the Load Saved Game screen")

    def save_rows(self):
        """Save names in list order: the game sorts by last save time, newest first."""
        saves = [d for d in (DOCS / "leaguedata").iterdir() if (d / "league.dat").exists()]
        saves.sort(key=lambda d: (d / "league.dat").stat().st_mtime, reverse=True)
        return [d.name for d in saves]

    def load_save_row(self, row, wait=20):
        """Load the save at list row `row` (0-based) on the Load Saved Game screen."""
        self.click(TITLE_LOAD_CAREER, 3)
        self.click((LOAD_ROW_X, LOAD_FIRST_ROW_Y + row * LOAD_ROW_H), 1, real=True)
        if not self._load_button().is_enabled():
            raise DriverError(f"row {row} did not select a save")
        self.click(LOAD_BUTTON, wait)

    def load_save(self, name, wait=20):
        """Load a save by folder name, resolving its row from the save list order."""
        rows = self.save_rows()
        if name not in rows:
            raise DriverError(f"no save named {name!r} (have {rows})")
        self.load_save_row(rows.index(name), wait)

    def sim_days(self, n=1, per_day_wait=8):
        self.click(NAV_HOT_SEAT, 3)
        for _ in range(n):
            self.click(HOTSEAT_SIM_DAY, per_day_wait)

    def save_game(self, wait=15):
        """Top-bar SAVE; the name box is prefilled with the loaded save's name."""
        self.click(TOP_SAVE, 3)
        self.click(SAVE_NAME_OK, wait)

    def exit_game(self, save=False):
        self.click(TOP_EXIT, 2)
        self.dismiss_message("Fast Break Pro Basketball 3", button="Yes" if save else "No", timeout=15)
        end = time.time() + 30
        while self.is_running() and time.time() < end:
            time.sleep(0.5)
        if self.is_running():
            self.kill()

    def output_mdb(self, save_name, attempts=4):
        """Tools -> Output MDB for the loaded save. Menu-label clicks are occasionally swallowed,
        so this retries and confirms by the file's timestamp rather than by the dialog alone."""
        target = DOCS / "leaguedata" / save_name / "LeagueOutput.mdb"
        before = target.stat().st_mtime if target.exists() else 0
        for _ in range(attempts):
            self.click(TOP_TOOLS, 2)
            self.click(TOOLS_OUTPUT_MDB, 2)
            end = time.time() + 180
            while time.time() < end:
                try:
                    self.dismiss_message("File Created", timeout=2)
                except DriverError:
                    pass
                if target.exists() and target.stat().st_mtime > before:
                    self.dismiss_all()
                    return target
            self.dismiss_all()
        raise DriverError(f"Output MDB did not refresh {target}")

    def dismiss_all(self):
        """Close any standard message boxes that are open."""
        for w in self.app.windows(class_name="#32770", visible_only=True):
            try:
                dlg = self.app.window(handle=w.handle)
                for title in ("OK", "&No", "Cancel"):
                    btn = dlg.child_window(title=title, class_name="Button")
                    if btn.exists():
                        btn.click()
                        break
            except Exception:
                pass

    def export_players(self, name):
        """Current League Editor → Players → Export → Save as PlayerFiles/<name>.csv. Returns the path."""
        target = DOCS / "PlayerFiles" / f"{name}.csv"
        if target.exists():
            target.unlink()
        self.click(TOP_TOOLS)
        self.click(TOOLS_LEAGUE_EDITOR, 3)
        combos = [c for c in self.main.descendants(class_name="ThunderRT6ComboBox") if c.is_visible()]
        sort_by = next(c for c in combos if "Draft Pool" in c.item_texts())
        sort_by.select("Players")
        time.sleep(2)
        self.click(EDITOR_EXPORT, 3)
        self.click(PLAYER_FILE_SAVE, 2)
        box = self.app.window(class_name="ThunderRT6FormDC").child_window(class_name="ThunderRT6TextBox")
        box.set_focus()
        box.type_keys(name, with_spaces=True, set_foreground=False)
        time.sleep(0.5)
        self.click(SAVE_NAME_OK, 3)
        end = time.time() + 30
        while not target.exists() and time.time() < end:
            time.sleep(0.5)
        if not target.exists():
            raise DriverError(f"player export did not appear at {target}")
        self.click(EDITOR_EXIT, 3)  # back to the Tools screen
        return target
