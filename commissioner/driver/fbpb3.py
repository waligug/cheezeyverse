"""Drive FBPB3 (VB6, owner-drawn UI) with pywinauto.

Owner-drawn buttons are clicked at window-relative coordinates measured on the 1019x762 main window
(see CONVENTIONS.md). Real Win32 controls (combo boxes, text boxes, message boxes) are driven directly.
"""
from __future__ import annotations

import subprocess
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
    def is_running():
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe"], capture_output=True, text=True).stdout
        return "FBPB3.exe" in out

    # ---- primitives --------------------------------------------------------------------------
    def click(self, xy, wait=1.5):
        r = self.main.rectangle()
        self.main.set_focus()
        mouse.click(coords=(r.left + xy[0], r.top + xy[1]))
        time.sleep(wait)

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
        from PIL import ImageGrab
        r = self.main.rectangle()
        ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True).save(path)

    # ---- workflows -------------------------------------------------------------------------
    def load_save_row(self, row, wait=20):
        """Load the save at list row `row` (0-based) on the Load Saved Game screen."""
        self.click(TITLE_LOAD_CAREER, 3)
        self.click((LOAD_ROW_X, LOAD_FIRST_ROW_Y + row * LOAD_ROW_H), 1)
        self.click(LOAD_BUTTON, wait)

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

    def output_mdb(self):
        self.click(TOP_TOOLS)
        self.click(TOOLS_OUTPUT_MDB, 1)
        return self.dismiss_message("File Created", timeout=120)

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
        box.click_input()
        box.type_keys(name, with_spaces=True)
        time.sleep(0.5)
        self.click(SAVE_NAME_OK, 3)
        end = time.time() + 30
        while not target.exists() and time.time() < end:
            time.sleep(0.5)
        if not target.exists():
            raise DriverError(f"player export did not appear at {target}")
        self.click(EDITOR_EXIT, 3)  # back to the Tools screen
        return target
