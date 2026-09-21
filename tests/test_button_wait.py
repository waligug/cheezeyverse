"""A sim day must not wait for the end-season popup to clear.

THE STOPPAGE THIS EXISTS FOR, 2026-09-21 17:00:52:

    Expected SIM DAY at (794, 585); read 'SIMDAY'. No further click was sent.

Those two strings are EQUAL. `screen_text.read` normalises before it returns and
`normalize("SIM DAY")` is `"SIMDAY"`, so the label matched on the very first read. What never
cleared was the other half of the condition: `_progress_popup_visible()`, a detector written for
END SEASON's scouting popup, which cannot tell that popup from the progress form a perfectly
ordinary sim day paints. The 3 second wait expired every time and the day's click was never sent.

The message sent everybody after the wrong thing, so there are two tests here: the daily click no
longer waits on the popup, and a wait that DOES time out on the popup says so instead of blaming
the text.

    python tests/test_button_wait.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner.driver.screen_text import normalize  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class _Fake:
    """Enough of FBPB3Driver to exercise _expect_button and nothing else."""

    def __init__(self, text, popup):
        self.text, self.popup, self.reads = text, popup, 0

    def _message_boxes(self):
        return []

    def _button_text(self, _xy):
        self.reads += 1
        return self.text

    def _progress_popup_visible(self):
        return self.popup


def run():
    from commissioner.driver.fbpb3 import FBPB3, DriverError
    expect = FBPB3._expect_button

    print("the label the OCR returns and the label we ask for are the same string")
    check("OCR of the button", normalize("SIM DAY"), "SIMDAY")
    check("they match", normalize("SIM DAY") == "SIMDAY", True)

    print("a sim day clicks through its own progress form")
    fake = _Fake("SIMDAY", popup=True)
    try:
        expect(fake, (794, 585), "SIM DAY", timeout=1, require_idle=False)
        check("returned rather than timing out", True, True)
    except DriverError as exc:
        check("returned rather than timing out", f"raised {exc}", True)

    print("an offseason phase still waits for the popup to clear")
    fake = _Fake("ENDSEASON", popup=True)
    try:
        expect(fake, (1, 2), "END SEASON", timeout=0.5)
        check("it waited", False, True)
    except DriverError as exc:
        check("it waited and then said so", "progress popup never cleared" in str(exc), True)
        check("and it did NOT blame the text", "read 'ENDSEASON'" in str(exc), False)

    print("a genuine label mismatch still reports the text it read")
    fake = _Fake("PROCEED", popup=False)
    try:
        expect(fake, (3, 4), "PROCESS ALL", timeout=0.5)
        check("it raised", False, True)
    except DriverError as exc:
        check("it names what it read", "read 'PROCEED'" in str(exc), True)

    print("a clear button with no popup returns at once, either way")
    for idle in (True, False):
        fake = _Fake("SIMDAY", popup=False)
        try:
            expect(fake, (5, 6), "SIM DAY", timeout=1, require_idle=idle)
            check(f"require_idle={idle}", True, True)
        except DriverError as exc:
            check(f"require_idle={idle}", f"raised {exc}", True)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  button wait: a sim day no longer blocks on the end-season popup, an offseason "
          "phase still does, and a popup timeout says so instead of blaming the label")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
