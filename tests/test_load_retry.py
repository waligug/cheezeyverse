"""A swallowed LOAD click is clicked again - but never while a real load is running.

WHAT WENT WRONG. Simming into the 2032 playoffs, prep and college loaded, simmed and exported,
then CV_Pro sat on the Load screen for the full 90s and the whole run died:

    DriverError: row 2 was still on the Load screen 90s after LOAD was clicked

The row was selected - the LOAD button had gone enabled, or it would have failed earlier - so the
one click simply never landed. load_save_row clicked once and waited, with no way back.

WHY A RE-CLICK NEEDS PERMISSION. A click queued behind a REAL load fires on whatever screen comes
next, so clicking again blindly is its own bug. The tell is the one load_save_row's own comment
gives: while VB6 reads league.dat it stops pumping messages. Windows calls that a hung window. So
the rule is: re-click only a window that is RESPONSIVE, still on the Load screen, and still
offering an ENABLED LOAD button - that one never started loading.

    python tests/test_load_retry.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner.driver import fbpb3  # noqa: E402
from commissioner.driver.fbpb3 import FBPB3, DriverError  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}{(': ' + detail) if detail else ''}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


class Button:
    def __init__(self, game):
        self.game = game

    def is_enabled(self):
        return self.game.enabled


class Game(FBPB3):
    """The Load screen with the window torn out. `swallow` = how many LOAD clicks are lost."""

    def __init__(self, swallow=0, hung=False, enabled=True, load_takes=0.2, disable_after=False):
        self.clicks, self.swallow, self.hung = [], swallow, hung
        self.disable_after = disable_after
        self.enabled, self.load_takes = enabled, load_takes
        self.on_load_screen, self.leaves_at = False, None

    def click(self, xy, wait=1.5, real=True):
        self.clicks.append(xy)
        if xy == fbpb3.TOP_LOAD:
            self.on_load_screen = True
        if xy == fbpb3.LOAD_BUTTON:
            if self.disable_after:
                self.enabled = False
            if self.swallow > 0:
                self.swallow -= 1                     # this one never lands
            elif self.leaves_at is None:
                self.leaves_at = time.monotonic() + self.load_takes

    def _load_screen_open(self, unknown=True):
        if self.leaves_at is not None and time.monotonic() >= self.leaves_at:
            self.on_load_screen = False
        return self.on_load_screen

    def _load_button(self):
        return Button(self)

    def _window_hung(self):
        return self.hung

    def _wait_for(self, cond, timeout):
        return cond()

    def _settle(self, *a, **k):
        return None

    def _wait_until_still(self, *a, **k):
        return True


def loads(game):
    return sum(1 for c in game.clicks if c == fbpb3.LOAD_BUTTON)


def run():
    saved = (fbpb3.LOAD_RETRY_AFTER, fbpb3.LOAD_LIMIT_FLOOR)
    fbpb3.LOAD_RETRY_AFTER, fbpb3.LOAD_LIMIT_FLOOR = 0.3, 4     # fast, but the same shape
    try:
        print("the click lands first time: one click, no retry")
        g = Game()
        g.load_save_row(2, wait=0)
        check("exactly one LOAD click", loads(g) == 1, str(loads(g)))

        print("\nTHE BUG: the first LOAD click is swallowed")
        g = Game(swallow=1)
        try:
            g.load_save_row(2, wait=0)
            ok = True
        except DriverError as exc:
            ok = False
            print("   ", exc)
        check("the load goes through", ok)
        check("because LOAD was clicked a second time", loads(g) == 2, str(loads(g)))

        print("\nbut a HUNG window is a real load, and is never clicked again")
        # The case a blind retry gets wrong: the click queues behind the load and fires on the
        # next screen. Load takes 1.5s here, well past the retry window.
        g = Game(hung=True, load_takes=1.5)
        g.load_save_row(2, wait=0)
        check("one click, however long the load takes", loads(g) == 1, str(loads(g)))

        print("")
        print("and a LOAD button that has greyed out is not clicked again either")
        # Enabled for the first click - a button disabled from the start fails earlier, at
        # "did not select a save" - then greyed out, which is the game acknowledging it.
        g = Game(swallow=5, disable_after=True)
        try:
            g.load_save_row(2, wait=0)
        except DriverError:
            pass
        check("no re-click without an enabled button", loads(g) == 1, str(loads(g)))

        print("\nthe retries are bounded, and the failure still says so")
        g = Game(swallow=99)
        try:
            g.load_save_row(2, wait=0)
            said = ""
        except DriverError as exc:
            said = str(exc)
        check("it stops at LOAD_MAX_CLICKS", loads(g) == fbpb3.LOAD_MAX_CLICKS, str(loads(g)))
        check("and the error names how many clicks it tried", "time(s)" in said, said)

        print("\nan unanswerable 'is it hung?' never licenses a re-click")
        # FBPB3 with no window at all: the real _window_hung must answer True, not raise.
        bare = FBPB3.__new__(FBPB3)
        check("_window_hung is True when the window cannot be asked", bare._window_hung() is True)
    finally:
        fbpb3.LOAD_RETRY_AFTER, fbpb3.LOAD_LIMIT_FLOOR = saved

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  load retry: a swallowed LOAD is clicked again, a real (hung) load never is, a disabled "
          "button is left alone, and the retries are bounded")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
