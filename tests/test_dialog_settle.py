"""Output MDB must not hand the next step a dialog it did not open.

FBPB3 writes LeagueOutput.mdb and THEN pops "File Created". output_mdb returned as soon as the
file's timestamp moved, so the box opened a beat after the last dismissal and stayed up. The next
click - college's LOAD, in Nate's 21-day week - went into that box instead, and the run died with
"LOAD button not found on the Load Saved Game screen": three steps downstream of the cause, in a
different league, describing a button that was fine.

_settle_dialogs is the fix, and it needs no game to test: it only asks for the open boxes and
dismisses them.

    python tests/test_dialog_settle.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.driver.fbpb3 import DriverError, FBPB3  # noqa: E402


class Fake:
    """Reports boxes from a script; dismiss_all clears whatever is showing now."""

    def __init__(self, script):
        self.script = list(script)
        self.dismissed = 0

    def _message_boxes(self):
        return self.script.pop(0) if self.script else []

    def dismiss_all(self):
        self.dismissed += 1


def settle(fake, **kw):
    return FBPB3._settle_dialogs(fake, **kw)


def main():
    # nothing open, and nothing arrives: returns without dismissing anything
    quiet = Fake([[], [], [], [], [], []])
    assert settle(quiet, grace=0.2, timeout=5) is True
    assert quiet.dismissed == 0

    # THE ONE THAT BROKE THE WEEK: quiet at first, then the box appears late. It must be
    # dismissed, not missed - a plain "look once and return" passes this only by luck.
    late = Fake([[], ["File Created"], [], [], [], [], [], []])
    assert settle(late, grace=0.2, timeout=5) is True
    assert late.dismissed == 1, late.dismissed

    # several in a row are all cleared
    many = Fake([["File Created"], ["Are you sure?"], [], [], [], [], []])
    assert settle(many, grace=0.2, timeout=5) is True
    assert many.dismissed == 2, many.dismissed

    # one that will not close STOPS the run here, where the message names it, instead of
    # letting the next league's clicks disappear into it
    stuck = Fake([["File Created"]] * 200)
    try:
        settle(stuck, grace=0.2, timeout=2)
    except DriverError as exc:
        assert "would not close" in str(exc), exc
    else:
        raise AssertionError("a permanent dialog was treated as settled")

    print("OK  dialogs: a box that arrives late is still dismissed, and a stuck one stops the run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
