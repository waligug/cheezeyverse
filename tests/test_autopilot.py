"""Autopilot runs one week, reports, and never decides anything irreversible by itself.

THE ASSERTION THIS FILE EXISTS FOR is `test_it_never_rolls_the_season_over`. An offseason promotes
people between leagues, drafts them, pays them and advances the season, through a sequence that
has failed mid-flight before and cannot be undone. A scheduled task that reached the end of a
regular season and rolled over on its own would do all of that at 3am with nobody watching. What
it does instead is run the PREVIEW - which writes nothing - and stop.

`test_a_failed_week_is_never_retried` is the other one. A sim that died halfway leaves a recovery
journal; a second attempt against an unreconciled universe turns one bad night into two.

Nothing here runs the game. `run_sim` is replaced throughout - the point is the decisions
autopilot makes around it, which are all in Python.

    python tests/test_autopilot.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("autopilot", ROOT / "tools" / "autopilot.py")
autopilot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(autopilot)

from commissioner.readiness import Row  # noqa: E402
from commissioner.simweek import SeasonEnd, SimBusy  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class Rig:
    """Replaces everything autopilot reaches for, and records what it was asked to do."""

    def __init__(self, rows=None, sim=None, preview=None, left=None):
        # Every league with a full week left, unless a test says otherwise.
        self._left = left if left is not None else {"prep": 40, "college": 50, "pro": 60}
        self.posted = []
        self.sims = []
        self.previews = 0
        self._rows = rows if rows is not None else [Row(True, "all clear")]
        self._sim = sim
        self._preview = preview

    def __enter__(self):
        self.old = {k: getattr(autopilot, k) for k in
                    ("run_sim", "preview_offseason", "_say", "days_left")}
        autopilot.days_left = lambda keys=None: dict(self._left)
        self.old_check = autopilot.readiness.check
        autopilot.readiness.check = lambda kind, **k: self._rows
        autopilot.run_sim = self._run_sim
        autopilot.preview_offseason = self._preview_offseason
        autopilot._say = lambda m, quiet=False, log=print: self.posted.append(m)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(autopilot, k, v)
        autopilot.readiness.check = self.old_check
        return False

    def _run_sim(self, **kw):
        self.sims.append(kw)
        if isinstance(self._sim, Exception):
            raise self._sim
        return self._sim if self._sim is not None else {
            "ok": True, "days": kw.get("days"), "leagues": ["prep", "college", "pro"],
            "seconds": 604, "applied": 3, "activated": 1}

    def _preview_offseason(self, log=print, lines=None):
        self.previews += 1
        if isinstance(self._preview, Exception):
            raise self._preview
        return (self._preview or "**The 2032 season is over.**\n- 1 drafted"), {}


def test_a_clean_universe_gets_one_week():
    print("the ordinary night")
    with Rig() as rig:
        code = autopilot.main(["--quiet"])
    check("it succeeded", code, 0)
    check("it simmed exactly once", len(rig.sims), 1)
    check("seven days by default", rig.sims[0]["days"], autopilot.DEFAULT_DAYS)
    # NO DISCORD POST ON SUCCESS. run_sim already opens and closes its own SimStatus card for
    # this event; a second one said the same thing in weaker words, and quoted a `seconds` the
    # dict run_sim returns has never carried, so it always read "?".
    check("it does not post a second time about one week", rig.posted, [])
    check("it never previewed an offseason", rig.previews, 0)


def test_a_closed_gate_stops_it_before_the_game_opens():
    print("readiness refuses")
    shut = [Row(True, "nothing else is running"),
            Row(False, "no interrupted run to reconcile", "a sim from Tuesday never finished")]
    with Rig(rows=shut) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed", code, 1)
    # THE POINT: the gate is in front of run_sim, not inside it.
    check("nothing was simmed", rig.sims, [])
    check("it said why", "never finished" in rig.posted[0], True)


def test_it_never_rolls_the_season_over():
    """The assertion this file exists for. EVERY league out of days is a real season end."""
    print("every league is out of regular season")
    with Rig(left={"prep": 0, "college": 0, "pro": 0}) as rig:
        code = autopilot.main(["--quiet"])
    check("a season end is not a failure", code, 0)
    check("it previewed instead", rig.previews, 1)
    check("and posted what would happen", "season is over" in rig.posted[0], True)
    check("and never simmed", rig.sims, [])


def test_one_short_league_is_not_the_end_of_the_season():
    """THE ONE THAT WOULD HAVE STALLED THE UNIVERSE, found by review and not by this file.

    `simweek` raises the SAME SeasonEnd class for "this league is finished" and for "seven days
    would run three past the end", so reading the exception's type cannot tell them apart. Prep
    plays a 30-game season against pro's 58, so prep drops under a week roughly a fortnight
    before the others - and the first version announced "**The season is over.**" to Discord on
    that Sunday, returned 0 so Task Scheduler recorded SUCCESS, and did the identical thing
    every Sunday after. Nothing would ever be simmed again, no bracket would ever be exported,
    and the new offseason gate would then correctly refuse the rollover it had just advertised.
    Every signal green.
    """
    print("prep is finished, pro has three more weeks")
    with Rig(left={"prep": 0, "college": 16, "pro": 20}) as rig:
        code = autopilot.main(["--quiet"])
    check("it does not claim the season is over", "season is over" in rig.posted[0], False)
    check("it never previews a rollover", rig.previews, 0)
    check("it names the league that finished", "prep" in rig.posted[0], True)
    check("and says the others are still playing", "day(s) left" in rig.posted[0], True)
    check("it simmed nothing, because the playoffs are a decision", rig.sims, [])
    check("and it is not an error", code, 0)


def test_a_short_week_is_shortened_rather_than_refused():
    """Three days left in the shortest league means a three-day week, not a refusal."""
    print("the week is sized to the shortest league")
    with Rig(left={"prep": 3, "college": 16, "pro": 20}) as rig:
        code = autopilot.main(["--quiet"])
    check("it ran", code, 0)
    check("it simmed the days that exist", rig.sims[0]["days"], 3)
    check("it did not claim the season was over", rig.previews, 0)

    # And a full week when there is a full week.
    with Rig(left={"prep": 40, "college": 50, "pro": 60}) as rig:
        autopilot.main(["--quiet"])
    check("a full week stays a full week", rig.sims[0]["days"], autopilot.DEFAULT_DAYS)


def test_the_season_end_backstop_asserts_nothing():
    """If SeasonEnd still fires, it must not announce anything it has not checked."""
    print("the backstop")
    with Rig(left={"prep": 40, "college": 50, "pro": 60},
             sim=SeasonEnd("prep has 0 more day(s) with games scheduled")) as rig:
        code = autopilot.main(["--quiet"])
    check("it stops", code, 1)
    check("it does NOT claim the season is over", "season is over" in rig.posted[0], False)
    check("it never previews", rig.previews, 0)
    check("it says where it stood down", "season boundary" in rig.posted[0], True)


def test_a_league_that_cannot_be_read_is_left_out_not_called_zero():
    """days_left omits an unreadable league. Calling it 0 would read as 'finished'."""
    print("an unreadable boundary")
    with Rig(left={"college": 50, "pro": 60}) as rig:   # prep missing entirely
        autopilot.main(["--quiet"])
    check("it still simmed", len(rig.sims), 1)
    check("sized on the leagues it could read", rig.sims[0]["days"], autopilot.DEFAULT_DAYS)
    # And no league readable at all falls back to the asked-for week rather than refusing.
    with Rig(left={}) as rig:
        autopilot.main(["--quiet"])
    check("no readable league still runs", len(rig.sims), 1)


def test_a_preview_that_will_not_run_is_reported_not_swallowed():
    print("the season runs out and the preview breaks")
    with Rig(left={"prep": 0, "college": 0, "pro": 0},
             preview=RuntimeError("could not read the saves")) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed", code, 1)
    check("it said the preview broke", "would not run" in rig.posted[0], True)
    check("and that nothing changed", "Nothing was changed" in rig.posted[0], True)


def test_a_dying_preview_still_reports_what_it_had_said():
    """The bracket warnings are the only part anybody reads; they must survive the traceback."""
    print("a preview that dies half way")

    class Talker(Rig):
        def _preview_offseason(self, log=print, lines=None):
            if lines is not None:
                lines.append("   ! pro: no 2032 playoff bracket in this export")
            raise RuntimeError("the saves went away")

    with Talker(left={"prep": 0, "college": 0, "pro": 0}) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed", code, 1)
    check("the warning came out with the failure",
          "no 2032 playoff bracket" in rig.posted[0], True)


def test_a_failed_week_is_never_retried():
    print("a week that dies")
    with Rig(sim=RuntimeError("the game stopped responding")) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed", code, 1)
    check("it ran once and stopped", len(rig.sims), 1)
    check("it named the error", "stopped responding" in rig.posted[0], True)
    check("and said it will not retry",
          "retried automatically" in rig.posted[0], True)


def test_a_week_that_finishes_dirty_is_not_called_a_success():
    """`ok: False` with errors is the shape a half-finished run comes back in."""
    print("a week that finishes but not cleanly")
    dirty = {"ok": False, "days": 7, "leagues": ["prep"], "errors": ["pro: no MDB"]}
    with Rig(sim=dirty) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed", code, 1)
    check("and surfaced the error", "no MDB" in rig.posted[0], True)


def test_the_lock_being_taken_is_a_shrug_not_an_alarm():
    print("something else got there first")
    with Rig(sim=SimBusy("a sim is already running")) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed quietly", code, 1)
    check("it stood down", "stood down" in rig.posted[0], True)


def test_a_dry_run_touches_nothing():
    print("the dry run")
    with Rig() as rig:
        code = autopilot.main(["--dry-run"])
    check("it succeeded", code, 0)
    check("nothing was simmed", rig.sims, [])
    check("nothing was posted", rig.posted, [])


def test_reporting_can_never_take_down_the_run():
    """A task whose only failure mode is 'could not report' looks fine and does nothing."""
    print("Discord is down")
    said = []
    real_post = autopilot.notify.post

    def boom(text, log=print):
        raise RuntimeError("discord is down")

    autopilot.notify.post = boom
    try:
        autopilot._say("a message", quiet=False, log=said.append)
    finally:
        autopilot.notify.post = real_post
    check("it did not raise", True, True)
    check("the terminal still got it", said[0], "a message")
    check("and it said the post failed", any("could not post" in s for s in said), True)


def main():
    test_a_clean_universe_gets_one_week()
    test_a_closed_gate_stops_it_before_the_game_opens()
    test_it_never_rolls_the_season_over()
    test_one_short_league_is_not_the_end_of_the_season()
    test_a_short_week_is_shortened_rather_than_refused()
    test_the_season_end_backstop_asserts_nothing()
    test_a_league_that_cannot_be_read_is_left_out_not_called_zero()
    test_a_preview_that_will_not_run_is_reported_not_swallowed()
    test_a_dying_preview_still_reports_what_it_had_said()
    test_a_failed_week_is_never_retried()
    test_a_week_that_finishes_dirty_is_not_called_a_success()
    test_the_lock_being_taken_is_a_shrug_not_an_alarm()
    test_a_dry_run_touches_nothing()
    test_reporting_can_never_take_down_the_run()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  autopilot: one week per run, the gate sits in front of the game rather than "
          "inside it, a season that runs out gets a preview and a human rather than an "
          "automatic rollover, nothing is ever retried, and a broken Discord cannot stop a sim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
