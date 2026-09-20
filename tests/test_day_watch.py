"""The day-advance watcher: cheap to watch, but never cheap to decide.

_wait_for_new_day used to PrintWindow the whole 1019x762 window every 0.1 s - measured at 62 ms
a capture, about thirteen of them per league-day, which across the 177 league-days of a 59-day
three-league week was roughly two minutes of a sim photographing a screen that had not changed.
It now watches the 160x21 date label with a BitBlt instead, measured at 0.65 ms: 96x cheaper.

BitBlt reads the pixels actually on screen, so a window sitting in front of FBPB3 is what it
returns. That makes it fine for WATCHING and unfit for DECIDING, and this pins the distinction,
because both ways of getting it wrong are expensive and silent:

  * a cheap read that says the date moved when it did not would end the day early - the next
    day's click goes in while the game is still on the old one, and the week ends short;
  * a window covered by something STATIC never changes under BitBlt at all, so a watcher that
    only ever looked cheaply would time out on a day that had finished perfectly well, and
    sim_days would click SIM DAY a second time for the same day.

So: every accepted advance is confirmed by PrintWindow, and PrintWindow is consulted on a timer
even while the cheap read says nothing has happened.

    python tests/test_day_watch.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402

FAILS = []


def ok(cond, msg):
    if not cond:
        FAILS.append(msg)


class Screen:
    """Stands in for the two ways of reading the date label.

    `truth` is what PrintWindow would return; `shown` is what BitBlt would - the same thing
    unless something is covering the window.
    """

    def __init__(self, truth=b"day-1", shown=None):
        self.truth, self.shown = truth, shown if shown is not None else truth
        self.cheap = self.full = 0

    def signature(self, cheap=False):
        if cheap:
            self.cheap += 1
            return self.shown
        self.full += 1
        return self.truth


def watcher(screen, settle_calls=None):
    game = FBPB3.__new__(FBPB3)
    game._date_signature = screen.signature
    game._wait_until_still = lambda **kw: (settle_calls.append(kw) if settle_calls is not None
                                           else None) or True
    return game


def main():
    # ---- 1. the ordinary case: the day advances and is accepted --------------------------
    s = Screen(b"day-1")
    g = watcher(s)

    def advance():
        s.truth = s.shown = b"day-2"
    import threading
    threading.Timer(0.3, advance).start()
    t = time.time()
    ok(g._wait_for_new_day(b"day-1", timeout=5, settle=0.2) is True,
       "a real day advance was not accepted")
    took = time.time() - t
    ok(took < 1.2, f"noticing a finished day took {took:.2f}s; the cheap poll is not being used")
    ok(s.cheap > s.full,
       f"the expensive read is still doing the watching: {s.full} PrintWindow vs {s.cheap} BitBlt")

    # ---- 2. a cheap read that lies is NOT enough -----------------------------------------
    # Something covered the window: BitBlt returns the covering window's pixels, which differ
    # from the date label. PrintWindow still says the day has not moved.
    s = Screen(truth=b"day-1", shown=b"some other window")
    g = watcher(s)
    t = time.time()
    ok(g._wait_for_new_day(b"day-1", timeout=1.0, settle=0.2) is False,
       "FINDING: a BitBlt that disagreed was accepted as a new day. A day called finished "
       "early is a day never simmed, and the week ends short.")
    ok(s.full >= 1, "the disagreement was never checked against PrintWindow at all")
    # ...and it must not have hammered PrintWindow once per poll either, or nothing was gained.
    ok(s.full <= 6, f"a covered window asked PrintWindow {s.full} times in a second; "
                    "the confirmation is not rate-limited")

    # ---- 3. a STATIC cover must not hide a day that really finished ----------------------
    # BitBlt never changes, because the covering window never changes. Only the timed
    # PrintWindow check can see the truth - without it sim_days would re-click a finished day.
    s = Screen(truth=b"day-1", shown=b"static cover")

    def finish():
        s.truth = b"day-2"            # the game moved on; the screen still shows the cover
    g = watcher(s)
    threading.Timer(0.2, finish).start()
    ok(g._wait_for_new_day(b"day-1", timeout=4, settle=0.2) is True,
       "FINDING: a finished day went unnoticed because a static window covered FBPB3. "
       "sim_days would click SIM DAY again and sim the day twice.")

    # ---- 4. the settle runs, and runs cheaply --------------------------------------------
    s = Screen(b"day-1")
    calls = []
    g = watcher(s, settle_calls=calls)
    s.truth = s.shown = b"day-2"
    g._wait_for_new_day(b"day-1", timeout=2, settle=0.25)
    ok(len(calls) == 1, f"the settle did not run exactly once: {calls}")
    ok(calls and calls[0].get("cheap") is True,
       f"the settle is still reading the screen the expensive way: {calls}")
    ok(calls and calls[0].get("settle") == 0.25,
       f"the caller's settle value was not passed through: {calls}")

    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  day watch: the cheap read does the watching, PrintWindow decides every accepted "
          "advance, a disagreeing cheap read is refused without hammering, a static cover "
          "cannot hide a finished day, and the settle runs once and cheaply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
