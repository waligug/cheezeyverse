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
# The offseason panel that REPLACES the sim buttons once the season is over (6/21 in the
# rehearsal). Same grid, one row higher, and the labels change as each phase completes.
HOTSEAT_END_SEASON = (794, 552)
HOTSEAT_OFFSEASON = (910, 552)
HOTSEAT_HIRE_STAFF = (910, 585)
HOTSEAT_TRAINING_CAMPS = (910, 651)
# On a phase screen (Hire Staff and friends): PROCESS ALL, which then becomes PROCEED.
PHASE_PROCESS_ALL = (805, 662)
HOTSEAT_SIM_TO_PLAYOFFS = (910, 651)
NAV_HOT_SEAT = (55, 95)

# The furthest point this driver ever clicks, in window coordinates: TOP_EXIT is at x=955 and
# the bottom row of buttons at y=663. Every click has to land inside the visible desktop, so
# this is the rectangle the game window needs to actually occupy. tests/test_clickable.py
# asserts that no coordinate in this file falls outside it, so adding a button further out
# fails the suite instead of failing a sim.
CLICK_EXTENT = (960, 670)

# Floors for the two adaptive waits (load and save). They are not how long those steps take -
# both finish when the game says so - but how long the driver will hold on before calling the
# step failed. A slow disk on a 6 MB save has never come near either. Named so the tests can
# shrink them; nothing else should.
LOAD_LIMIT_FLOOR = 30
SAVE_LIMIT_FLOOR = 30


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
        self.assert_clickable()
        return self

    def assert_clickable(self, move=True):
        """Refuse to drive the game unless every point we click can actually be clicked.

        THE TWO WAYS THIS MACHINE STOPS BEING CLICKABLE, both of which look completely healthy
        from a command line:

        LOCKED OR DISCONNECTED SESSION. Closing a Remote Desktop window locks the session; the
        processes keep running and nothing renders. Real mouse input lands nowhere.
        GetForegroundWindow returns 0 there, which is the cheapest honest test there is.

        A DESKTOP TOO SMALL FOR THE WINDOW. FBPB3's window is 1019x762 and the driver clicks by
        position, out to (955, 663). The console on this box with no monitor attached is
        1024x768, and once the taskbar takes its forty pixels the bottom row of buttons is
        underneath it - so a click meant for LOAD lands on the taskbar, and the driver waits out
        a load that was never started. That is the failure the whole dummy-plug conversation is
        about, and until now nothing checked for it.

        Both are refusals rather than warnings. A sim that cannot click is not a slower sim; it
        is a sim that does something else, and FBPB3 has no undo.
        """
        import win32api
        import win32con
        import win32gui

        if win32gui.GetForegroundWindow() == 0:
            raise DriverError(
                "this desktop is not rendering - the session is locked or disconnected, and "
                "every real mouse click would land nowhere. Reconnect, or hand the session "
                "back to the console (tools\\install_session_keeper.ps1).")

        r = self.main.rectangle()
        need_w, need_h = CLICK_EXTENT
        work = win32api.GetMonitorInfo(
            win32api.MonitorFromWindow(self.main.handle, win32con.MONITOR_DEFAULTTONEAREST)
        )["Work"]

        def fits(left, top):
            return (left >= work[0] and top >= work[1]
                    and left + need_w <= work[2] and top + need_h <= work[3])

        if fits(r.left, r.top):
            return True
        if move:
            # Usually it does fit and is merely sitting too low or too far right - a window the
            # game restored to where it was on a bigger screen. Move it to the corner of the
            # work area and ask again before refusing.
            try:
                self.main.move_window(x=work[0], y=work[1])
                time.sleep(0.5)
                r = self.main.rectangle()
                if fits(r.left, r.top):
                    return True
            except Exception:                                    # noqa: BLE001
                pass
        raise DriverError(
            f"the desktop is too small to drive the game: the usable area is "
            f"{work[2] - work[0]}x{work[3] - work[1]} and the driver needs {need_w}x{need_h} "
            f"from the window's top-left corner (the window is at {r.left},{r.top}). Clicks "
            "meant for the bottom row of buttons would land on the taskbar or off-screen. "
            "Raise the resolution - a headless console falls back to 1024x768, and a dummy "
            "HDMI/DP plug makes it report a real monitor's size.")

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
        # If the game is ALREADY the foreground window, the raise below and its 0.3 s settle
        # are 300 ms of doing nothing - and they are paid on every single click, which measured
        # 520 ms each and made up roughly a fifth of a simulated day. The check is exact
        # (GetForegroundWindow, not "probably still ours"), so a stolen focus or an FBPB3 dialog
        # - a different top-level window - still takes the full path.
        if win32gui.GetForegroundWindow() == hwnd and not win32gui.IsIconic(hwnd):
            yield
            return
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
        # Each of these three used to be a fixed sleep as well. The Load screen arriving IS the
        # LOAD button existing, and the row being selected IS that button going enabled, so
        # both are things to watch for rather than to wait out.
        self._load_hwnd = None          # never trust a handle found on an earlier load
        self.click(TOP_LOAD, 0)
        if not self._wait_for(lambda: self._load_screen_open(unknown=False), 10):
            raise DriverError("the Load Saved Game screen never appeared")
        self.click((LOAD_ROW_X, LOAD_FIRST_ROW_Y + row * LOAD_ROW_H), 0, real=True)
        if not self._wait_for(lambda: self._load_button().is_enabled(), 5):
            raise DriverError(f"row {row} did not select a save")
        # Click, then WAIT FOR THE LOAD TO FINISH rather than sleeping a flat 30 s. A load cost
        # 36 s a league in the three-league run of 2026-09-19 and almost all of it was the
        # sleep; the game is on the Hot Seat long before it ends. `wait` is now a ceiling
        # instead of a price.
        #
        # The signal is the LOAD SCREEN GOING AWAY, not the picture settling. A settled picture
        # would be the obvious test and the wrong one: while VB6 reads league.dat it stops
        # pumping messages, and an unresponsive window repaints to the same pixels every time,
        # so "nothing is moving" is exactly what the middle of a load looks like. The button
        # only vanishes once the game has actually navigated off the Load screen.
        limit = max(LOAD_LIMIT_FLOOR, wait * 3)
        deadline = time.time() + limit
        self.click(LOAD_BUTTON, 0)
        while self._load_screen_open(unknown=True):
            if time.time() > deadline:
                raise DriverError(f"row {row} was still on the Load screen {limit}s after "
                                  "LOAD was clicked")
            time.sleep(0.25)
        # A short settle, not a long one: this only has to outlast the Hot Seat's own repaint so
        # that whatever reads the date label next reads a finished one.
        self._wait_until_still(settle=0.5, timeout=30, poll=0.1)

    @staticmethod
    def _wait_for(cond, timeout, poll=0.05):
        """True as soon as `cond()` is, False at `timeout`.

        A condition that raises counts as "not yet", whatever it raised. Reading a VB6 window
        tree while the game is rebuilding it throws from inside pywinauto - InvalidWindowHandle
        on a control destroyed between being listed and being wrapped - and that is a normal
        thing to see mid-transition, not a reason to abandon the run. The timeout is what makes
        this safe: a condition that never becomes true still ends, and ends as a refusal.
        """
        end = time.time() + timeout
        while time.time() < end:
            try:
                if cond():
                    return True
            except Exception:                                    # noqa: BLE001 - see above
                pass
            time.sleep(poll)
        return False

    def _load_screen_open(self, unknown=True):
        """True while the Load Saved Game screen's LOAD button is there.

        `unknown` is the answer when the window tree cannot be read at all, and the two callers
        want opposite answers. Waiting for the screen to ARRIVE, an unreadable tree means keep
        looking (False, not yet). Waiting for a load to FINISH, it must mean keep waiting
        (True): the one thing that must never happen is a transient pywinauto error being read
        as "the load is done", which would send the export at a league still being read in.

        After the first look the button's handle is remembered and the question becomes a bare
        IsWindowVisible. Enumerating a VB6 form's controls costs a good fraction of a second and
        this is asked four times a second for the length of every load.
        """
        import win32gui
        hwnd = getattr(self, "_load_hwnd", None)
        try:
            if hwnd:
                return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))
            self._load_hwnd = self._load_button().handle
            return True
        except DriverError:
            return False
        except Exception:                                        # noqa: BLE001 - see above
            return unknown

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
        # Settle rather than sleep 3 s, and settle BEFORE the first date is read: a label caught
        # half-painted is a `before` that nothing will ever match, which reads as a day that
        # advanced when it did not.
        self.click(NAV_HOT_SEAT, 0)
        self._wait_until_still(settle=0.4, timeout=15, poll=0.1)
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

    def roll_over_season(self, wait=12, log=print):
        """Take a finished season through FBPB3's own rollover to the next PRESEASON.

        Rehearsed end to end on a copy of the live CV_Prep, 2026-09-19, the season having been
        won. The sequence, and what each step does:

          END SEASON      no dialog. Stage -> OFFSEASON, records reset, 12 of 425 players
                          retired, every player +1 experience and some ratings developed.
          OFFSEASON       stage -> STAFF HIRING.
          HIRE STAFF      opens a screen. PROCESS ALL runs the CPU's hiring and the button then
                          becomes PROCEED, which is what advances the phase - pressing EXIT
                          instead leaves the stage exactly where it was, which is how this was
                          first got wrong.
          TRAINING CAMPS  stage -> PRESEASON, on the new season's calendar.

        Afterwards: Season 2027, SeasonDay back to 1, a fresh 240-game schedule with the
        playoffs cleared, 425 players again, and all seven characters still on their teams.
        The draft and free-agency phases are skipped entirely because this universe has them
        off; a league with them on would stop at a button this does not press, which is why
        every step verifies instead of assuming.

        THE GAME DEVELOPS PLAYERS HERE, so offseason.py must have run BEFORE this: the bonus is
        computed from the finished season's own pages, and this is the moment they stop
        describing the season that just ended.
        """
        # THE PANEL HAS TO BE SHOWING FIRST. It replaces the sim buttons only once the calendar
        # reaches it - 6/21 in the rehearsal - and until then these same coordinates are SIM
        # MONTH and friends. Pressed early on the live saves, "END SEASON" was SIM MONTH and
        # quietly simmed sixteen idle days instead, on all three leagues. The calendar moved,
        # so a check for "did anything happen" was satisfied by the wrong thing happening.
        self.advance_to_offseason(log=log)

        seen = []
        for label, xy, phase in (("END SEASON", HOTSEAT_END_SEASON, None),
                                 ("OFFSEASON", HOTSEAT_OFFSEASON, None),
                                 ("HIRE STAFF", HOTSEAT_HIRE_STAFF, True),
                                 ("TRAINING CAMPS", HOTSEAT_TRAINING_CAMPS, None)):
            self.click(NAV_HOT_SEAT, 3)
            before, stage_before = self._date_signature(), self._stage_signature()
            self.click(xy, wait)
            if phase:
                # PROCESS ALL, then the same button as PROCEED. Two presses, each confirmed by
                # the screen changing, because a phase left half-done looks identical to one
                # that never started.
                self.click(PHASE_PROCESS_ALL, wait)
                self.click(PHASE_PROCESS_ALL, wait)
            self._settle_dialogs(grace=3, timeout=60)
            moved = self._date_signature() != before
            staged = self._stage_signature() != stage_before
            seen.append((label, moved, staged))
            log(f"   {label}: calendar {'moved' if moved else 'unchanged'}, "
                f"stage {'changed' if staged else 'unchanged'}")
            # EVERY step of the rollover changes the stage - POSTSEASON, OFFSEASON, STAFF
            # HIRING, TRAINING, PRESEASON. A step that moves the calendar without changing the
            # stage is a sim button being pressed by mistake, which is exactly what happened.
            if not staged:
                raise DriverError(
                    f"{label} did not change the stage (calendar {'moved' if moved else 'did not move'}). "
                    "That is what pressing a sim button by mistake looks like; the offseason "
                    "panel is probably not showing.")
        return seen

    def advance_to_offseason(self, limit=90, log=print):
        """Sim day by day until the offseason panel replaces the sim buttons.

        The panel appears on a fixed date - 6/21 in the rehearsal, 51 idle days after the final
        - and the only way to know it has arrived is that SIM DAY stops moving the calendar,
        which is precisely what sim_days already raises on.
        """
        try:
            self.sim_days(limit)
        except DriverError as exc:
            log(f"   reached the offseason panel: {exc}")
            return True
        raise DriverError(
            f"simmed {limit} days without reaching the offseason panel - the season may not be "
            "over, or the panel is somewhere this does not know about.")

    # window-relative box around the STAGE label and its progress bar, bottom left
    STAGE_BOX = (20, 712, 175, 742)

    def _stage_signature(self):
        return self._grab().crop(self.STAGE_BOX).tobytes()

    def sim_preseason(self, wait=90):
        """Blast through the preseason so the regular season can start.

        A freshly created league opens in Preseason and SIM DAY only advances exhibition games;
        nothing lands in the standings until this button has been pressed.
        """
        self.click(NAV_HOT_SEAT, 3)
        self.click(HOTSEAT_SIM_PRESEASON, wait)
        self.dismiss_all()

    def save_game(self, wait=15, path=None):
        """Top-bar SAVE; the name box is prefilled with the loaded save's name.

        With `path` (the league.dat being written) this waits for THE FILE'S TIMESTAMP TO MOVE
        rather than for a fixed sleep, and raises if it never does. That is both faster and
        stricter: a save that silently did not happen used to look exactly like one that did,
        and the next step would export or roll over a league whose work was still only in
        memory.
        """
        limit = max(SAVE_LIMIT_FLOOR, wait * 6)
        before = self._file_mark(path)
        self.click(TOP_SAVE, 0)
        self._wait_until_still(settle=0.4, timeout=15, poll=0.1)   # the name box coming up
        self.click(SAVE_NAME_OK, 0)
        if before is None:
            self._wait_until_still(settle=1.5, timeout=limit)
            return
        # Two conditions, and the second is the one that matters: the file has been touched AND
        # it has stopped growing. mtime alone moves when the write BEGINS, so waiting only for
        # that would hand a half-written league.dat to the export that comes next.
        end, last, quiet_since = time.time() + limit, before, None
        while time.time() < end:
            mark = self._file_mark(path)
            if mark != last:
                last, quiet_since = mark, time.time()
            # A full second of quiet, not less. The other waits here can be shaved because the
            # thing they watch is a screen; this one is a 6 MB file being written by a process
            # that owes us no promises about its pauses, and being wrong costs a half-written
            # league.dat handed to the export.
            elif quiet_since and time.time() - quiet_since >= 1.0:
                self._wait_until_still(settle=0.4, timeout=30, poll=0.1)
                return
            time.sleep(0.1)
        raise DriverError(f"save did not finish writing {path} within {limit}s")

    @staticmethod
    def _file_mark(path):
        """(mtime, size) for a file, or None if there is no path or nothing there yet."""
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        s = p.stat()
        return (s.st_mtime, s.st_size)

    def _wait_until_still(self, settle=1.0, timeout=60, poll=0.15):
        """Block until the window has looked the same for `settle` seconds. True if it did.

        The general form of what sim_days does per day: the game is busy while the screen is
        changing and done when it stops, which is a far better signal than any sleep somebody
        picked once and nobody measured since.
        """
        end, last, still_since = time.time() + timeout, None, None
        while time.time() < end:
            now = self._grab().tobytes()
            if now != last:
                last, still_since = now, time.time()
            elif time.time() - still_since >= settle:
                return True
            time.sleep(poll)
        return False

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
        so this retries and confirms by the file's timestamp rather than by the dialog alone.

        IT MUST LEAVE NO DIALOG OPEN. The file lands BEFORE the "File Created" box appears, so
        returning the moment the timestamp moved raced the box: dismiss_all found nothing, the
        box opened a beat later, and the next thing the driver clicked went into it instead.
        That is exactly how Nate's 21-day week died - prep simmed, saved and exported, then
        college's LOAD click hit a leftover "File Created" and the run stopped with "LOAD button
        not found on the Load Saved Game screen", three steps downstream of the cause.

        So after the file appears this waits for the box on purpose, dismisses it, and refuses
        to return while any message box is still up: a stuck dialog stops the run here, where it
        says what it is, rather than in the middle of the next league.
        """
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
                    self._settle_dialogs()
                    return target
            self.dismiss_all()
        raise DriverError(f"Output MDB did not refresh {target}")

    def _settle_dialogs(self, grace=10, timeout=30):
        """Clear every message box, INCLUDING one that has not appeared yet.

        `grace` is how long to keep watching for a box that is still on its way - the whole point
        of this, since the one that broke a week arrived after the work it announced had already
        finished. Raises rather than returning with a dialog still up.
        """
        end, quiet_since = time.time() + timeout, None
        while time.time() < end:
            if self._message_boxes():
                self.dismiss_all()
                quiet_since = None
                time.sleep(0.5)
                continue
            if quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since >= grace:
                return True
            time.sleep(0.5)
        raise DriverError(f"a message box would not close: {self._message_boxes()}")

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

        `old_boxes` drives the dialog's "Output old boxes" control. **IT IS NOT A YES/NO.** Its
        options are "No" followed by one entry per day - "After 2030-11-14", "After 2030-11-13",
        ... - so mapping it through {True: "Yes", False: "No"} could never set it to anything but
        No, and "Yes" is not among its choices at all. That is why every previous attempt to test
        the box-score question proved nothing: the control the experiment turned on could not be
        turned on.

        Pass True for the OLDEST date offered (every box score the game still has), False for
        "No", or an exact option string. Measured 2026-09-19: setting it to the oldest date wrote
        55 real box scores - quarter scores and full player lines - into html/boxes/.

        THE WINDOW IS ROLLING, about 31 days back from the save's own current date, and that is
        the whole constraint: box scores are available for RECENT games only. There is no
        "everything ever" option, so they have to be exported while they are still inside it.
        """
        out = DOCS / "leaguedata" / save_name / "html"
        before = max((p.stat().st_mtime for p in out.glob("*.htm")), default=0) if out.exists() else 0
        self._open_html_screen()

        yes_no = {True: "Yes", False: "No"}
        # "Output old boxes" is a DATE list, not a Yes/No - see the docstring. True means the
        # oldest date it offers, which is every box score the game still holds. Read off the
        # control itself rather than computed from a calendar: the window moves with the save's
        # own date, and a date we constructed would be one it does not offer.
        if old_boxes is True:
            try:
                old = list(self._control_at(self.HTML_OLD_BOXES).item_texts())
                boxes_want = old[-1] if len(old) > 1 else "No"
            except Exception:
                boxes_want = "No"
        elif old_boxes in (False, None):
            boxes_want = "No"
        else:
            boxes_want = str(old_boxes)
        for rel, want in ((self.HTML_PLAYER_PAGES, yes_no[player_pages]),
                          (self.HTML_COACH_PAGES, yes_no[coach_pages]),
                          (self.HTML_BOX_LINKS, yes_no[box_links]),
                          (self.HTML_OLD_BOXES, boxes_want)):
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
