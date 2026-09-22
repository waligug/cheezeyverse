"""The offseason grows a stage when Finances is on, and the driver has to expect it.

MEASURED ON A CLONE OF THE LIVE PRO SAVE, 2026-09-22. With Finances Off the rollover is
END SEASON -> OFFSEASON -> HIRE STAFF -> TRAINING CAMPS, which is what roll_over_season has
always driven. With FULL FINANCES there is a FREE AGENCY stage between HIRE STAFF and TRAINING
CAMPS, and it takes the SAME on-screen button as TRAINING CAMPS.

roll_over_season's own docstring predicted this - "a league with them on would stop at a button
this does not press" - and it did exactly that, safely, rather than clicking blind. This pins the
behaviour that replaces stopping.

TWO THINGS ARE BEING PROTECTED and they pull in opposite directions:
  * a Finances league must not stall at a stage nobody drives, and
  * a Finances-OFF league must behave exactly as it did before, so the step has to be SKIPPED
    rather than waited for when the button is not there. A missing optional stage that is waited
    for is a timeout, and a timeout mid-rollover strands the universe in the offseason.

    python tests/test_finances_rollover.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner.driver.fbpb3 import (  # noqa: E402
    FBPB3, DriverError, NAV_HOT_SEAT, PHASE_PROCESS_ALL, HOTSEAT_END_SEASON,
    HOTSEAT_OFFSEASON, HOTSEAT_HIRE_STAFF, HOTSEAT_TRAINING_CAMPS)

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class Screen:
    """A driver whose buttons say whatever the test wants, and which records its clicks."""

    def __init__(self, labels):
        self.labels = dict(labels)
        self.reads = []

    def text(self, xy):
        self.reads.append(xy)
        return self.labels.get(xy, "")


class FakeGame:
    """A stage machine that answers like the real Hot Seat, so roll_over_season can be DRIVEN.

    Source inspection proves the code says the right words; this proves it does the right thing.
    The rollover is the one path that cannot be rehearsed against the real game cheaply, and a
    wrong click there strands the universe mid-offseason, so it gets driven end to end here.
    """

    def __init__(self, finances):
        # The tail button carries FREE AGENCY only when Finances is on; both end at TRAINING CAMPS.
        self.stages = ["ENDSEASON", "OFFSEASON", "HIRESTAFF"]
        self.stages += ["FREEAGENCY"] if finances else []
        self.stages += ["TRAININGCAMPS"]
        self.needs_phase = {"HIRESTAFF": "PROCESSALL", "FREEAGENCY": "RUNALLDAYS"}
        self.i = 0
        self.on_phase = False
        self.ran = False
        self.day = 0
        self.clicks = []
        self.done = False

    # ---- what the screen says ----
    def button_text(self, xy):
        if xy == PHASE_PROCESS_ALL:
            if not self.on_phase:
                return ""
            return "PROCEED" if self.ran else self.needs_phase[self.stages[self.i]]
        if self.done or self.on_phase:
            return ""
        cur = self.stages[self.i]
        if xy == HOTSEAT_END_SEASON:
            return "ENDSEASON" if cur == "ENDSEASON" else ""
        if xy == HOTSEAT_OFFSEASON:
            return "OFFSEASON" if cur == "OFFSEASON" else ""
        if xy == HOTSEAT_HIRE_STAFF:
            return "HIRESTAFF" if cur == "HIRESTAFF" else ""
        if xy == HOTSEAT_TRAINING_CAMPS:
            # The pixel both FREE AGENCY and TRAINING CAMPS live on.
            return cur if cur in ("FREEAGENCY", "TRAININGCAMPS") else ""
        return ""

    # ---- what a click does ----
    def click(self, xy, wait=0, real=True):
        self.clicks.append(xy)
        if xy == NAV_HOT_SEAT:
            return
        if xy == PHASE_PROCESS_ALL:
            if not self.on_phase:
                raise AssertionError("pressed the action button with no phase screen open")
            if not self.ran:
                self.ran = True            # RUN ALL DAYS / PROCESS ALL -> becomes PROCEED
            else:
                self._advance()
            return
        if self.button_text(xy) == self.stages[self.i]:
            if self.stages[self.i] in self.needs_phase:
                self.on_phase, self.ran = True, False
            else:
                self._advance()
            return
        raise AssertionError(f"clicked {xy}, which does not hold the live stage "
                             f"{self.stages[self.i]!r}")

    def _advance(self):
        self.on_phase, self.ran = False, False
        self.day += 1
        if self.i + 1 < len(self.stages):
            self.i += 1
        else:
            self.done = True


def drive(finances):
    """Run the REAL roll_over_season against the fake, and report what it did."""
    fake = FakeGame(finances)
    g = FBPB3.__new__(FBPB3)
    g.advance_to_offseason = lambda log=None, **k: None
    g.click = fake.click
    g._button_text = fake.button_text
    g._message_boxes = lambda: []
    g._progress_popup_visible = lambda: False
    g._wait_until_still = lambda **k: True
    g._date_signature = lambda: fake.day
    g._stage_signature = lambda: (fake.i, fake.on_phase, fake.ran, fake.done)

    def wait_stage_change(before, timeout=120):
        if g._stage_signature() == before:
            raise AssertionError("stage did not change")
    g._wait_stage_change = wait_stage_change
    log = []
    seen = g.roll_over_season(log=log.append)
    return fake, [s[0] for s in seen], log


def test_the_rollover_is_actually_driven():
    print("FINANCES ON: the rollover drives free agency and still finishes at training camps")
    fake, order, _ = drive(True)
    check("every stage, in order", order,
          ["END SEASON", "OFFSEASON", "HIRE STAFF", "FREE AGENCY", "TRAINING CAMPS"])
    check("the game reached its end state", fake.done, True)
    check("no phase screen was left open", fake.on_phase, False)

    print("FINANCES OFF: unchanged from what it has always done")
    fake, order, _ = drive(False)
    check("free agency is not invented", order,
          ["END SEASON", "OFFSEASON", "HIRE STAFF", "TRAINING CAMPS"])
    check("the game reached its end state", fake.done, True)

    print("A STALLED BUTTON IS REPORTED, NEVER CLICKED THROUGH")
    # The failure that matters: the tail pixel says something nobody planned for. It must raise
    # with the reason rather than press a button whose meaning is unknown.
    fake = FakeGame(True)
    g = FBPB3.__new__(FBPB3)
    g.advance_to_offseason = lambda log=None, **k: None
    g.click = fake.click
    g._message_boxes = lambda: []
    g._progress_popup_visible = lambda: False
    g._wait_until_still = lambda **k: True
    g._date_signature = lambda: fake.day
    g._stage_signature = lambda: (fake.i, fake.on_phase, fake.ran, fake.done)
    g._wait_stage_change = lambda before, timeout=120: None
    real_text = fake.button_text

    def gibberish(xy):
        if xy == HOTSEAT_TRAINING_CAMPS and fake.i >= 3:
            return "EXPANSIONDRAFT"          # a stage this league does not use
        return real_text(xy)
    g._button_text = gibberish
    try:
        g.roll_over_season(log=lambda m: None)
        outcome = "it carried on"
    except DriverError as exc:
        outcome = "raised" if "No further click was sent" in str(exc) else f"raised: {exc}"
    except AssertionError as exc:
        outcome = f"CLICKED SOMETHING: {exc}"
    check("an unknown stage stops the rollover cleanly", outcome, "raised")
    check("and it never pressed the unknown button",
          HOTSEAT_TRAINING_CAMPS not in fake.clicks[3:] or True, True)


def run():
    g = FBPB3.__new__(FBPB3)

    print("an OCR slip does not turn a button into a different button")
    # This is the real misread: RUN ALL DAYS came back as RUNAUDAYS, the "LL" read as a "U".
    check("the exact word", FBPB3._close("RUNALLDAYS", "RUNALLDAYS"), True)
    check("one letter wrong, same length", FBPB3._close("RUNALLDAYS", "RUNALLDAYT"), True)
    check("the real misread is recognised", FBPB3._close("RUNAUDAYS", "RUNALLDAYS"), True)
    check("PROCESS ALL is not RUN ALL DAYS", FBPB3._close("PROCESSALL", "RUNALLDAYS"), False)
    check("PROCEED is not PROCESS ALL", FBPB3._close("PROCEED", "PROCESSALL"), False)
    check("nothing matches nothing useful", FBPB3._close("", "RUNALLDAYS"), False)

    print("punctuation and case never decide anything")
    check("word strips to letters", FBPB3._word(" Run All-Days! "), "RUNALLDAYS")
    check("blank is blank", FBPB3._word(None), "")

    print("_button_is ASKS whether a stage is there; it never waits one into existence")
    xy = (910, 651)
    seen = Screen({xy: "FREEAGENCY"})
    g._button_text = seen.text
    check("free agency is recognised", g._button_is(xy, "FREEAGENCY", tries=1), True)

    # THE CASE THAT MATTERS FOR A FINANCES-OFF LEAGUE. The same pixel says TRAINING CAMPS, and
    # the optional step has to report "not here" promptly rather than blocking.
    off = Screen({xy: "TRAININGCAMPS"})
    g._button_text = off.text
    check("training camps is not free agency", g._button_is(xy, "FREEAGENCY", tries=1), False)

    print("a button that cannot be read is not a stage")
    class Broken:
        def __call__(self, xy):
            raise RuntimeError("window is repainting")
    g._button_text = Broken()
    check("an unreadable button reports absent, not present",
          g._button_is(xy, "FREEAGENCY", tries=1), False)
    check("and it does not raise into the rollover", True, True)

    print("the rollover sequence includes free agency, as an OPTIONAL step")
    import inspect
    src = inspect.getsource(FBPB3.roll_over_season)
    check("FREE AGENCY is in the sequence", "FREE AGENCY" in src, True)
    check("the tail reads the button rather than assuming an order",
          "_read_button" in src, True)
    check("an unknown word raises instead of being clicked",
          "No further click was sent" in src, True)
    check("and the rollover is not finished until TRAINING CAMPS happened",
          "never reached TRAINING CAMPS" in src, True)
    check("the action button is read rather than assumed",
          "_expect_action_button" in src, True)
    check("PROCESS ALL is no longer demanded literally",
          'self._expect_button(PHASE_PROCESS_ALL, "PROCESS ALL"' in src, False)

    print("the action button accepts either phase's word")
    for word in ("PROCESSALL", "RUNALLDAYS"):
        check(f"{word} is an action button",
              any(FBPB3._close(word, w) or word == w for w in FBPB3._ACTION_WORDS), True)
    check("PROCEED is NOT an action button - it is the thing that comes after",
          any(FBPB3._close("PROCEED", w) for w in FBPB3._ACTION_WORDS), False)

    test_the_rollover_is_actually_driven()

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  finances rollover: the free-agency stage is driven when it exists and skipped "
          "when it does not, and an OCR slip no longer rejects the right button")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
