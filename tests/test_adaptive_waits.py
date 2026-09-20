"""Loading and saving wait for the game, not for the clock.

WHAT THIS REPLACED. `load_save_row` slept a flat 30 s after clicking LOAD and `save_game` slept
15 s after clicking OK. Measured on the three-league run of 2026-09-19, the loads alone were
108 s of a 873 s week and the game was sitting on the Hot Seat for most of it. A sleep that long
is also a lie in the other direction: it reports success at second 30 whether the game loaded,
hung, or put a message box in the way.

THE TWO SIGNALS, and why neither is "the picture stopped moving":

  LOAD    the LOAD button disappearing, i.e. the game leaving the Load screen. While VB6 reads
          league.dat it stops pumping messages, and an unresponsive window repaints the same
          pixels forever - so a settled picture is EXACTLY what the middle of a load looks like,
          and a settle test would call the load done while it is still reading.

  SAVE    league.dat's (mtime, size) going quiet AFTER it has changed. The timestamp moves when
          the write BEGINS, so waiting only for it to move hands a half-written save to the
          export that runs next. Both conditions, in that order, or it is not a save.

AND THE FAILURES MUST BE LOUD. A save that never wrote and a load that never left the screen
both used to be indistinguishable from success; each one silently exports or rolls over the
league that was loaded BEFORE. Both now raise.

    python tests/test_adaptive_waits.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.driver import fbpb3  # noqa: E402
from commissioner.driver.fbpb3 import DriverError, FBPB3  # noqa: E402


class FakeGame(FBPB3):
    """An FBPB3 with the window torn out: clicks are recorded, nothing is drawn."""

    made = []

    def __init__(self, load_screen_for=0.0, writes=()):
        self.clicks = []
        self._screen_until = None
        self._load_screen_for = load_screen_for
        self._writes = writes
        self.settled = 0
        self.timers = []
        FakeGame.made.append(self)

    def click(self, xy, wait=1.5, real=True):
        self.clicks.append((xy, wait))
        if xy == fbpb3.TOP_LOAD:                      # the Load screen arrives
            self._screen_until = time.time() + 3600
        if xy == fbpb3.LOAD_BUTTON:                   # and leaves again once the load is done
            self._screen_until = time.time() + self._load_screen_for
        if xy == fbpb3.SAVE_NAME_OK:
            for delay, size in self._writes:
                t = threading.Timer(delay, self._write, (size,))
                self.timers.append(t)
                t.start()
        # No click in either path may carry a sleep any more; every one of them now has
        # something to watch for instead.
        if wait:
            raise AssertionError(f"click({xy}) still sleeps {wait}s before anything is checked")

    def _write(self, size):
        self.path.write_bytes(b"x" * size)

    def _load_screen_open(self, unknown=True):
        return self._screen_until is not None and time.time() < self._screen_until

    def _wait_until_still(self, settle=1.0, timeout=60, poll=0.15):
        self.settled += 1
        return True


def timed(fn):
    start = time.time()
    try:
        return fn(), None, time.time() - start
    except Exception as exc:                                   # noqa: BLE001 - the point
        return None, exc, time.time() - start


def main():
    tmp = Path(__file__).resolve().parent / "_waits_tmp"
    tmp.mkdir(exist_ok=True)
    save = tmp / "league.dat"
    floors = (fbpb3.LOAD_LIMIT_FLOOR, fbpb3.SAVE_LIMIT_FLOOR)
    fbpb3.LOAD_LIMIT_FLOOR = fbpb3.SAVE_LIMIT_FLOOR = 4       # so a failure case is testable
    try:
        # ---- SAVE: returns when the file goes quiet, not when it is first touched -----------
        save.write_bytes(b"old")
        # Two writes 0.65 s apart. A save_game that returned as soon as the mtime MOVED would
        # stop at the first one; this must sit through the gap and leave with all 4000 bytes.
        # The rule it is testing, and the rule's limit: the file counts as written once it has
        # been quiet for a full second, so a game that paused LONGER than that mid-save would
        # fool it. FBPB3 writes its 6 MB straight through in about 5.8 s, measured.
        g = FakeGame(writes=((0.15, 1000), (0.8, 4000)))
        g.path = save
        _, exc, took = timed(lambda: g.save_game(wait=0, path=save))
        assert exc is None, exc
        assert save.stat().st_size == 4000, \
            f"save_game returned mid-write: the file was {save.stat().st_size} bytes, not 4000"
        assert took >= 1.8, f"returned in {took:.2f}s - it did not sit through the pause"
        assert g.settled, "the screen was never given a chance to settle after the write"

        # ---- SAVE: a save that never happened is an error, not a shrug ----------------------
        g = FakeGame(writes=())
        g.path = save
        _, exc, took = timed(lambda: g.save_game(wait=0, path=save))
        assert isinstance(exc, DriverError), f"a save that wrote nothing returned {exc!r}"
        assert "did not finish writing" in str(exc), exc
        assert took < 8, f"took {took:.1f}s to give up on a 4s floor"

        # ---- SAVE: with no path it still returns (the rehearsal and newgame call it bare) ---
        g = FakeGame()
        _, exc, _ = timed(lambda: g.save_game(wait=0))
        assert exc is None, exc
        assert g.settled >= 1, "with no file to watch, settling is the only signal left"

        # ---- LOAD: waits for the Load screen to go, and no longer ---------------------------
        g = FakeGame(load_screen_for=0.7)
        g._load_button = lambda: _Enabled()
        _, exc, took = timed(lambda: g.load_save_row(0, wait=0))
        assert exc is None, exc
        assert 0.7 <= took < 3, f"a 0.7s load took {took:.2f}s - the old sleep is still in there"

        # ---- LOAD: a screen that never leaves is an error ------------------------------------
        g = FakeGame(load_screen_for=60)
        g._load_button = lambda: _Enabled()
        _, exc, took = timed(lambda: g.load_save_row(0, wait=0))
        assert isinstance(exc, DriverError), f"a stuck Load screen returned {exc!r}"
        assert "still on the Load screen" in str(exc), exc
        assert took < 8, f"took {took:.1f}s to give up on a 4s floor"

        # ---- and the real code must not have grown a sleep back ------------------------------
        src = (ROOT / "commissioner" / "driver" / "fbpb3.py").read_text(encoding="utf-8")
        for line in ("self.click(LOAD_BUTTON, 0)", "self.click(SAVE_NAME_OK, 0)",
                     "self.click(TOP_LOAD, 0)", "self.click(TOP_SAVE, 0)",
                     "self.click(NAV_HOT_SEAT, 0)"):
            assert line in src, f"{line!r} is gone - a click there must not carry a wait"
        # and the click primitive must keep skipping the window-raise when it is not needed
        assert "win32gui.GetForegroundWindow() == hwnd" in src,             "the 0.3 s window-raise is unconditional again - that is 300 ms on every click"
    finally:
        for fake in FakeGame.made:
            for t in fake.timers:
                t.cancel()
        fbpb3.LOAD_LIMIT_FLOOR, fbpb3.SAVE_LIMIT_FLOOR = floors
        for p in tmp.glob("*"):
            p.unlink()
        tmp.rmdir()
    print("OK  load waits for the Load screen to go, save waits for league.dat to go quiet, "
          "and neither failure is silent")
    return 0


class _Enabled:
    def is_enabled(self):
        return True


if __name__ == "__main__":
    raise SystemExit(main())
