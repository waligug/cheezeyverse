"""The offseason grows a stage when Finances is on, and the driver has to expect it.

MEASURED ON A CLONE OF THE LIVE PRO SAVE, 2026-09-22. With Finances Off the rollover ends
END SEASON -> OFFSEASON -> HIRE STAFF -> TRAINING CAMPS. With FULL FINANCES there is a FREE
AGENCY stage in between, on the SAME pixel as TRAINING CAMPS, and its action button says RUN ALL
DAYS rather than PROCESS ALL because free agency is a run of days.

WHAT GOES WRONG HERE IS EXPENSIVE AND QUIET. seasonflow wraps roll_over_season in no timeout and
its `finally` runs `exit_game(save=False)`, so a rollover the game really completed can be thrown
away by a driver that merely failed to recognise the end of it. Every assertion below exists
because some version of this file let one of those through.

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


class FakeGame:
    """A stage machine that answers like the real Hot Seat, so roll_over_season can be DRIVEN."""

    def __init__(self, finances, tail_after_camps="SIMTOPLAYOFFS", misread=None):
        self.stages = ["ENDSEASON", "OFFSEASON", "HIRESTAFF"]
        self.stages += ["FREEAGENCY"] if finances else []
        self.stages += ["TRAININGCAMPS"]
        self.needs_phase = {"HIRESTAFF": "PROCESSALL", "FREEAGENCY": "RUNALLDAYS"}
        self.tail_after_camps = tail_after_camps
        self.misread = misread or {}
        self.i = 0
        self.on_phase = False
        self.ran = False
        self.day = 0
        self.clicks = []
        self.done = False

    def _say(self, word):
        return self.misread.get(word, word)

    def button_text(self, xy):
        if xy == PHASE_PROCESS_ALL:
            if not self.on_phase:
                return ""
            return self._say("PROCEED" if self.ran else self.needs_phase[self.stages[self.i]])
        if self.on_phase:
            return ""
        if self.done:
            # THE PANEL GIVES WAY TO THE SIM BUTTONS. Same pixel, which is exactly why a driver
            # that insists on TRAINING CAMPS can fail a rollover the game finished.
            return self._say(self.tail_after_camps) if xy == HOTSEAT_TRAINING_CAMPS else ""
        cur = self.stages[self.i]
        want = {HOTSEAT_END_SEASON: "ENDSEASON", HOTSEAT_OFFSEASON: "OFFSEASON",
                HOTSEAT_HIRE_STAFF: "HIRESTAFF"}.get(xy)
        if want:
            return self._say(cur) if cur == want else ""
        if xy == HOTSEAT_TRAINING_CAMPS:
            return self._say(cur) if cur in ("FREEAGENCY", "TRAININGCAMPS") else ""
        return ""

    def click(self, xy, wait=0, real=True):
        self.clicks.append(xy)
        if xy == NAV_HOT_SEAT:
            return
        if xy == PHASE_PROCESS_ALL:
            if not self.on_phase:
                raise AssertionError("pressed the action button with no phase screen open")
            if not self.ran:
                self.ran = True
            else:
                self._advance()
            return
        if self.done:
            raise AssertionError(f"clicked {xy} after the offseason was over")
        if self.button_text(xy) == self._say(self.stages[self.i]):
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


def wire(fake):
    g = FBPB3.__new__(FBPB3)
    g.advance_to_offseason = lambda log=None, **k: None
    g.click = fake.click
    g._button_text = fake.button_text
    g._message_boxes = lambda: []
    g._progress_popup_visible = lambda: False
    g._wait_until_still = lambda **k: True
    g._date_signature = lambda: fake.day
    # THE REAL _stage_signature IS A CROP OF THE STAGE-NAME BOX, which does not move until the
    # phase actually completes. An earlier fake included on_phase, so merely OPENING a phase
    # screen counted as a stage change - and the bug roll_over_season's docstring records as
    # "how this was first got wrong", pressing EXIT instead of PROCEED, would have passed.
    g._stage_signature = lambda: (fake.i, fake.done)

    def wait_stage_change(before, timeout=120):
        if g._stage_signature() == before:
            raise AssertionError("stage did not change")
    g._wait_stage_change = wait_stage_change
    return g


def drive(finances, **kw):
    fake = FakeGame(finances, **kw)
    seen = wire(fake).roll_over_season(log=lambda m: None)
    return fake, [s[0] for s in seen]


def run():
    g = FBPB3.__new__(FBPB3)

    print("an OCR slip does not turn a button into a different button")
    check("the exact word", FBPB3._close("RUNALLDAYS", "RUNALLDAYS"), True)
    check("a substitution is forgiven", FBPB3._close("RUNAUDAYS", "RUNALLDAYS"), True)
    # A CLIPPED CROP DROPS THE LAST LETTER far more often than it garbles a middle one, and the
    # old rule required the ENDS to match, which a truncation can never do.
    check("a dropped last letter is forgiven", FBPB3._close("TRAININGCAMP", "TRAININGCAMPS"), True)
    check("so is a clipped FREE AGENCY", FBPB3._close("FREEAGENC", "FREEAGENCY"), True)
    check("PROCESS ALL is not RUN ALL DAYS", FBPB3._close("PROCESSALL", "RUNALLDAYS"), False)
    check("PROCEED is not PROCESS ALL", FBPB3._close("PROCEED", "PROCESSALL"), False)
    check("FREE AGENCY is not TRAINING CAMPS",
          FBPB3._close("FREEAGENCY", "TRAININGCAMPS"), False)
    check("nothing matches nothing useful", FBPB3._close("", "RUNALLDAYS"), False)
    check("a trailing S read as a 5 still resolves",
          FBPB3._word("TRAINING CAMP5") == "TRAININGCAMP"
          and FBPB3._close("TRAININGCAMP", "TRAININGCAMPS"), True)

    print("the action words are only ones actually seen on these screens")
    check("PROCESS ALL", "PROCESSALL" in FBPB3._ACTION_WORDS, True)
    check("RUN ALL DAYS", "RUNALLDAYS" in FBPB3._ACTION_WORDS, True)
    # RUN DAY was a guess, and it is one substitution from SUNDAY - a word the free-agency
    # screen, which runs a series of DAYS, can genuinely paint into this read box.
    check("no day of the week is an action button",
          [d for d in ("SUNDAY", "MONDAY", "FRIDAY")
           if any(FBPB3._close(d, w) or d == w for w in FBPB3._ACTION_WORDS)], [])

    print("_read_button settles before it commits, and says which half failed")

    class Screen:
        def __init__(self, words):
            self.words, self.n = list(words), 0

        def __call__(self, xy):
            w = self.words[min(self.n, len(self.words) - 1)]
            self.n += 1
            return w

    g._message_boxes = lambda: []
    g._progress_popup_visible = lambda: False
    # A stale word from the previous screen, then the real one. Committing to the first read is
    # what made the tail raise "offseason stalled" at a league that was perfectly fine.
    g._button_text = Screen(["PROCEED", "FREEAGENCY", "FREEAGENCY"])
    check("a one-off stale read is not committed to",
          g._read_button((910, 651), timeout=5), "FREEAGENCY")

    g._button_text = Screen([""])
    try:
        g._read_button((910, 651), timeout=0.6)
        out = "returned"
    except DriverError as exc:
        out = "blank" if "never painted" in str(exc) else f"other: {exc}"
    check("a screen that never paints is reported as that", out, "blank")

    print("_expect_button forgives the same slip the tail already forgave")
    # Identifying a stage tolerantly and then re-checking it EXACTLY one line later made the
    # tolerance decoration: accepted upstream, rejected here, after the full timeout.
    g._button_text = lambda xy: "FREEAGENCV"
    try:
        g._expect_button((910, 651), "FREE AGENCY", timeout=2)
        out = "accepted"
    except DriverError:
        out = "rejected"
    check("a single-character misread is accepted", out, "accepted")
    g._button_text = lambda xy: "TRAININGCAMPS"
    try:
        g._expect_button((910, 651), "FREE AGENCY", timeout=1)
        out = "accepted"
    except DriverError:
        out = "rejected"
    check("but a genuinely different stage is not", out, "rejected")

    print("_expect_action_button keeps both gates its predecessor had")
    g._button_text = lambda xy: "PROCESSALL"
    g._message_boxes = lambda: ["Confirm"]
    try:
        g._expect_action_button(timeout=1)
        out = "returned"
    except DriverError as exc:
        out = "message box" if "message box" in str(exc) else f"other: {exc}"
    check("a modal is named immediately, not mistaken for a label mismatch", out, "message box")
    g._message_boxes = lambda: []
    g._progress_popup_visible = lambda: True
    try:
        g._expect_action_button(timeout=1)
        out = "returned while busy"
    except DriverError:
        out = "waited"
    check("and it never returns while a progress popup is up", out, "waited")

    print("THE ROLLOVER IS ACTUALLY DRIVEN")
    fake, order = drive(True)
    check("Finances on: every stage, in order", order,
          ["END SEASON", "OFFSEASON", "HIRE STAFF", "FREE AGENCY", "TRAINING CAMPS"])
    check("it reached the end", fake.done, True)
    check("no phase screen was left open", fake.on_phase, False)

    fake, order = drive(False)
    check("Finances off: unchanged from what it always did", order,
          ["END SEASON", "OFFSEASON", "HIRE STAFF", "TRAINING CAMPS"])
    check("it reached the end", fake.done, True)

    print("an OCR slip mid-rollover does not stop it")
    fake, order = drive(True, misread={"FREEAGENCY": "FREEAGENCV", "RUNALLDAYS": "RUNAUDAYS"})
    check("the whole sequence still ran", order,
          ["END SEASON", "OFFSEASON", "HIRE STAFF", "FREE AGENCY", "TRAINING CAMPS"])

    print("a finished rollover is never mistaken for a failure")
    # The offseason panel REPLACES the sim buttons, and TRAINING CAMPS shares its pixel with
    # SIM TO PLAYOFFS. seasonflow discards the rollover on any raise, so this must not raise.
    for word in ("SIMTOPLAYOFFS", "SIMDAY", "SIMPRESEASON"):
        try:
            fake, order = drive(True, tail_after_camps=word)
            out = order[-1]
        except DriverError as exc:
            out = f"raised: {exc}"
        check(f"the panel giving way to {word} is completion", out, "TRAINING CAMPS")

    print("a stage that did not really advance is reported, not driven again")
    # _wait_stage_change only proves a crop changed. If it lies, a button-driven loop would run
    # free agency over and over at 60s + 900s a time before failing.
    fake = FakeGame(True)
    g2 = wire(fake)
    g2._wait_stage_change = lambda before, timeout=120: None     # the wait lies
    real_advance = fake._advance

    def stuck():
        # The phase SCREEN closes - otherwise the fake shows a blank Hot Seat and the driver
        # simply cannot read anything, which is a different failure from the one under test.
        # What does not happen is the stage moving on.
        fake.on_phase, fake.ran = False, False
        if fake.stages[fake.i] != "FREEAGENCY":
            real_advance()

    fake._advance = stuck
    try:
        g2.roll_over_season(log=lambda m: None)
        out = "carried on"
    except DriverError as exc:
        out = "refused" if "already run" in str(exc) else f"other: {exc}"
    except AssertionError as exc:
        out = f"CLICKED SOMETHING: {exc}"
    check("free agency is never driven twice", out, "refused")

    print("an unknown stage stops the rollover cleanly")
    fake = FakeGame(True)
    g3 = wire(fake)
    real_text = fake.button_text
    g3._button_text = lambda xy: ("EXPANSIONDRAFT"
                                  if xy == HOTSEAT_TRAINING_CAMPS and fake.i >= 3
                                  else real_text(xy))
    try:
        g3.roll_over_season(log=lambda m: None)
        out = "carried on"
    except DriverError as exc:
        out = "raised" if "No further click was sent" in str(exc) else f"other: {exc}"
    except AssertionError as exc:
        out = f"CLICKED SOMETHING: {exc}"
    check("an unrecognised stage raises instead of being pressed", out, "raised")
    check("and that pixel was never clicked", fake.clicks.count(HOTSEAT_TRAINING_CAMPS), 0)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  finances rollover: the free-agency stage is driven when it exists and skipped "
          "when it does not, a finished rollover is recognised however the panel ends, a stage "
          "is never driven twice, and an OCR slip no longer rejects the right button")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
