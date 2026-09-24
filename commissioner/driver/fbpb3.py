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
from datetime import date, timedelta
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
HOTSEAT_SIM_TO_GAME = (794, 651)
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
# EVERY DEADLINE IN THIS FILE IS time.monotonic(), never time.time(). This machine runs
# unattended and logs itself back in, so an NTP correction lands whenever it lands: a forward
# step makes a wait expire early and report a save that never finished, a backward one makes it
# hang past its limit. Elapsed time is what all of these actually mean, and monotonic is the
# only clock that measures it.
LOAD_LIMIT_FLOOR = 30
# A responsive window that is still on the Load screen this long after LOAD has not started a
# load; a standalone CV_Pro load measured 17.8-18.8s, all of it with the window hung.
LOAD_RETRY_AFTER = 8
LOAD_MAX_CLICKS = 3
SAVE_LIMIT_FLOOR = 30
# The player export. EXPORT_GRID_LIMIT is how long the League Editor's list may take to fill on
# the biggest save; EXPORT_OPEN_LIMIT how long the Player File screen has to replace it once
# EXPORT is clicked; EXPORT_WRITE_LIMIT how long the CSV may take to be written and go quiet.
# Named so the tests can shrink them; nothing else should.
EXPORT_GRID_LIMIT = 30
EXPORT_OPEN_LIMIT = 15
EXPORT_WRITE_LIMIT = 30
EXPORT_ATTEMPTS = 3


class DriverError(Exception):
    pass


class OffseasonReached(DriverError):
    """The END SEASON button was read on screen; not a simulation timeout."""


class FBPB3:
    def __init__(self):
        self.app = None
        self.main = None
        self._hwnd_cache = None

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
        self._hwnd_cache = None          # a new window; forget the one _grab remembered
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
        end = time.monotonic() + timeout
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
            if time.monotonic() >= end:
                raise DriverError(f"could not set the dropdown at {rel} to {value!r}: "
                                  f"{last or 'it never offered any options'}")
            time.sleep(0.5)

    def dismiss_message(self, title=None, button="OK", timeout=30):
        """Wait for a standard message box, return its text, and press a button."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
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

    def _runtime_error(self):
        """The text of an FBPB3 'Run-time error' box, if one is up; otherwise None."""
        try:
            for w in self.app.windows(class_name="#32770", visible_only=True):
                body = " ".join(c.window_text() for c in w.children()
                                if c.class_name() == "Static").strip()
                if "run-time error" in body.lower():
                    return " ".join(body.split())
        except Exception:                                               # noqa: BLE001
            pass
        return None

    def _window_hung(self):
        """True while the game has stopped pumping messages - which is what a real load looks like.

        IsHungAppWindow is Windows' own verdict (no messages processed for about five seconds).
        Any failure to ask answers True, so an unanswerable question never licenses a re-click.
        """
        try:
            import ctypes
            return bool(ctypes.windll.user32.IsHungAppWindow(self.main.handle))
        except Exception:                                               # noqa: BLE001
            return True

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
        # RETRIED, like _open_html_screen: a click is not an arrival. On 2026-09-23 the first
        # load of a freshly launched game - straight after a crashed one had been killed - never
        # saw the screen open, and the whole run died on one lost click. Re-clicking TOP_LOAD
        # when the screen has not opened is harmless; it only navigates.
        for _attempt in range(3):
            self.click(TOP_LOAD, 0)
            if self._wait_for(lambda: self._load_screen_open(unknown=False), 10):
                break
            crash = self._runtime_error()
            if crash:
                raise DriverError(f"FBPB3 crashed opening the Load screen: {crash}")
            try:
                self.dismiss_all()
            except Exception:                                           # noqa: BLE001
                pass
        else:
            raise DriverError("the Load Saved Game screen never appeared after 3 clicks")
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
        deadline = time.monotonic() + limit
        self.click(LOAD_BUTTON, 0)
        # A SWALLOWED LOAD CLICK, not a slow load. Caught 2026-09-23 simming into the playoffs:
        # prep and college loaded, simmed and exported, then CV_Pro sat on the Load screen for
        # the full 90s and the run died - with the row selected and the LOAD button enabled, so
        # the one click simply never landed. The same shape as today's other lost clicks
        # (_open_html_screen, _leave_html_screen): a click is not an arrival.
        #
        # Re-clicking blindly would be dangerous, because a click queued behind a REAL load
        # fires on whatever screen comes next. So it asks the window first. The comment above
        # says what a real load looks like - VB6 stops pumping messages while it reads
        # league.dat - and Win32's IsHungAppWindow answers exactly that question. A window that
        # is responsive, still on the Load screen and still offering an enabled LOAD button
        # after LOAD_RETRY_AFTER seconds has not started loading, and is clicked again.
        clicks, retry_at = 1, time.monotonic() + LOAD_RETRY_AFTER
        while self._load_screen_open(unknown=True):
            now = time.monotonic()
            # A GAME CRASH IS NOT A SLOW LOAD. FBPB3 reports its own VB6 run-time errors in a
            # message box that sits behind the Load screen; without this the run waited the full
            # 90s and then blamed a click. Fail at once, with the game's own words.
            crash = self._runtime_error()
            if crash:
                raise DriverError(f"FBPB3 crashed loading row {row}: {crash}")
            if now > deadline:
                raise DriverError(f"row {row} was still on the Load screen {limit}s after "
                                  f"LOAD was clicked {clicks} time(s)")
            if now > retry_at and clicks < LOAD_MAX_CLICKS and not self._window_hung():
                try:
                    enabled = self._load_button().is_enabled()
                except Exception:                                       # noqa: BLE001
                    enabled = False
                if enabled:
                    self.click(LOAD_BUTTON, 0)
                    clicks += 1
                retry_at = now + LOAD_RETRY_AFTER
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
        end = time.monotonic() + timeout
        while time.monotonic() < end:
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

    def sim_days(self, n=1, timeout=20, settle=0.3, on_day=None):
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
                if self._button_text(HOTSEAT_END_SEASON) == "ENDSEASON":
                    raise OffseasonReached("END SEASON is visible; SIM DAY has ended")
                # require_idle=False: a sim day's own progress form is not a reason to hold
                # the click, and the date check below is what proves the day actually advanced.
                self._expect_button(HOTSEAT_SIM_DAY, "SIM DAY", timeout=3, require_idle=False)
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
            # Report only confirmed advances. Observers must not turn a completed day into
            # a failed sim, and must enqueue their network work rather than do it here.
            if on_day is not None:
                try:
                    on_day(day, n)
                except Exception:
                    pass

    CALENDAR_NEXT_MONTH = (941, 257)
    CALENDAR_FIRST_CELL = (764, 305)
    CALENDAR_CELL_STEP = 29
    HOTSEAT_BOTTOM_RIGHT_BOX = (850, 637, 970, 665)


    _MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
               "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")

    def _calendar_date(self):
        """The date the Hot Seat calendar is showing, or None if it cannot be read.

        The heading reads "MARCH 23, 2027". It is the only place the game states the day it is
        actually on while a fast jump is running - `_date_signature` proves the label CHANGED
        but cannot say to what, which is why the jump's progress could only ever be a lower
        bound. OCR is far too slow to poll, so the caller reads it on a timer and uses the cheap
        signature to decide it is worth reading at all.
        """
        from . import screen_text
        try:
            text = screen_text.read_raw(self._grab(self.HOTSEAT_DATE_BOX))
        except Exception:      # noqa: BLE001 - an unreadable heading is not a sim failure
            return None
        up = "".join(c if c.isalnum() else " " for c in str(text).upper()).split()
        month = day = year = None
        for word in up:
            if month is None and word in self._MONTHS:
                month = self._MONTHS.index(word) + 1
            elif word.isdigit():
                n = int(word)
                if len(word) == 4:
                    year = n
                elif day is None and 1 <= n <= 31:
                    day = n
        if month and day and year:
            try:
                return date(year, month, day)
            except ValueError:
                return None
        return None

    def sim_to_date(self, n, start_date, on_day=None, timeout=None):
        """Use the Hot Seat calendar to advance exactly ``n`` days in one FBPB action.

        FBPB's SIM TO GAME stops at the selected Hot Seat team's game. The selected date is
        therefore a fast approximation, not proof of the resulting save day: it may stop before
        that day's game, or at the team's next game when the chosen date is idle. The caller
        reads league.dat after SAVE and corrects a short landing with SIM DAY or replays an
        overshoot from its checkpoint.

        Every action is bounded by visible UI state. Each month arrow must change the calendar
        heading, the computed target cell must acquire the blue selection, SIM TO GAME must
        replace the lower-right button with STOP SIMMING, and that button must return before
        this reports success. A playoff warning is answered No and returns False so the caller
        can use the proven daily path without having moved the calendar.
        """
        try:
            current = start_date if isinstance(start_date, date) else date.fromisoformat(str(start_date))
        except (TypeError, ValueError) as exc:
            raise DriverError(f"cannot use calendar sim with start date {start_date!r}") from exc
        if n < 1:
            return True
        # Select the last requested playing date, leaving the caller to finish its games with
        # SIM DAY and verify the saved result. At the regular-season boundary, the final daily
        # click can skip an idle date while FBPB schedules the playoffs (April 19 -> April 21).
        # Neither click count nor pixel changes alone proves the saved calendar day.
        target = current + timedelta(days=n - 1)
        months = (target.year - current.year) * 12 + target.month - current.month
        if months < 0:
            raise DriverError("calendar sim cannot move backwards")

        self.click(NAV_HOT_SEAT, 0)
        self._wait_until_still(settle=0.4, timeout=15, poll=0.1)
        for _ in range(months):
            before = self._date_signature()
            self.click(self.CALENDAR_NEXT_MONTH, 0)
            if not self._wait_for(lambda: self._date_signature() != before, 3):
                raise DriverError("calendar next-month arrow did not change the displayed month")

        first_col = (date(target.year, target.month, 1).weekday() + 1) % 7
        col = (target.weekday() + 1) % 7
        row = (first_col + target.day - 1) // 7
        x = self.CALENDAR_FIRST_CELL[0] + col * self.CALENDAR_CELL_STEP
        y = self.CALENDAR_FIRST_CELL[1] + row * self.CALENDAR_CELL_STEP
        self.click((x, y), 0)
        if not self._wait_for(lambda: self._calendar_cell_selected(x, y), 3):
            raise DriverError(f"calendar did not visibly select {target.isoformat()}")

        ready = self._grab(self.HOTSEAT_BOTTOM_RIGHT_BOX).tobytes()
        progress_signature = self._date_signature()
        confirmed_steps = 0
        next_read = 0.0
        self.click(HOTSEAT_SIM_TO_GAME, 0)
        deadline = time.monotonic() + (timeout or max(60, n * 5))
        seen_busy = False
        while time.monotonic() < deadline:
            boxes = self._message_boxes()
            if boxes:
                if "Schedule Warning" in boxes and not seen_busy:
                    self.dismiss_message("Schedule Warning", button="No", timeout=3)
                    return False
                raise DriverError(f"calendar sim opened a message box: {boxes}")
            button = self._grab(self.HOTSEAT_BOTTOM_RIGHT_BOX).tobytes()
            if button != ready:
                seen_busy = True
                # FBPB repaints the visible date as it advances. We may miss dates when it runs
                # faster than the screen poll, so this is deliberately a lower bound: every new
                # signature proves at least one more day completed, and the final callback below
                # supplies the exact total. This keeps the panel and Discord alive without any
                # extra clicks while the game owns the simulation loop.
                signature = self._date_signature()
                if signature != progress_signature and confirmed_steps < n - 1:
                    progress_signature = signature
                    # THE SIGNATURE SAYS "IT MOVED", THE HEADING SAYS HOW FAR. Counting
                    # signature changes undercounts badly - FBPB advances faster than a 20 Hz
                    # poll, so a 29-day jump reported about 18 and then leapt to 29, which is
                    # what made this number untrustworthy. The heading is the truth, but OCR is
                    # far too slow to run every pass, so it runs on a timer and only when the
                    # cheap signature says something changed.
                    now = time.monotonic()
                    if now >= next_read:
                        next_read = now + 0.6
                        shown = self._calendar_date()
                        if shown is not None:
                            elapsed = (shown - current).days
                            if confirmed_steps < elapsed <= n - 1:
                                confirmed_steps = elapsed
                                if on_day is not None:
                                    try:
                                        on_day(confirmed_steps, n)
                                    except Exception:
                                        pass
            elif seen_busy:
                self._wait_until_still(settle=0.4, timeout=5, poll=0.1, cheap=True)
                if (self._grab(self.HOTSEAT_BOTTOM_RIGHT_BOX).tobytes() == ready
                        and self._calendar_cell_selected(x, y)):
                    return True
            time.sleep(0.05)
        raise DriverError(
            f"SIM TO GAME did not visibly finish after {timeout or max(60, n * 5)}s; "
            "the save was not touched")

    def _calendar_cell_selected(self, x, y):
        """The selected calendar cell has FBPB's cyan fill; ordinary cells are gray."""
        image = self._grab((x - 11, y - 11, x + 12, y + 12))
        blue = sum(1 for r, g, b in image.getdata() if b - r > 55 and g - r > 30)
        return blue >= 20

    # window-relative box around the Hot Seat calendar's date label ("MARCH 23, 2027")
    HOTSEAT_DATE_BOX = (775, 247, 935, 268)

    def _grab(self, box=None, cheap=False):
        """The main window, or one window-relative `box` of it, as a PIL image.

        TWO WAYS TO READ THE SCREEN, and the difference is most of a sim's waiting.

        PrintWindow (cheap=False) asks the window to render itself into our bitmap, so it is
        right even when something covers FBPB3 - which is why it has always been used here. It
        costs what it costs: 28 ms measured, and it always renders the WHOLE 1019x762 window
        however little of it we wanted.

        BitBlt (cheap=True) copies pixels that are already on screen, so it costs in proportion
        to the area asked for - 0.51 ms for the 160x21 date label, about 130x cheaper than a
        _grab() was - but it reads whatever is actually in front of those pixels. Covered, it
        returns the covering window.

        Measured 2026-09-20 on the title screen: PrintWindow whole window 27.7 ms, BitBlt whole
        window 10.9 ms, BitBlt the date box 0.51 ms - and PrintWindow-then-crop and BitBlt-the-box
        returned byte-identical images. So the cheap read is used to WATCH, and PrintWindow is
        still what CONFIRMS anything the run acts on. See _wait_for_new_day.
        """
        import win32gui
        import win32ui
        from PIL import Image
        # `self.main` is a pywinauto WindowSpecification, and .handle RESOLVES it - a window
        # search, every time, measured at about 4 ms. On a path called twenty times a simulated
        # day that is the largest cost left in a cheap capture. The handle of a window that is
        # still open never changes, so it is remembered and checked with IsWindow (a pointer
        # lookup) rather than searched for again; a window that has gone away, or a game that has
        # been relaunched, falls back to asking pywinauto properly.
        hwnd = getattr(self, "_hwnd_cache", None)
        if not hwnd or not win32gui.IsWindow(hwnd):
            hwnd = self._hwnd_cache = self.main.handle
        # LAZILY, because main.rectangle() is a pywinauto round trip that measured 7.6 ms - more
        # than fourteen times the BitBlt it was being fetched for. Reading the date box needs
        # only the box, so on the hot path the window is never measured at all: that one line
        # was costing more than every other part of a cheap capture put together.
        x, y = (box[0], box[1]) if box else (0, 0)
        if cheap and box:
            full_w, full_h = box[2] - box[0], box[3] - box[1]
        else:
            r = self.main.rectangle()
            full_w, full_h = r.width(), r.height()   # PrintWindow renders all of it regardless
        w, h = (box[2] - box[0], box[3] - box[1]) if box else (full_w, full_h)
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, full_w, full_h)
        mem.SelectObject(bmp)
        try:
            if cheap:
                mem.BitBlt((0, 0), (w, h), src, (x, y), 0x00CC0020)      # SRCCOPY
            else:
                ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)
            info = bmp.GetInfo()
            image = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                     bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1)
            return image.crop(box) if (box and not cheap) else image
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)

    def _date_signature(self, cheap=False):
        return self._grab(self.HOTSEAT_DATE_BOX, cheap).tobytes()

    def _wait_for_new_day(self, before, timeout, settle):
        """True once the date label differs from `before` and the window has been still for
        `settle` seconds (capped at 5 s, so a busy screen cannot hold the run). False on timeout.

        WATCHED CHEAPLY, CONFIRMED EXPENSIVELY. This used to PrintWindow the entire window every
        0.1 s - about thirteen full captures per league-day at 66 ms each, which over the 177
        league-days of a 59-day three-league week came to something like two and a half minutes
        of a sim doing nothing but photographing a screen that had not changed.

        Now the 160x21 date label is watched with a 0.51 ms BitBlt, and NOTHING is acted on
        until a PrintWindow says the same thing. That matters because BitBlt reads the pixels
        actually on screen: if another window covers FBPB3 it returns the covering window, which
        would otherwise read as a date that changed when it had not - and a day wrongly called
        finished is a day never simmed.

        So a cheap read that DISAGREES only earns an authoritative second opinion, at most twice
        a second, and the run advances on the authoritative one. A cheap read that agrees is not
        trusted either: PrintWindow checks anyway once a second, because a window covered by
        something static would otherwise never look like it had changed at all, and the day would
        time out and be clicked a second time.

        The upshot: exactly the same captures decide the outcome as before. Only the waiting in
        between got cheap.
        """
        end = time.monotonic() + timeout
        # The cheap read is believed until it is caught being wrong. `quiet_until` is set ONLY
        # by a confirmation that failed - so in the ordinary case a changed date is confirmed
        # the instant it is seen, with no rate limit standing in front of it, and only a screen
        # that has already lied once gets asked more slowly. Rate-limiting every confirmation
        # instead would put up to half a second back onto every single day.
        quiet_until = 0.0
        next_periodic = time.monotonic() + 1.0
        while time.monotonic() < end:
            now = time.monotonic()
            suspect = self._date_signature(cheap=True) != before and now >= quiet_until
            if suspect or now >= next_periodic:
                next_periodic = now + 1.0
                if self._date_signature() != before:
                    self._wait_until_still(settle=settle, timeout=5, poll=0.1, cheap=True)
                    return True
                if self._button_text(HOTSEAT_END_SEASON) == "ENDSEASON":
                    raise OffseasonReached("END SEASON replaced SIM DAY")
                if self._message_boxes():
                    raise DriverError("A message box interrupted SIM DAY")
                quiet_until = now + 0.5
            time.sleep(0.05)
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
        # THE TAIL OF THE OFFSEASON IS DRIVEN BY WHAT THE BUTTON SAYS, not by a fixed order.
        #
        # Measured on a clone of the live pro save, 2026-09-22: with Finances Off the sequence
        # ends END SEASON -> OFFSEASON -> HIRE STAFF -> TRAINING CAMPS. With FULL FINANCES there
        # is a FREE AGENCY stage in between, and it occupies THE SAME PIXEL as TRAINING CAMPS.
        #
        # An earlier version of this decided in advance whether free agency was there by reading
        # that pixel before navigating to the Hot Seat - so it read whatever screen HIRE STAFF
        # happened to leave up, and a blank read was taken as "no free agency". Skipping it then
        # sent TRAINING CAMPS at a button that said FREE AGENCY. _expect_button refuses to click
        # on a mismatch, so that failed loudly rather than doing something strange, but it still
        # stranded the league mid-offseason. Reading the button AFTER navigating, and treating
        # "unreadable" as "wait longer" rather than "not there", is what makes that impossible.
        head = (("END SEASON", HOTSEAT_END_SEASON, False, 120),
                ("OFFSEASON", HOTSEAT_OFFSEASON, False, 120),
                ("HIRE STAFF", HOTSEAT_HIRE_STAFF, True, 120))
        # The offseason panel REPLACES the sim buttons, so when it gives way to one of them the
        # rollover is over - HOTSEAT_TRAINING_CAMPS and HOTSEAT_SIM_TO_PLAYOFFS are the same
        # pixel. Treating that as an unknown word would have failed a rollover the game had
        # finished, and seasonflow's `finally: exit_game(save=False)` would then throw it away.
        over = ("SIMTOPLAYOFFS", "SIMDAY", "SIMPRESEASON", "SIMTOGAME")
        finished = False
        for label, xy, phase, proceed_wait in head + ((None, HOTSEAT_TRAINING_CAMPS, None, 0),) * 4:
            if finished:
                break
            # ONE nav click per stage. Two in a row land inside the OS double-click interval, so
            # Windows delivers a DblClick rather than a second Click.
            self.click(NAV_HOT_SEAT, 0)
            if label is None:
                word = self._read_button(xy, timeout=180)
                if self._same(word, "TRAININGCAMPS"):
                    label, phase, proceed_wait = "TRAINING CAMPS", False, 120
                elif self._same(word, "FREEAGENCY"):
                    # NEVER TWICE. _wait_stage_change only proves a crop of the stage box
                    # changed, which a repaint can satisfy without the stage advancing - and a
                    # button-driven loop will then happily drive the same stage again, at 60s +
                    # 900s a go, before failing. A stage we have already run and which is still
                    # on the button means the wait lied, and that is worth saying plainly.
                    if any(done == "FREE AGENCY" for done, _, _ in seen):
                        raise DriverError(
                            "FREE AGENCY is still on the button after it was already run and "
                            "reported complete; the stage did not really advance. No further "
                            "click was sent.")
                    label, phase, proceed_wait = "FREE AGENCY", True, 900
                elif any(self._same(word, w) for w in over):
                    log(f"   the offseason panel has given way to {word}; the rollover is over")
                    finished = True
                    break
                else:
                    raise DriverError(
                        f"offseason stalled at {xy}: the button reads {word!r}, which is not a "
                        "stage this knows. No further click was sent.")
            # 15s was the default and it is not enough here: each of these lands straight after
            # the previous phase's work, and the scouting popup on a finished season outlives it.
            self._expect_button(xy, label, timeout=180)
            before, stage_before = self._date_signature(), self._stage_signature()
            self.click(xy, 0)
            if phase:
                # THE ACTION BUTTON IS NOT ALWAYS CALLED "PROCESS ALL". Hire Staff runs one
                # batch and says PROCESS ALL; free agency runs a series of DAYS and says RUN ALL
                # DAYS on the same pixel. Both then become PROCEED. Waiting for a literal
                # "PROCESS ALL" stalls on free agency for the whole timeout and then gives up on
                # a screen that was working perfectly.
                self._expect_action_button(timeout=60)
                self.click(PHASE_PROCESS_ALL, 0)
                # PER STAGE, not per file. Hiring staff keeps the 120s it always had; only free
                # agency, which signs a whole league a day at a time, gets the long allowance.
                # Granting 900s to every phase multiplied the worst case across three leagues
                # into hours, and a rollover that runs past ~22:40 costs that night's offsite
                # backup, which defers while FBPB3 is open.
                self._expect_button(PHASE_PROCESS_ALL, "PROCEED", timeout=proceed_wait)
                self.click(PHASE_PROCESS_ALL, 0)
            # A stage slow enough to need a long PROCEED is slow enough to repaint slowly too;
            # granting one and not the other is how the 900s allowance ended in a 120s death.
            self._wait_stage_change(stage_before,
                                    timeout=max(60, wait * 10, proceed_wait // 3))
            moved = self._date_signature() != before
            seen.append((label, moved, True))
            log(f"   {label}: confirmed stage change; calendar {'moved' if moved else 'unchanged'}")
            if label == "TRAINING CAMPS":
                finished = True
        # DONE MEANS THE PANEL LET GO, not that one particular button was pressed. CONVENTIONS
        # records a sequence with no TRAINING CAMPS in it at all, measured on a different league,
        # so demanding that exact stage would fail a rollover the game completed.
        if not finished:
            raise DriverError("the offseason never finished: neither TRAINING CAMPS nor a sim "
                              "button was reached")
        return seen

    def advance_to_offseason(self, limit=90, log=print):
        """Stop only when END SEASON is actually read, never on an arbitrary timeout."""
        try:
            self.sim_days(limit)
        except OffseasonReached:
            log("   reached the offseason panel: END SEASON detected")
            return True
        raise DriverError(
            f"simmed {limit} days without reaching the offseason panel - the season may not be "
            "over, or the panel is somewhere this does not know about.")

    def _button_text(self, xy):
        from . import screen_text
        x, y = xy
        image = self._grab((x - 57, y - 12, x + 57, y + 12))
        signature = image.tobytes()
        cache = getattr(self, "_button_cache", {})
        if xy in cache and cache[xy][0] == signature:
            return cache[xy][1]
        try:
            text = screen_text.read(image)
        except Exception as exc:
            raise DriverError(f"Cannot read the button at {xy}: {exc}") from exc
        cache[xy] = (signature, text)
        self._button_cache = cache
        return text

    # A progress form is roughly this big. Measured from the end-season scouting popup; the
    # range is deliberately wider than one dialog so a near neighbour still counts.
    _POPUP_W = (250, 700)
    _POPUP_H = (100, 420)

    def _looks_like_popup(self, window):
        try:
            rect = window.rectangle()
        except Exception:      # noqa: BLE001 - a window that vanished mid-check is not a popup
            return False
        return (self._POPUP_W[0] <= rect.width() <= self._POPUP_W[1]
                and self._POPUP_H[0] <= rect.height() <= self._POPUP_H[1])

    def _progress_popup_visible(self):
        """Is one of FBPB3's progress forms still up?

        END SEASON changes the stage *before* its scouting popup closes, and the underlying
        button labels stay readable the whole time - so the label alone is not proof the work
        finished. That is what this is for.

        IT MUST BE A POPUP, NOT MERELY A ThunderRT6 WINDOW. The first check here used to return
        True for ANY visible top-level window of that class other than the main one, with no size
        test at all, while the owned-child check right below it did filter by size. FBPB3 keeps
        such a window around on the offseason screen, so the gate never cleared: the 2029 rollover
        died with "END SEASON was readable the whole time, but a progress popup never cleared
        within 15s" after the store had already promoted everybody. Both paths now ask the same
        question - is there a window the size and shape of a progress form - instead of one of
        them asking merely whether a window exists.
        """
        try:
            # Same race as the owned-child scan below: listing wraps every handle it found.
            tops = self.app.windows(visible_only=True)
        except Exception:                                               # noqa: BLE001
            return True
        for window in tops:
            if window.handle == self.main.handle:
                continue
            try:
                if not window.class_name().startswith("ThunderRT6"):
                    continue
            except Exception:  # noqa: BLE001
                continue
            if self._looks_like_popup(window):
                return True
        # Some VB6 progress forms are owned child windows, not enumerated top-level dialogs.
        #
        # A WINDOW CAN DIE BETWEEN BEING LISTED AND BEING WRAPPED. pywinauto enumerates the
        # handles, then builds a wrapper per handle - and a progress form closing in that gap
        # raised InvalidWindowHandle straight out of the college rollover of the 2033 offseason,
        # throwing away the rolled-over league. A form vanishing mid-scan is the popup finishing;
        # answer "still busy" and let the next poll look again.
        try:
            for window in self.main.descendants(class_name="ThunderRT6FormDC"):
                if window.is_visible() and self._looks_like_popup(window):
                    return True
        except Exception:                                               # noqa: BLE001
            return True
        return False

    # These buttons are owner-drawn and read by OCR, so a letter comes back wrong from time to
    # time - RUN ALL DAYS was read as 'RUNAUDAYS' once, the "LL" as a "U". An exact comparison
    # then rejects a button that says precisely what it should. Matching on the shape of the
    # word survives that without loosening anything that matters: no two buttons on these
    # screens are one OCR slip apart.
    # Only words actually OBSERVED on these screens. PROCESS DAY and RUN DAY were guesses, and
    # they were dangerous ones: the caller presses this button ONCE and then waits for PROCEED,
    # which a per-day button never becomes, and "RUNDAY" is a single substitution from "SUNDAY" -
    # a word the free-agency screen, which runs a series of DAYS, can genuinely paint into this
    # read box. Accepting it would have clicked a pixel whose contents were unknown.
    _ACTION_WORDS = ("PROCESSALL", "RUNALLDAYS")

    @staticmethod
    def _word(text):
        return "".join(c for c in (text or "").upper() if c.isalpha())

    def _read_button(self, xy, timeout=60):
        """The button's word, waiting through BLANK reads while the screen paints.

        A blank read is "not yet", never "nothing there". Conflating the two is what made an
        optional stage look absent because the window had not finished repainting, and a stage
        wrongly judged absent is a stranded offseason.
        """
        end = time.monotonic() + timeout
        word, steady, blanks = "", "", 0
        while time.monotonic() < end:
            if self._message_boxes():
                raise DriverError(f"a message box is open while reading the button at {xy}")
            try:
                word = self._word(self._button_text(xy))
            except Exception:                                           # noqa: BLE001
                word = ""
            if not word:
                blanks += 1
                steady = ""
            elif self._progress_popup_visible():
                steady = ""
            elif word == steady:
                # TWICE, NOT ONCE. This button's read box overlaps the phase screen's action
                # button, and the Hot Seat click before it does not wait - so the first
                # non-blank read can be the PREVIOUS screen, or a half-painted word. Either is
                # non-blank, and committing to it made the caller raise "offseason stalled" at
                # a league that was perfectly fine. The same word twice is cheap and settles it.
                return word
            else:
                steady = word
            time.sleep(0.25)
        # SAY WHICH HALF FAILED - the defect _expect_button was fixed for. "the button reads ''"
        # blamed the stage when the truth was that nothing was readable for three minutes.
        if not word:
            raise DriverError(f"nothing was readable at {xy} for {timeout}s ({blanks} blank "
                              "reads); the screen never painted. No further click was sent.")
        raise DriverError(f"the button at {xy} never settled within {timeout}s; last read "
                          f"{word!r}. No further click was sent.")

    def _same(self, got, want):
        """One button word against another, tolerating a single OCR slip."""
        got, want = self._word(got), self._word(want)
        return bool(got) and (got == want or self._close(got, want))

    @staticmethod
    def _close(a, b):
        """Same length and at most one differing character, or one a prefix-ish of the other."""
        if abs(len(a) - len(b)) > 2:
            return False
        if len(a) == len(b):
            return sum(1 for x, y in zip(a, b) if x != y) <= 1
        short, long_ = (a, b) if len(a) < len(b) else (b, a)
        if len(short) < 4:
            return False
        # A TRUNCATION IS THE COMMONEST SLIP, because the crop is fixed and the word is not:
        # 'TRAININGCAMP' for TRAINING CAMPS, 'FREEAGENC' for FREE AGENCY. The old rule required
        # the ends to match, which a truncation can never do. Worse, _word strips non-letters,
        # so a final S read as a 5 BECOMES a truncation.
        if long_.startswith(short):
            return True
        return long_.startswith(short[:4]) and long_.endswith(short[-4:])

    def _expect_action_button(self, timeout=60):
        """Wait for the phase screen's action button, whatever this phase calls it."""
        end = time.monotonic() + timeout
        seen = ""
        while time.monotonic() < end:
            # BOTH GATES THE OLD _expect_button CALL HAD. Without the message-box check a modal
            # is reported as a label mismatch minutes later instead of by name immediately;
            # without the popup check this returns while the scouting form is still up - and
            # roll_over_season's own comment says that popup OUTLIVES the stage change and the
            # label stays readable underneath it the whole time. The click that followed would
            # land on the popup, be swallowed, and strand the phase.
            boxes = self._message_boxes()
            if boxes:
                raise DriverError(f"Waiting for the phase action button, but a message box is "
                                  f"open: {boxes}")
            try:
                seen = self._word(self._button_text(PHASE_PROCESS_ALL))
            except Exception:                                           # noqa: BLE001
                seen = ""
            if (seen and any(self._close(seen, w) or seen == w for w in self._ACTION_WORDS)
                    and not self._progress_popup_visible()):
                return seen
            time.sleep(0.5)
        raise DriverError(f"no action button on the phase screen at {PHASE_PROCESS_ALL}; "
                          f"read {seen!r}, expected one of {self._ACTION_WORDS}")

    def _expect_button(self, xy, label, timeout=15, require_idle=True):
        """Wait until the button at `xy` really says `label`.

        `require_idle` ALSO waits for the progress popup to go, which is what an offseason phase
        needs: END SEASON changes the stage before its scouting popup closes, and the underlying
        label is readable the whole time, so the label alone is not proof the work finished.

        IT IS WRONG FOR A SIM DAY, and turning it on for every button is what stopped a sim on
        2026-09-21 with "Expected SIM DAY at (794, 585); read 'SIMDAY'". Those two strings are
        equal - `screen_text.read` already normalises, and normalize("SIM DAY") is "SIMDAY" - so
        the label matched on the first read every time. The popup half never cleared: a normal
        sim day paints its own progress form, which `_progress_popup_visible` cannot tell from
        the end-season one, so the 3 s wait always expired and the day's click was never sent.
        The daily loop already proves its work a better way, by waiting for the DATE to move.
        """
        from .screen_text import normalize
        end = time.monotonic() + timeout
        last, busy = "", False
        while time.monotonic() < end:
            boxes = self._message_boxes()
            if boxes:
                raise DriverError(f"Waiting for {label}, but a message box is open: {boxes}")
            last = self._button_text(xy)
            # TOLERANT, OR THE TOLERANCE ELSEWHERE IS DECORATION. The offseason tail identifies a
            # stage with _same and then lands here, which compared EXACTLY - so the one misread
            # the tolerance exists to survive was accepted upstream and rejected here, after the
            # full timeout. Recovery was impossible: _button_text caches on the image bytes and
            # WinRT OCR is deterministic, so a static button returns the identical misread for
            # every poll. A near-match is announced rather than swallowed.
            matched = last == normalize(label)
            # Through the CLASS, not through self: _expect_button is exercised by test
            # doubles that implement only _button_text and the two gates, and they have
            # no business growing a pair of pure string helpers to stay usable.
            if not matched and last and FBPB3._close(FBPB3._word(last), FBPB3._word(label)):
                print(f"button at {xy}: read {last!r}, accepting as {normalize(label)!r} "
                      "(single-character OCR slip)", flush=True)
                matched = True
            busy = require_idle and self._progress_popup_visible()
            if matched and not busy:
                return
            time.sleep(0.2)
        # SAY WHICH HALF FAILED. The old message printed only the text, so a wait that timed out
        # on the popup read as a label mismatch and sent everybody after the wrong thing.
        if last == normalize(label):
            raise DriverError(
                f"{label} at {xy} was readable the whole time, but a progress popup never "
                f"cleared within {timeout}s. No further click was sent.")
        raise DriverError(f"Expected {label} at {xy}; read {last!r}. No further click was sent.")

    def _wait_stage_change(self, before, timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._message_boxes():
                raise DriverError("A dialog blocked the offseason stage transition")
            if not self._progress_popup_visible() and self._stage_signature() != before:
                if self._wait_until_still(settle=0.4, timeout=5, poll=0.1):
                    if not self._progress_popup_visible() and self._stage_signature() != before:
                        return
            time.sleep(0.2)
        raise DriverError("The offseason stage did not change; further steps were stopped")

    # Stage text only: the animated progress bar is not a phase change.
    STAGE_BOX = (20, 712, 175, 728)

    def _stage_signature(self):
        return self._grab().crop(self.STAGE_BOX).tobytes()

    def sim_preseason(self, wait=90):
        """Blast through the preseason so the regular season can start.

        A freshly created league opens in Preseason and SIM DAY only advances exhibition games;
        nothing lands in the standings until this button has been pressed.
        """
        self.click(NAV_HOT_SEAT, 0)
        self._expect_button(HOTSEAT_SIM_PRESEASON, "SIM PRESEASON")
        before = self._stage_signature()
        self.click(HOTSEAT_SIM_PRESEASON, 0)
        self._wait_stage_change(before, timeout=max(120, wait * 2))
        self._expect_button(HOTSEAT_SIM_DAY, "SIM DAY")

    def save_game(self, wait=15, path=None):
        """Top-bar SAVE; the name box is prefilled with the loaded save's name.

        With `path` (the league.dat being written) this waits for THE FILE'S TIMESTAMP TO MOVE
        rather than for a fixed sleep, and raises if it never does. That is both faster and
        stricter: a save that silently did not happen used to look exactly like one that did,
        and the next step would export or roll over a league whose work was still only in
        memory.
        """
        if path is None:
            raise DriverError("save_game requires the target league.dat path")
        # ONE RETRY, because the caller's `finally` exits WITHOUT saving: a stalled save throws
        # away everything in memory. On 2026-09-23 pro's whole 2033 rollover (free agency,
        # camps, preseason) was lost that way - the SAVE click produced no write at all, the file
        # never moved in 90s - and the identical sequence re-run by hand saved first time. So a
        # stall is photographed for next time, any dialog is cleared, and SAVE is pressed again.
        # A VB6 run-time error box is not retried: that game is not coming back.
        try:
            return self._save_once(wait, path)
        except DriverError as exc:
            if "did not finish writing" not in str(exc):
                raise
            note = self._save_stall_evidence(path)
            if note.get("runtime_error"):
                raise DriverError(f"{exc}; FBPB3 showed a run-time error: "
                                  f"{note['runtime_error']}") from exc
            self.dismiss_all()
            time.sleep(1)
            try:
                return self._save_once(wait, path)
            except DriverError as again:
                raise DriverError(f"{again} (second attempt; first stall evidence: "
                                  f"{note})") from again

    def _save_stall_evidence(self, path):
        """Screenshot and dialogs at the moment a save stalled, into logs/. Never raises."""
        note = {}
        try:
            logs = Path(__file__).resolve().parents[2] / "logs"
            logs.mkdir(exist_ok=True)
            shot = logs / f"save-stall-{time.strftime('%Y%m%d-%H%M%S')}.png"
            self.screenshot(str(shot))
            note["screenshot"] = str(shot)
        except Exception as exc:                                        # noqa: BLE001
            note["screenshot"] = f"failed: {exc}"
        for key, read in (("dialogs", self._message_boxes), ("runtime_error", self._runtime_error)):
            try:
                note[key] = read()
            except Exception as exc:                                    # noqa: BLE001
                note[key] = f"unreadable: {exc}"
        return note

    def _save_once(self, wait, path):
        limit = max(SAVE_LIMIT_FLOOR, wait * 6)
        before = self._file_mark(path)
        self.click(TOP_SAVE, 0)
        if not self._wait_until_still(settle=0.4, timeout=15, poll=0.1):
            raise DriverError("save name dialog did not settle")
        self.click(SAVE_NAME_OK, 0)
        # Two conditions, and the second is the one that matters: the file has been touched AND
        # it has stopped growing. mtime alone moves when the write BEGINS, so waiting only for
        # that would hand a half-written league.dat to the export that comes next.
        end, last, quiet_since = time.monotonic() + limit, before, None
        while time.monotonic() < end:
            mark = self._file_mark(path)
            if mark != last:
                last, quiet_since = mark, time.monotonic()
            # A full second of quiet, not less. The other waits here can be shaved because the
            # thing they watch is a screen; this one is a 6 MB file being written by a process
            # that owes us no promises about its pauses, and being wrong costs a half-written
            # league.dat handed to the export.
            elif mark is not None and mark[1] > 0 and quiet_since is not None and time.monotonic() - quiet_since >= 1.0:
                if not self._wait_until_still(settle=0.4, timeout=30, poll=0.1):
                    raise DriverError("save window did not settle after writing")
                if self._file_mark(path) == mark:
                    return
                quiet_since = None
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

    def _wait_until_still(self, settle=1.0, timeout=60, poll=0.15, cheap=False):
        """Block until the window has looked the same for `settle` seconds. True if it did.

        The general form of what sim_days does per day: the game is busy while the screen is
        changing and done when it stops, which is a far better signal than any sleep somebody
        picked once and nobody measured since.

        `cheap` reads the screen with BitBlt (10.9 ms for the whole window against 27.7 ms for
        PrintWindow). This is a TIMING signal, not a decision: nothing is accepted or rejected on
        the strength of it, so reading a covering window instead costs at worst a click that
        lands early - and sim_days already clicks a second time and re-confirms the date when
        one is swallowed. The checks that decide anything stay on PrintWindow.
        """
        end, last, still_since = time.monotonic() + timeout, None, None
        while time.monotonic() < end:
            now = self._grab(cheap=cheap).tobytes()
            if now != last:
                last, still_since = now, time.monotonic()
            elif time.monotonic() - still_since >= settle:
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
        end = time.monotonic() + 30
        while self.is_running() and time.monotonic() < end:
            time.sleep(0.5)
        if self.is_running():
            self.kill()

    def output_mdb(self, save_name, attempts=1, timeout=60, max_seconds=120):
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
        before = self._file_mark(target)
        failure = "did not start"
        for attempt in range(attempts):
            # CLEAR THE WAY FIRST, exactly as `_open_html_screen` does and for the reason its
            # docstring gives: "a click lands on whatever window is topmost, so a leftover message
            # box from the previous league swallows it and the game simply stays where it was".
            #
            # This step had no such guard, and that asymmetry is the whole fault. Inside a Sim
            # Week the MDB export follows html_output, which ends by dismissing its own completion
            # box - and whatever arrives after that swallowed the Tools click. The export never
            # started, so the file never moved, so the no-progress budget ran to the end and the
            # run reported "no MDB for pro". Measured 2026-09-22, twice in the same evening:
            #
            #   inside a Sim Week, straight after html_output ....... no file progress at all
            #   standalone - launch, load, Output MDB, nothing else .. 24.9s, then 23.8s, complete
            #
            # A standalone run has no leftover box, which is exactly why it never reproduced. The
            # budget was raised to 300s chasing this and made it worse: same failure, three more
            # minutes of waiting. It has been put back.
            #
            # DISMISSING HERE IS SAFE, and the warning below is about a different moment. That one
            # says never to dismiss while OUR export is running, because dismiss_all() presses the
            # first of OK/No/Cancel and would cancel it. Nothing of ours is running yet.
            # BEST EFFORT. Clearing the way must never stop the attempt that follows: if there
            # is nothing to dismiss, or no window to ask, the click is still worth making. The
            # old behaviour - click blind - is what this falls back to.
            try:
                self.dismiss_all()
            except Exception:                                           # noqa: BLE001
                pass
            self.click(TOP_TOOLS, 2)
            self.click(TOOLS_OUTPUT_MDB, 2)
            # THE BUDGET HAS TO EXCEED THE SLOWEST LEAGUE, and 180s did not. Measured on
            # 2026-09-20: prep's export took 19.5s, college's 170s and PRO'S 211s - pro has
            # twenty teams and a 9.8 MB database. So pro timed out every single time, one
            # second of patience short of the truth, and the retry below then made it worse:
            # dismiss_all() presses the first of OK/No/Cancel on whatever is open, which
            # cancels the export still running underneath. Four attempts, four cancellations,
            # twelve minutes, and a "did not refresh" at the end of it. Caught live, mid-run,
            # with pro's MDB still carrying the previous week's timestamp.
            # `timeout` is a NO-PROGRESS budget. Jet grows/truncates the MDB while working, so
            # every observed size/mtime change renews it, up to the separate absolute deadline.
            # File churn is useful evidence, but it is not permission to hold a completed Sim
            # Week forever. The Pro exporter that prompted this guard kept touching the MDB for
            # more than nine minutes without ever raising its completion box. The save and HTML
            # were already good; only this optional table was holding snapshots, points and the
            # public site hostage. Keep both limits: inactivity catches a dead export, and the
            # absolute cap catches an exporter that churns forever.
            started = time.monotonic()
            progress_deadline = started + timeout
            absolute_deadline = started + max_seconds
            last = before
            while time.monotonic() < min(progress_deadline, absolute_deadline):
                # Killing or crashing FBPB3 used to leave this loop alive for the full timeout,
                # followed by two more attempts. Once the process is gone no completion dialog
                # can arrive and retrying clicks against a dead window cannot help.
                if not self.is_running():
                    raise DriverError(f"FBPB3 exited while exporting {target}")
                created = False
                try:
                    self.dismiss_message("File Created", timeout=2)
                    created = True
                except DriverError:
                    pass
                mark = self._file_mark(target)
                if mark is not None and mark != last:
                    last = mark
                    progress_deadline = time.monotonic() + timeout
                if mark is not None and mark != before and created:
                    self._settle_dialogs()
                    return target
            if time.monotonic() >= absolute_deadline:
                failure = f"exceeded its {max_seconds}s absolute limit"
            else:
                failure = f"made no file progress for {timeout}s"
            # Only tidy up if something is actually open. An unconditional dismiss_all is how
            # a slow export got killed by the thing meant to rescue it.
            if self._message_boxes():
                self.dismiss_all()
            if attempt + 1 < attempts and not self.is_running():
                raise DriverError(f"FBPB3 exited while exporting {target}")
        raise DriverError(f"Output MDB {failure} on {target} across {attempts} attempt(s)")

    def _settle_dialogs(self, grace=10, timeout=30):
        """Clear every message box, INCLUDING one that has not appeared yet.

        `grace` is how long to keep watching for a box that is still on its way - the whole point
        of this, since the one that broke a week arrived after the work it announced had already
        finished. Raises rather than returning with a dialog still up.
        """
        end, quiet_since = time.monotonic() + timeout, None
        while time.monotonic() < end:
            if self._message_boxes():
                self.dismiss_all()
                quiet_since = None
                time.sleep(0.5)
                continue
            if quiet_since is None:
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= grace:
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
        end = time.monotonic() + timeout
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
            if time.monotonic() >= end:
                break
            time.sleep(0.5)
        raise DriverError(f"no control at window-relative {rel} after {timeout}s"
                          + (f" ({last})" if last else ""))

    def _set_export_text(self, box, value):
        """Skip matching styles; set the native Edit in one message and verify it."""
        if box.window_text() == value:
            return
        try:
            box.set_edit_text(value)
        except Exception:
            pass
        if box.window_text() != value:
            with self._foreground():
                box.set_focus()
                box.type_keys("^a{BACKSPACE}", set_foreground=True)
                if value:
                    box.type_keys(value, with_spaces=True, set_foreground=True)
        if box.window_text() != value:
            raise DriverError("HTML export style did not accept the requested value")

    def _screen_is_open(self, caption):
        """True when an MDI child form with this caption is still on screen.

        The arrival check `_open_html_screen` makes has no counterpart for leaving, and leaving is
        where this bites: see `_leave_html_screen`.
        """
        try:
            for window in self.main.descendants(class_name="ThunderRT6FormDC"):
                if window.is_visible() and caption.lower() in (window.window_text() or "").lower():
                    return True
        except Exception:                                               # noqa: BLE001
            pass
        return False

    def _leave_html_screen(self, attempts=3):
        """Click EXIT on the HTML Output form and CONFIRM it actually closed.

        WHY THIS EXISTS. `_open_html_screen` already says a click is not the same as arriving
        somewhere. The same is true of leaving, and nothing checked it: html_output clicked EXIT
        once and returned. When that click was swallowed the game stayed parked on the HTML
        Output form, and the very next thing a Sim Week does is Tools -> Output MDB - whose click
        then went into that form instead of the menu. The export never started, the file never
        moved, and the run reported "no MDB for pro" after burning its whole budget.

        Caught live 2026-09-23 by enumerating the game's windows while it was stuck: no message
        box anywhere, but a visible ThunderRT6FormDC captioned "Html Output". That also showed
        the first fix for this was aimed at the wrong thing - dismiss_all() closes message boxes
        (#32770), and this was never a message box.

        Never fatal: if the screen will not close, the caller has already produced its pages and
        the MDB step can still be attempted. It just says so.
        """
        for _ in range(attempts):
            if not self._screen_is_open("Html Output"):
                return True
            self._dismiss_affirmative()
            self.click(self.HTML_EXIT, 2)
        left = not self._screen_is_open("Html Output")
        if not left:
            print("   ! the HTML Output screen would not close; the next menu click may be lost")
        return left

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
            self._set_export_text(box, value)

        self.click(self.HTML_OUTPUT_BTN, 3)
        end = time.monotonic() + timeout
        index = out / "index.htm"
        answered = []
        while time.monotonic() < end:
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
                # AND CONFIRM IT CLOSED. A swallowed EXIT leaves the game on this form, and the
                # next step's menu click lands in it - which is exactly how pro's MDB export kept
                # failing while the same export run on its own took 24 seconds.
                self._leave_html_screen()
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
        """Current League Editor -> Players -> Export -> Save as PlayerFiles/<name>.csv.

        Nothing else in the commissioner calls this, so it had quietly stopped working and the
        first real use found out. Selecting "Players" repopulates the grid from the whole league,
        and on a 530-player save that takes longer than a flat sleep allowed; the EXPORT click
        landed mid-load and VB6 swallowed it, taking focus and opening nothing.

        THE RETRY IS THE DANGEROUS PART, and the first attempt at one was worse than no retry at
        all. PLAYER_FILE_SAVE and EDITOR_EXIT ARE THE SAME PIXEL (917, 662): on the exact failure
        a retry exists for - EXPORT swallowed, screen still the League Editor - clicking SAVE
        presses EXIT instead, and every later click lands on a Tools screen this driver has no
        model of. So SAVE is never sent on faith. The Player File screen has to have replaced the
        editor first, which is read off the EXPORT button itself: while that pixel still says
        EXPORT we are still in the editor and the click did nothing.

        Each attempt re-navigates from Tools rather than clicking again where it stands, the way
        _open_html_screen does, because a leftover modal that ate the first click will eat the
        second and waiting longer cannot fix it.
        """
        target = DOCS / "PlayerFiles" / f"{name}.csv"

        def _clear_target():
            """Remove any previous export, INSIDE the attempt loop rather than once before it.

            Clearing once was a bug the retry made reachable: attempt 1 could leave a partial
            CSV, and attempt 2's write-wait would find that stale file already quiet and return
            it as success. make_codec_fixtures then copies a truncated export into the fixture
            set and test_codec compares it row by row against a 530-player save - a phantom
            failure that reads as a codec bug.

            Wrapped because this driver's error contract is DriverError: the file being held
            open, by Excel or by a test_codec run, must not escape as a bare OSError.
            """
            try:
                if target.exists():
                    target.unlink()
            except OSError as exc:
                raise DriverError(f"cannot clear the previous export at {target}: {exc}") from exc

        def _name_box(timeout):
            """The save-as text box on a VISIBLE dialog, waited for and returned.

            Not `window(...).child_window(...).exists()`: pywinauto's exists() forces
            visible_only=False, and VB6 keeps forms loaded-but-hidden - so that check is true
            before anything has opened, and the export name would be typed into whatever is
            actually on screen. It also raises ElementAmbiguousError when two forms match, which
            _wait_for would swallow as "not yet" while the dialog sat open. Scan the visible
            windows instead and take the one that really has a box.
            """
            found = []

            def _look():
                found.clear()
                for w in self.app.windows(visible_only=True):
                    try:
                        if not w.class_name().startswith("ThunderRT6Form"):
                            continue
                        boxes = [b for b in w.descendants(class_name="ThunderRT6TextBox")
                                 if b.is_visible()]
                    except Exception:                               # noqa: BLE001
                        continue
                    if boxes:
                        found.append(boxes[0])
                return bool(found)

            if not self._wait_for(_look, timeout):
                raise DriverError(
                    f"the export name dialog never appeared within {timeout}s, although the "
                    f"League Editor had been left - so SAVE opened something else")
            return found[0]

        def _left_the_editor():
            """The EXPORT button is gone, so something opened over the League Editor.

            Read from the screen rather than from a window handle: VB6 keeps forms loaded but
            hidden, so "a ThunderRT6FormDC with a textbox exists" is true before anything opens.
            """
            try:
                from .screen_text import normalize
                return "EXPORT" not in normalize(self._button_text(EDITOR_EXPORT))
            except Exception:                                       # noqa: BLE001
                return False

        last = None
        for attempt in range(EXPORT_ATTEMPTS):
            _clear_target()
            self.dismiss_all()
            self.click(TOP_TOOLS, 2)
            self.click(TOOLS_LEAGUE_EDITOR, 1)
            try:
                # WAIT FOR THE COMBOS, do not sleep at them. A flat 3s was enough on a quiet
                # machine and not on a loaded one, and reading descendants() before the editor
                # has painted returns nothing - the same failure this whole method exists to fix,
                # one step earlier. The dropdown APPEARING is the thing to watch for.
                found = []

                def _sort_by_ready():
                    found.clear()
                    try:
                        for c in self.main.descendants(class_name="ThunderRT6ComboBox"):
                            if c.is_visible() and "Draft Pool" in c.item_texts():
                                found.append(c)
                                return True
                    except Exception:                               # noqa: BLE001
                        pass          # a control being rebuilt mid-transition is normal
                    return False

                if not self._wait_for(_sort_by_ready, EXPORT_OPEN_LIMIT):
                    raise DriverError(
                        f"the League Editor's Sort by list never appeared within "
                        f"{EXPORT_OPEN_LIMIT}s, so the editor did not open")
                sort_by = found[0]
                # PROVE the selection took, the way combo() does: a dropdown that has not
                # finished populating drops a valid request in silence and keeps its previous
                # view, and selected_text() reports the LAST option when nothing is selected.
                # Compare the index instead. Exporting the Draft Pool under the Players name
                # would be a successful run producing the wrong file.
                sort_by.select("Players")
                wanted = sort_by.item_texts().index("Players")
                if sort_by.selected_index() != wanted:
                    raise DriverError("the League Editor stayed on its previous view; "
                                      "'Players' did not take")
                # The grid is filling. This is a TIMING signal and nothing is decided on it -
                # the decision below is the EXPORT button disappearing - so the cheap read is
                # allowed here, which is the rule _wait_until_still states.
                if not self._wait_until_still(settle=1.0, timeout=EXPORT_GRID_LIMIT,
                                              poll=0.25, cheap=True):
                    raise DriverError(
                        f"the League Editor's player list was still changing after "
                        f"{EXPORT_GRID_LIMIT}s; the export was not attempted")

                self.click(EDITOR_EXPORT, 1)
                if not self._wait_for(_left_the_editor, EXPORT_OPEN_LIMIT):
                    raise DriverError(
                        f"EXPORT was clicked but the League Editor is still up after "
                        f"{EXPORT_OPEN_LIMIT}s, so the click was swallowed")
                # Only now is (917, 662) SAVE rather than EXIT.
                self.click(PLAYER_FILE_SAVE, 2)
                box = _name_box(EXPORT_OPEN_LIMIT)
                # SET IT, THEN WAKE THE FORM UP. set_edit_text puts the text in with one
                # message and never fires VB6's Change event, so the dialog's SAVE button stays
                # DISABLED - the name is sitting there correctly typed and the button is grey,
                # which is exactly what it looked like on screen. The old code got away with
                # type_keys only because keystrokes fire Change per character.
                #
                # So: set it exactly (no escaping worries, no {}()+^%~ being read as key
                # syntax), then send an edit that changes nothing - END, a space, a backspace -
                # purely so the control reports a change and the form enables its button. The
                # text is re-verified afterwards because a stray keystroke landing elsewhere
                # would otherwise save under the wrong name.
                self._set_export_text(box, name)
                with self._foreground():
                    box.set_focus()
                    box.type_keys("{END}{SPACE}{BACKSPACE}", set_foreground=True)
                if box.window_text() != name:
                    raise DriverError(
                        f"the export name box reads {box.window_text()!r}, not {name!r}")
                # BEFORE the click, the way save_game does. Reading it afterwards captures the
                # file the click has already begun writing, so "it changed" is never true and a
                # perfectly good export times out.
                before = self._file_mark(target)
                self.click(SAVE_NAME_OK, 0)

                # WRITTEN AND QUIET, not merely created. exists() is true the instant FBPB3
                # opens the file, and make_codec_fixtures copies it straight into a fixture that
                # test_codec then compares row by row - a truncated CSV there reads as a codec
                # bug. Same two conditions save_game uses on league.dat.
                # Seeded with what was on disk BEFORE the click, and accepted only once it has
                # CHANGED and then gone quiet - the pair of conditions save_game and output_mdb
                # both use. Seeding None instead would accept whatever was already there.
                end, quiet = time.monotonic() + EXPORT_WRITE_LIMIT, None
                mark = before
                while time.monotonic() < end:
                    now = self._file_mark(target)
                    if now != mark:
                        mark, quiet = now, time.monotonic()
                    elif (mark and mark != before and mark[1] > 0
                          and quiet and time.monotonic() - quiet >= 1.0):
                        self.click(EDITOR_EXIT, 3)      # back to Tools
                        return target
                    time.sleep(0.2)
                raise DriverError(f"the player export never finished writing to {target}")
            except DriverError as exc:
                last = exc
                # Back out to somewhere known before trying again. A modal left open swallows
                # the caller's exit_game clicks and costs it 45 seconds before it kills the
                # process, so this must not be skipped on the way to raising.
                self.dismiss_all()
        # Leave the game somewhere known. dismiss_all only clears #32770 message boxes, so a VB6
        # Player File screen would still be up and would swallow the caller's exit_game clicks -
        # 45 seconds of timeouts and then a kill. _open_html_screen sends ESC for this reason.
        try:
            self.main.type_keys("{ESC}", set_foreground=False)
            self.click(EDITOR_EXIT, 1)
        except Exception:                                           # noqa: BLE001
            pass
        raise DriverError(f"player export failed after {EXPORT_ATTEMPTS} attempts: {last}")
