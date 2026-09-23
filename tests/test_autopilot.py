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

    def __init__(self, rows=None, sim=None, preview=None):
        self.posted = []
        self.sims = []
        self.previews = 0
        self._rows = rows if rows is not None else [Row(True, "all clear")]
        self._sim = sim
        self._preview = preview

    def __enter__(self):
        self.old = {k: getattr(autopilot, k) for k in
                    ("run_sim", "preview_offseason", "_say")}
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

    def _preview_offseason(self, log=print):
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
    check("it reported", len(rig.posted), 1)
    check("and the report names the work", "simmed 7 day" in rig.posted[0], True)
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
    """The assertion this file exists for."""
    print("the season runs out")
    with Rig(sim=SeasonEnd("prep has 0 more day(s) with games scheduled")) as rig:
        code = autopilot.main(["--quiet"])
    check("a refusal is not a failure", code, 0)
    check("it previewed instead", rig.previews, 1)
    check("and posted what would happen", "season is over" in rig.posted[0], True)
    # It tried the week once, was refused, and did not try anything else.
    check("exactly one sim attempt", len(rig.sims), 1)


def test_a_preview_that_will_not_run_is_reported_not_swallowed():
    print("the season runs out and the preview breaks")
    with Rig(sim=SeasonEnd("out of days"),
             preview=RuntimeError("could not read the saves")) as rig:
        code = autopilot.main(["--quiet"])
    check("it failed", code, 1)
    check("it said the preview broke", "preview would not run" in rig.posted[0], True)
    check("and that nothing changed", "Nothing was changed" in rig.posted[0], True)


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
    test_a_preview_that_will_not_run_is_reported_not_swallowed()
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
