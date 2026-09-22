"""The fast jump reports the day it is really on, not the number of repaints it caught.

`sim_to_date` hands FBPB one SIM TO GAME click and watches it work. It used to count progress by
watching the calendar's date label CHANGE - `_date_signature` proves a repaint happened but cannot
say what it now reads - and the game advances faster than a 20 Hz poll. So a 29-day jump reported
its way to about 18 and then leapt straight to 29 of 29, which is what made the panel's counter
untrustworthy.

The heading itself ("MARCH 23, 2027") is the only place the game states the day it is on. OCR is
far too slow to poll, so it is read on a timer and only when the cheap signature says something
changed - and the day count becomes the truth rather than a lower bound.

THE PARSER MUST FAIL SAFELY. A misread month is normal for OCR on a small crop, and a wrong date
would move the counter backwards or jump it wildly. Anything it cannot read as a whole date is
None, and the caller simply keeps the last figure it trusted.

    python tests/test_calendar_date.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner.driver import screen_text  # noqa: E402
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class _Screen(FBPB3):
    """Only the crop and the OCR are stubbed; the parsing under test is the real thing."""

    def __init__(self):
        pass

    def _grab(self, box=None, cheap=False):
        return None


def _reads(text):
    screen_text.read_raw = lambda _image: text
    return _Screen()._calendar_date()


def run():
    real = screen_text.read_raw
    try:
        print("the heading as FBPB writes it")
        check("a date mid-season", _reads("MARCH 23, 2027"), date(2027, 3, 23))
        check("a single-digit day", _reads("OCTOBER 5, 2029"), date(2029, 10, 5))
        check("the season opener", _reads("OCTOBER 16, 2029"), date(2029, 10, 16))
        check("case does not matter", _reads("march 23, 2027"), date(2027, 3, 23))
        check("nor does a dropped comma", _reads("MARCH 23 2027"), date(2027, 3, 23))
        check("nor surrounding noise", _reads("  APRIL 18, 2030  "), date(2030, 4, 18))

        print("anything it cannot read whole is None, never a guess")
        check("a misread month", _reads("MARCM 23, 2027"), None)
        check("no day", _reads("MARCH 2027"), None)
        check("no year", _reads("MARCH 23"), None)
        check("empty", _reads(""), None)
        check("a date that does not exist", _reads("FEBRUARY 31, 2030"), None)

        print("an OCR failure is not a sim failure")
        def boom(_image):
            raise RuntimeError("Windows OCR is unavailable")
        screen_text.read_raw = boom
        check("returns None instead of raising", _Screen()._calendar_date(), None)
    finally:
        screen_text.read_raw = real

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  calendar date: the heading parses to a real date, and every unreadable form "
          "returns None rather than a wrong day")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
