"""Drive FBPB3 (VB6, owner-drawn UI) with pywinauto.

Owner-drawn buttons are clicked at window-relative coordinates measured on the 1019x762 main window
(see CONVENTIONS.md). Real Win32 controls (combo boxes, text boxes, message boxes) are driven directly.
"""
from __future__ import annotations

import ctypes
import struct
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
TOP_LOAD = (132, 25)
TOP_SAVE = (207, 25)
TOP_EXIT = (955, 25)
TITLE_LOAD_CAREER = (457, 663)
LOAD_FIRST_ROW_Y, LOAD_ROW_H, LOAD_ROW_X = 170, 18, 300
LOAD_BUTTON = (804, 662)
TOOLS_OUTPUT_MDB = (260, 222)
TOOLS_HTML_OUTPUT = (264, 174)
TOOLS_LEAGUE_EDITOR = (808, 150)
EDITOR_EXPORT = (465, 662)
PLAYER_FILE_SAVE = (917, 662)
EDITOR_EXIT = (917, 662)
SAVE_NAME_OK = (622, 495)
HOTSEAT_SIM_DAY = (794, 585)
HOTSEAT_SIM_PRESEASON = (910, 585)
HOTSEAT_SIM_TO_PLAYOFFS = (910, 651)
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

    # How many rows of the Load Career grid this driver can click without scrolling.
    LOAD_ROWS_VISIBLE = 12

    def _dismiss_affirmative(self):
        """Answer a dialog with OK or Yes if it has one, and report what it said.

        Affirmative only, and only while an operation we want to COMPLETE is in flight.
        dismiss_all presses the first of OK / No / Cancel, which is right for tearing down
        leftovers and wrong here: No and Cancel are how you abort the very export being waited
        on. Returns the dialog's text, or None.

        The text is returned rather than discarded so the caller can say what it answered. This
        is the only place the driver presses an affirmative button on a dialog it has not
        identified by title, and CONVENTIONS records no Yes/No prompt during HTML Output at all
        - so whatever this answers is worth knowing about.
        """
        for label in ("OK", "Yes"):
            try:
                return self.dismiss_message(button=label, timeout=1)
            except DriverError:
                continue
        return None

    def combo(self, rel, value, timeout=20):
        """Set a dropdown by window-relative position and PROVE it took. Returns the value.

        Two traps, both of which have bitten:

        `if value in item_texts(): select(value)` skips in silence when the option is not there
        - and a combo that has not finished populating has no options yet, so a perfectly valid
        request is dropped and the screen runs with its previous setting. Hence the poll: the
        surrounding code's idiom everywhere else is wait-then-fail, not fail-immediately.

        And `selected_text()` cannot be trusted to confirm the result. pywinauto computes it as
        `item_texts()[selected_index()]`, and `selected_index()` returns CB_ERR (-1) when
        nothing is selected - which Python indexes as the LAST option. So a select that never
        took reports the final entry, and on these dialogs the final entry is frequently exactly
        the "Yes" being asked for. Compare the index instead.
        """
        end = time.time() + timeout
        last = ""
        while True:
            c = self._control_at(rel, timeout=min(10, timeout))
            if not hasattr(c, "item_texts"):
                # _control_at matches on position alone, with no class filter.
                raise DriverError(f"the control at {rel} is a {c.class_name()}, not a dropdown")
            try:
                options = list(c.item_texts())
            except Exception as exc:
                options, last = [], str(exc)
            if value in options:
                c.select(options.index(value))
                time.sleep(0.3)
                if c.selected_index() == options.index(value):
                    return value
                last = f"it stayed on index {c.selected_index()} of {options!r}"
            elif options:
                last = f"its options are {options!r}"
            if time.time() >= end:
                raise DriverError(f"could not set the dropdown at {rel} to {value!r}: "
                                  f"{last or 'it never offered any options'}")
            time.sleep(0.5)

    def dismiss_message(self, title=None, button="OK", timeout=30):
        """Wait for a standard message box, return its text, and press a button."""
        end = time.time() + timeout
        while time.time() < end:
            for w in self.app.windows(class_name="#32770", visible_only=True):
                # Everything that touches the window goes inside the guard. Reading the caption
                # and the body happened OUTSIDE it, and message boxes are transient - the game
                # closes its own, and a person may click one - so a handle going stale here
                # raised a raw pywinauto error straight past every `except DriverError`. The
                # export loop calls this about twice a second for up to fifteen minutes, so it
                # is a race that gets hundreds of chances per run.
                try:
                    if title and w.window_text() != title:
                        continue
                    dlg = self.app.window(handle=w.handle)
                    text = " ".join(c.window_text() for c in dlg.children()
                                    if c.class_name() == "Static")
                except Exception:
                    continue
                try:
                    dlg.child_window(title_re=f"&?{button}", class_name="Button").click()
                except Exception as exc:  # noqa: BLE001 - see below
                    # A Yes/No confirmation or an Abort/Retry/Ignore box has no OK, and
                    # pywinauto raises ElementNotFoundError - which is NOT a DriverError, so
                    # every `except DriverError` wrapped around this call was a hole that let
                    # a stray dialog abort a whole week's export. Raise our own error so those
                    # guards mean what they say. Fixed here rather than at each call site,
                    # because there are four and two were missed the first time.
                    raise DriverError(
                        f"message box {w.window_text()!r} has no {button!r} button ({exc})") from exc
                time.sleep(0.5)
                return text
            time.sleep(0.5)
        raise DriverError(f"no message box {title!r} within {timeout}s")

    def screenshot(self, path):
        """Capture the main window even when other windows cover it (PrintWindow)."""
        self._grab().save(path)

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

    @staticmethod
    def save_time(folder):
        """The game's own last-save timestamp for a save, as it sorts the Load Career list by.

        `saveinfo.dat` is two little-endian doubles: [0..7] is the fraction of a day (time) and
        [8..15] is the date serial counted from 1899-12-30. Added together they reproduce the
        "Last Save" column exactly.

        This must not be confused with `league.dat`'s file mtime: the codec rewrites that file
        without the game knowing, which reorders the list and makes a row click load the wrong
        league. That bug once sent an HTML export into the wrong save.
        """
        info = Path(folder) / "saveinfo.dat"
        try:
            frac, serial = struct.unpack_from("<2d", info.read_bytes(), 0)
        except (OSError, struct.error):
            # None, not 0.0. A read failure is NOT "very old": returning a timestamp for a file
            # that could not be read sorts that save to the bottom while the game's own list
            # still shows it in its real place, and every row below it shifts by one - so
            # load_save asks for CV_Pro and clicks CV_Prep. Silent, and precisely the wrong-save
            # load this ordering exists to get right. The file is readable in the normal case
            # and unreadable mainly when the running game holds it, which is exactly when this
            # is called.
            return None
        value = serial + frac
        # NaN would defeat every comparison below it: sorted() would emit an arbitrary
        # permutation without raising, and NaN != NaN means two identically corrupt saves would
        # not even register as tied.
        return None if value != value else value

    def save_rows(self):
        """Save names in the order the Load Career list shows them: newest game-save first.

        Refuses to guess when two saves report the SAME save time. The row is chosen purely by
        this ordering, so a tie means the order is arbitrary and `load_save` would load whichever
        one sorted first - silently, with everything downstream then reading or writing the wrong
        league. That is not hypothetical: copying a save folder to experiment on copies
        `saveinfo.dat` with it, so the clone and the original tie immediately, and an export
        asked for on the clone landed in the original.

        Two real saves can only tie if the game wrote them in the same second, which is unlikely
        but not impossible during a three-league Sim Week. Better to stop and say so than to pick
        one.
        """
        rows, unreadable = [], []
        for d in (DOCS / "leaguedata").iterdir():
            if not (d / "league.dat").exists():
                continue
            t = self.save_time(d)
            (unreadable if t is None else rows).append((t, d.name))
        # An unreadable save cannot be placed, and guessing a position for it shifts every row
        # after it. Report it and put it last, so load_save can refuse for the right reason.
        rows.sort(key=lambda r: r[0], reverse=True)
        self._unreadable_saves = [n for _, n in unreadable]
        return [n for _, n in rows] + self._unreadable_saves

    def ambiguous_saves(self):
        """Save names whose row in the load list cannot be trusted, and why.

        Two saves reporting the same time sort arbitrarily against each other. Copying a save
        folder copies saveinfo.dat with it, so a clone ties with its original immediately - and
        a save whose saveinfo.dat could not be read has no position at all.

        This REPORTS rather than refuses, because equal keys sort adjacently: a tie between two
        other saves cannot move a third save's row. The earlier version raised from save_rows
        for any tie anywhere, which failed every caller over an ambiguity that did not concern
        them - including the codec's own test copy, which CONVENTIONS requires to exist. Worse,
        run_sim calls this AFTER committing point spends and marking them applied, so a refusal
        there charged people for upgrades and then simmed nothing.
        """
        times, bad = {}, {}
        for d in (DOCS / "leaguedata").iterdir():
            if not (d / "league.dat").exists():
                continue
            t = self.save_time(d)
            if t is None:
                bad[d.name] = "its saveinfo.dat could not be read"
                continue
            times.setdefault(t, []).append(d.name)
        for names in times.values():
            if len(names) > 1:
                for n in names:
                    bad[n] = f"it reports the same save time as {', '.join(sorted(set(names) - {n}))}"
        return bad

    def load_save_row(self, row, wait=20):
        """Load the save at list row `row` (0-based) on the Load Saved Game screen.

        Uses the top-bar LOAD rather than the title screen's LOAD CAREER button: the top bar is
        present on every screen, while LOAD CAREER only exists before a career is open. Clicking
        the title-screen button from inside a loaded league does nothing at all, which silently
        leaves the previous league loaded and sends the next export into the wrong save.
        """
        self.click(TOP_LOAD, 3)
        self.click((LOAD_ROW_X, LOAD_FIRST_ROW_Y + row * LOAD_ROW_H), 1, real=True)
        if not self._load_button().is_enabled():
            raise DriverError(f"row {row} did not select a save")
        self.click(LOAD_BUTTON, wait)

    def load_save(self, name, wait=20):
        """Load a save by folder name, resolving its row from the save list order."""
        rows = self.save_rows()
        if name not in rows:
            raise DriverError(f"no save named {name!r} (have {rows})")
        # Only the requested save's own ambiguity matters. Everything else in the folder can be
        # as duplicated as it likes without moving this row.
        why = self.ambiguous_saves().get(name)
        if why:
            raise DriverError(
                f"refusing to load {name!r}: {why}, so its position in the load list is a guess. "
                "Open it in the game and save it, or remove the duplicate.")
        row = rows.index(name)
        if row >= self.LOAD_ROWS_VISIBLE:
            # The grid scrolls, and a VB6 grid keeps its previous selection when a click lands on
            # blank space - so LOAD would load whatever was selected before, silently.
            raise DriverError(
                f"{name!r} is row {row} of the load list, past the {self.LOAD_ROWS_VISIBLE} rows "
                "this driver can click without scrolling. Remove some old saves from leaguedata.")
        self.load_save_row(row, wait)

    def sim_days(self, n=1, timeout=20, settle=0.3):
        """Click SIM DAY `n` times, each one as soon as the previous day has finished.

        This used to sleep a fixed 8 s per click. Measured click-to-new-date on a copy of CV_Prep
        (2026-09-19), a day takes 0.55-1.2 s with 0-4 games and about 0.9 s with play-by-play
        logging on - so roughly 85% of every run's sim time was spent waiting on nothing. Now
        each click waits for the calendar's date label to change and the window to stop
        repainting for `settle` seconds, and then the next click goes straight in.

        A day that never advances is NOT waited out and clicked past, which the fixed sleep did
        silently. On 6/21 FBPB3 replaces the Hot Seat's sim buttons with its offseason panel and
        this very spot becomes DRAFT LOTTERY; a message box would swallow the click the same way.
        So: an open message box stops the run at once, a date that has not moved after `timeout`
        gets exactly one more click (an owner-drawn button does occasionally eat one), and a
        second failure raises. `timeout` is ~13x the slowest day measured, so a slow day is not
        mistaken for a lost click and simmed twice.
        """
        self.click(NAV_HOT_SEAT, 3)
        for day in range(1, n + 1):
            before = self._date_signature()
            for _attempt in range(2):
                self.click(HOTSEAT_SIM_DAY, 0)
                if self._wait_for_new_day(before, timeout, settle):
                    break
                boxes = self._message_boxes()
                if boxes:
                    raise DriverError(f"day {day} of {n}: a message box is open instead of a "
                                      f"new day: {boxes}")
            else:
                raise DriverError(
                    f"day {day} of {n}: SIM DAY did not advance the calendar in {timeout}s, "
                    "twice. Past 6/21 that spot is DRAFT LOTTERY, not SIM DAY - look at the "
                    "Hot Seat before retrying.")

    # window-relative box around the Hot Seat calendar's date label ("MARCH 23, 2027")
    HOTSEAT_DATE_BOX = (775, 247, 935, 268)

    def _grab(self):
        """The main window as a PIL image, even when other windows cover it (PrintWindow)."""
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
        try:
            ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
            info = bmp.GetInfo()
            return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                    bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)

    def _date_signature(self):
        return self._grab().crop(self.HOTSEAT_DATE_BOX).tobytes()

    def _wait_for_new_day(self, before, timeout, settle):
        """True once the date label differs from `before` and the window has been still for
        `settle` seconds (capped at 5 s, so a busy screen cannot hold the run). False on timeout."""
        end = time.time() + timeout
        while time.time() < end:
            image = self._grab()
            if image.crop(self.HOTSEAT_DATE_BOX).tobytes() != before:
                last, still_since, cap = image.tobytes(), time.time(), time.time() + 5
                while time.time() < cap:
                    time.sleep(0.1)
                    now = self._grab().tobytes()
                    if now != last:
                        last, still_since = now, time.time()
                    elif time.time() - still_since >= settle:
                        break
                return True
            time.sleep(0.1)
        return False

    def _message_boxes(self):
        found = []
        for w in self.app.windows(class_name="#32770", visible_only=True):
            try:
                found.append(w.window_text() or "(untitled)")
            except Exception:
                found.append("(unreadable)")
        return found

    def sim_preseason(self, wait=90):
        """Blast through the preseason so the regular season can start.

        A freshly created league opens in Preseason and SIM DAY only advances exhibition games;
        nothing lands in the standings until this button has been pressed.
        """
        self.click(NAV_HOT_SEAT, 3)
        self.click(HOTSEAT_SIM_PRESEASON, wait)
        self.dismiss_all()

    def save_game(self, wait=15):
        """Top-bar SAVE; the name box is prefilled with the loaded save's name."""
        self.click(TOP_SAVE, 3)
        self.click(SAVE_NAME_OK, wait)

    def exit_game(self, save=False):
        """Close the game. With save=False this cannot fail - closing is the whole job.

        The quit confirmation does not always appear: the game sometimes exits outright, and
        sometimes the box is already gone by the time we look. This used to raise, which killed
        run_sim at line 491 - AFTER the week had been simmed, saved and exported, but BEFORE the
        snapshots, the publish, the weekly points and current_week. A whole week of work thrown
        away over a dialog that was not needed, at the one point in the pipeline where failing is
        most expensive. If the box never shows, dismiss whatever is open and end the process.

        With save=True it still raises, because there a missing confirmation means the work was
        never written and pretending otherwise would lose it.
        """
        self.click(TOP_EXIT, 2)
        try:
            self.dismiss_message("Fast Break Pro Basketball 3",
                                 button="Yes" if save else "No", timeout=15)
        except DriverError:
            if save:
                raise
            self.dismiss_all()
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

    # HTML Output is a screen, not a one-click action: options on the left, the generated site's
    # colours on the right (real text boxes), then the OUTPUT HTML button. Setting the colours here
    # means the pages come out of the game already wearing the Cheezeyverse palette.
    HTML_PLAYER_PAGES = (372, 140)
    HTML_COACH_PAGES = (372, 188)
    HTML_OLD_BOXES = (372, 236)
    HTML_BOX_LINKS = (372, 284)
    HTML_STYLE = {                       # window-relative position -> what it colours
        "menu_bg_image": (760, 138), "bg_image": (760, 162),
        "menu_text": (760, 186), "menu_bg": (760, 210), "background": (760, 234),
        "header_text": (760, 258), "text": (760, 282), "link": (760, 306),
        "human_link": (760, 330), "table_header_bg": (760, 354), "table_header_font": (760, 378),
        "row": (760, 402), "row_alt": (760, 426),
    }
    HTML_OUTPUT_BTN = (691, 662)
    HTML_EXIT = (917, 662)

    # The cheese palette, matching commissioner/publish/restyle.py.
    CHEEZEY_STYLE = {
        "menu_bg_image": "", "bg_image": "",
        "menu_text": "#2E2100", "menu_bg": "#F2B705", "background": "#FFF8E6",
        "header_text": "#2E2100", "text": "#2E2100", "link": "#7A4B00",
        "human_link": "#1D5C8A", "table_header_bg": "#D9901A", "table_header_font": "#2E2100",
        "row": "#FFF8E6", "row_alt": "#F3E4BE",
    }

    def _control_at(self, rel, timeout=25):
        """Find a control by its window-relative position, WAITING for it to exist.

        A screen does not appear the instant the button that opens it is clicked, and how long
        it takes depends on what else the machine is doing. Reading a control immediately after
        the click is the same bug that made the third New Game of a session throw while the
        first two passed - and html_output() is driven three times in a row, once per league.
        Poll instead of assuming.
        """
        end = time.time() + timeout
        last = None
        while True:
            try:
                r0 = self.main.rectangle()
                for c in self.main.descendants():
                    try:
                        r = c.rectangle()
                        if (r.left - r0.left, r.top - r0.top) == rel:
                            return c
                    except Exception:
                        continue
            except Exception as exc:
                last = exc
            if time.time() >= end:
                break
            time.sleep(0.5)
        raise DriverError(f"no control at window-relative {rel} after {timeout}s"
                          + (f" ({last})" if last else ""))

    def _open_html_screen(self, attempts=3):
        """Tools -> HTML Output, checked, and retried if the screen did not actually open.

        Clicking a menu is not the same as arriving somewhere. A click lands on whatever window
        is topmost, so a leftover message box from the previous league swallows it and the game
        simply stays where it was - which is how the THIRD export of a session died on a control
        that the first two found instantly. Waiting longer cannot fix that; the screen is not
        coming. Dismiss whatever is in the way, click again, and confirm arrival by finding the
        first control the caller is about to use.
        """
        last = None
        for attempt in range(attempts):
            self.dismiss_all()
            self.click(TOP_TOOLS, 2)
            self.click(TOOLS_HTML_OUTPUT, 3)
            try:
                self._control_at(self.HTML_PLAYER_PAGES, timeout=10)
                return
            except DriverError as exc:
                last = exc
                # Back out to a known place before trying again: an Escape on the wrong screen
                # is harmless, an unnoticed dialog is not.
                self.dismiss_all()
                try:
                    self.main.type_keys("{ESC}", set_foreground=True)
                except Exception:
                    pass
                time.sleep(1.5)
        raise DriverError(f"Tools -> HTML Output would not open after {attempts} attempts ({last})")

    def html_output(self, save_name, player_pages=True, coach_pages=True, box_links=True,
                    old_boxes=False, style=None, timeout=900):
        """Tools -> Commish Tools -> HTML Output, with player pages on. Returns the html folder.

        Player pages are off by default in FBPB3 (which is why the reference Stabbyverse site has
        none). Turning them on is what gives every character a page of his own.

        `old_boxes` drives the dialog's "Output old boxes" control. It is exposed rather than
        hardcoded so the box-score question can be tested rather than assumed - and it is known
        to change nothing today, because our saves contain no `.box` files for it to convert
        (see CONVENTIONS). Left False, which is the value that was hardcoded before.
        """
        out = DOCS / "leaguedata" / save_name / "html"
        before = max((p.stat().st_mtime for p in out.glob("*.htm")), default=0) if out.exists() else 0
        self._open_html_screen()

        yes_no = {True: "Yes", False: "No"}
        for rel, want in ((self.HTML_PLAYER_PAGES, yes_no[player_pages]),
                          (self.HTML_COACH_PAGES, yes_no[coach_pages]),
                          (self.HTML_BOX_LINKS, yes_no[box_links]),
                          (self.HTML_OLD_BOXES, yes_no[old_boxes])):
            try:
                self.combo(rel, want)
            except DriverError:
                # Leave the game somewhere known. Every other exit from this screen either
                # clicks EXIT or backs out; raising from the middle of a modal dialog leaves it
                # open, so the caller's exit_game() then clicks the top bar THROUGH it and burns
                # its timeouts before falling back to kill().
                self.dismiss_all()
                try:
                    self.main.type_keys("{ESC}", set_foreground=True)
                except Exception:
                    pass
                raise

        for key, value in (style if style is not None else self.CHEEZEY_STYLE).items():
            box = self._control_at(self.HTML_STYLE[key])
            with self._foreground():
                box.set_focus()
                box.type_keys("^a{BACKSPACE}", set_foreground=True)
                if value:
                    box.type_keys(value, with_spaces=True, set_foreground=True)
            time.sleep(0.15)

        self.click(self.HTML_OUTPUT_BTN, 3)
        end = time.time() + timeout
        index = out / "index.htm"
        answered = []
        while time.time() < end:
            # Affirmative buttons only: No and Cancel are how you abort the export being
            # waited on. Whatever it answers is remembered, because the timeout below is
            # otherwise unable to tell "the export was slow" from "a box was in the way" - and
            # a box that blocked for ten minutes and was then cleared leaves nothing to see.
            said = self._dismiss_affirmative()
            if said:
                answered.append(said.strip()[:120])
            # A dead game looks exactly like a slow export to a loop that only watches for a
            # file. FBPB3 has crashed mid-run before.
            if not self.is_running():
                raise DriverError(
                    f"FBPB3 stopped while exporting {save_name}"
                    + (f"; it had said: {answered}" if answered else ""))
            if index.exists() and index.stat().st_mtime > before:
                time.sleep(5)  # the per-player pages keep landing after index.htm does
                # NOT dismiss_all here. It clicks the first of OK / No / Cancel that exists, and
                # by the comment five lines up the export is still writing per-player pages - so
                # answering No or Cancel to a question asked mid-export truncates the site and
                # this path RETURNS SUCCESS, which is worse than the loop failing loudly. Same
                # affirmative-only policy as the wait loop.
                self._dismiss_affirmative()
                self.click(self.HTML_EXIT, 2)
                return out
        # Say what was on screen, and what was answered along the way. A caption alone is
        # useless here - FBPB3 titles its own boxes "Fast Break Pro Basketball 3", so an
        # overwrite prompt, a disk error and a quit confirmation all look identical - so the
        # body text collected while waiting is the part worth keeping.
        blocking = []
        try:
            for w in self.app.windows(class_name="#32770", visible_only=True):
                dlg = self.app.window(handle=w.handle)
                body = " ".join(c.window_text() for c in dlg.children()
                                if c.class_name() == "Static")
                blocking.append(body.strip()[:120] or w.window_text())
        except Exception:
            pass
        # Back out before raising, for the same reason the combo failure above does: a modal
        # left open swallows the caller's exit_game clicks and costs another 45 seconds before
        # it gives up and kills the process.
        self.dismiss_all()
        try:
            self.main.type_keys("{ESC}", set_foreground=True)
        except Exception:
            pass
        extra = f"; a dialog is open: {blocking}" if blocking else ""
        extra += f"; answered along the way: {answered}" if answered else ""
        raise DriverError(f"HTML output did not appear under {out} within {timeout}s{extra}")

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
