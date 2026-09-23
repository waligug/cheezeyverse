"""Every precondition refuses for its own reason, and one of them used to not exist.

THE ASSERTION THIS FILE EXISTS FOR is `test_an_undecided_postseason_refuses`, and it is here
because the first version of readiness.py got it wrong. `playoff_bracket` returns
`qualifiers or None, None` the moment ANY series is undecided, so a NON-empty bracket with no
champion is exactly what a final sitting at 1-0 looks like. Testing `bracket is None` alone waved
that through - while `seasonflow._readiness` refused the same save with "finish the playoffs
first". Two gates, one question, opposite answers, and the new one said go. A rollover on an
undecided final is precisely what the monotonic per-round clinch inside `playoff_bracket` was
written to catch.

`test_an_unexported_postseason_refuses_the_offseason` is the other half: a bracket from LAST
season. That decline was already correct inside `playoff_bracket` but silent - the playoff and
title rows simply never appeared, so a rollover paid NOBODY for either, in every league, with
nothing saying a payment had been skipped.

`test_a_boundary_that_cannot_be_read_is_not_a_refusal` is the other one worth keeping. It is the
opposite failure: a check that cannot see its own evidence and refuses anyway would block every
run the moment an export went missing, which is worse than the thing it guards against.
`_refuse_to_cross_the_season` already says out loud that it cannot guard such a league and carries
on; this must agree with it, or the two disagree about whether a sim is allowed.

    python tests/test_readiness.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import readiness  # noqa: E402
from commissioner import seasonbonus  # noqa: E402
from commissioner import simweek  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class Patch:
    """Swap module attributes for the duration of a block, and put them all back."""

    def __init__(self, **targets):
        self.targets = targets
        self.old = {}

    def __enter__(self):
        for dotted, value in self.targets.items():
            mod, _, attr = dotted.rpartition("_ATTR_")
            obj = {"readiness": readiness, "simweek": simweek,
                   "seasonbonus": seasonbonus}[mod]
            self.old[dotted] = getattr(obj, attr)
            setattr(obj, attr, value)
        return self

    def __exit__(self, *a):
        for dotted, value in self.old.items():
            mod, _, attr = dotted.rpartition("_ATTR_")
            obj = {"readiness": readiness, "simweek": simweek,
                   "seasonbonus": seasonbonus}[mod]
            setattr(obj, attr, value)
        return False


def row(rows, fragment):
    return next((r for r in rows if fragment in r.name), None)


def clean_shared():
    """A universe with nothing in the way, so a test can isolate one check."""
    return {
        "readiness_ATTR__lock_is_free": lambda: True,
        "simweek_ATTR_interrupted_run": lambda: None,
    }


def test_the_kind_must_be_one_we_know():
    print("an unknown kind is a programming error, not a refusal")
    try:
        readiness.check("rollover")
        check("it raised", False, True)
    except ValueError as exc:
        check("it raised ValueError", "kind must be one of" in str(exc), True)


def test_each_shared_check_refuses_on_its_own():
    print("the three that stop any run")
    with Patch(**dict(clean_shared(),
                      readiness_ATTR__lock_is_free=lambda: False)):
        rows = readiness.check("sim", keys=[])
        r = row(rows, "nothing else is running")
        check("a held lock refuses", r.ok, False)
        check("and says who has it", "lock" in r.detail, True)
        check("the whole gate is closed", readiness.ready(rows), False)

    busy = {"kind": "offseason", "started_at": "2026-09-23T10:00:00Z", "phase": "draft"}
    with Patch(**dict(clean_shared(), simweek_ATTR_interrupted_run=lambda: busy)):
        rows = readiness.check("sim", keys=[])
        r = row(rows, "interrupted run")
        check("an interrupted run refuses", r.ok, False)
        check("and describes it", "never finished" in r.detail, True)

    with Patch(**clean_shared()):
        rows = readiness.check("sim", keys=[])
        check("a clean universe passes the shared checks",
              all(r.ok for r in rows), True)
        check("and there are three of them", len(rows), 3)


def test_an_unexported_postseason_refuses_the_offseason():
    """The check this module was written for."""
    print("the offseason will not run on last year's bracket")
    with Patch(**dict(clean_shared(),
                      seasonbonus_ATTR_playoff_bracket=lambda *a, **k: (None, None))):
        rows = readiness.check("offseason", keys=["college"], settings={"current_season": 2032})
        r = row(rows, "postseason is finished")
        check("it refuses", r.ok, False)
        check("it names the season", "2032" in r.name, True)
        check("it says what is lost", "NOBODY" in r.detail, True)
        check("and how to fix it", "sim the postseason" in r.fix, True)
        check("the gate is closed", readiness.ready(rows), False)

    # And the same league with a real bracket lets it through.
    with Patch(**dict(clean_shared(),
                      seasonbonus_ATTR_playoff_bracket=lambda *a, **k: ({"Tulips", "Wahoos"},
                                                                       "Tulips"))):
        rows = readiness.check("offseason", keys=["college"], settings={"current_season": 2032})
        r = row(rows, "postseason is finished")
        check("an exported bracket passes", r.ok, True)
        check("and names the champion", "Tulips" in r.detail, True)
        check("the gate is open", readiness.ready(rows), True)


def test_an_undecided_postseason_refuses():
    """A non-empty bracket with no champion is a final still being played, not a finished one.

    `playoff_bracket` returns `qualifiers or None, None` the moment ANY series is undecided, so
    testing `bracket is None` alone passes a postseason in progress. `seasonflow._readiness`
    refuses exactly that state with "finish the playoffs first" - and a rollover on an undecided
    final is the thing the monotonic per-round clinch inside `playoff_bracket` was written to
    catch. A gate that passes what the scorer refuses to score is worse than no gate.
    """
    print("a final sitting at 1-0")
    eight = {"Leghorns", "Swiss", "Wheels", "Ferns", "Tulips", "Rails", "Kings", "Dealers"}
    with Patch(**dict(clean_shared(),
                      seasonbonus_ATTR_playoff_bracket=lambda *a, **k: (eight, None))):
        rows = readiness.check("offseason", keys=["pro"], settings={"current_season": 2032})
        r = row(rows, "postseason is finished")
        check("it refuses", r.ok, False)
        check("it says the postseason is still on", "still being played" in r.detail, True)
        # The SAME WORDS seasonflow._readiness uses, so the two gates cannot drift apart into
        # giving opposite answers to one question again.
        check("in seasonflow's words", r.fix, "finish the playoffs first")
        check("the gate is closed", readiness.ready(rows), False)

    # An empty bracket is the OTHER failure and keeps its own sentence.
    with Patch(**dict(clean_shared(),
                      seasonbonus_ATTR_playoff_bracket=lambda *a, **k: (None, None))):
        r = row(readiness.check("offseason", keys=["pro"],
                                settings={"current_season": 2032}), "postseason is finished")
        check("a missing bracket is a different refusal", "NOBODY" in r.detail, True)


def test_a_league_that_does_not_exist_is_refused_not_passed():
    """A typo used to print "YES. Nothing is in the way of a Sim Week" about a fictional league.

    The sim path wraps its boundary read in a blanket except that maps any failure to ok=True -
    right for an unreadable boundary, wrong for an unreadable NAME, and the two were the same
    branch. The offseason path did not even get that far; it raised a bare KeyError.
    """
    print("a misspelt league")
    with Patch(**clean_shared()):
        for kind, extra in (("sim", {}), ("offseason", {"settings": {"current_season": 2032}})):
            rows = readiness.check(kind, keys=["colege"], **extra)
            r = row(rows, "every league named exists")
            check(f"{kind}: it refuses", r is not None and not r.ok, True)
            check(f"{kind}: it names the typo", "colege" in r.detail, True)
            check(f"{kind}: the gate is closed", readiness.ready(rows), False)
        # A real league alongside a fake one still gets checked.
        rows = readiness.check("sim", keys=["prep", "nope"])
        check("the real league is still examined", row(rows, "prep") is not None, True)


def test_the_lock_row_is_read_once():
    """Verdict and reason came from two separate calls, so they could disagree."""
    print("one lock read per row")
    calls = []

    def flaky():
        calls.append(1)
        return len(calls) > 1          # False first, True after

    with Patch(**dict(clean_shared(), readiness_ATTR__lock_is_free=flaky)):
        rows = readiness.check("sim", keys=[])
    r = row(rows, "nothing else is running")
    check("the lock was read once", len(calls), 1)
    check("it refused", r.ok, False)
    check("and the refusal carries its reason", bool(r.detail), True)


def test_a_bracket_that_will_not_read_refuses_rather_than_passing():
    """An exception reading the bracket is not evidence that the bracket is fine."""
    print("an unreadable bracket")

    def boom(*a, **k):
        raise RuntimeError("playoffs.htm is a directory")

    with Patch(**dict(clean_shared(), seasonbonus_ATTR_playoff_bracket=boom)):
        rows = readiness.check("offseason", keys=["pro"], settings={"current_season": 2032})
        r = row(rows, "postseason is finished")
        check("it refuses", r.ok, False)
        check("and says what went wrong", "directory" in r.detail, True)


def test_an_unknown_season_refuses_the_offseason():
    print("a rollover with no season to roll over")
    with Patch(**clean_shared()):
        for bad in ({}, {"current_season": None}, {"current_season": "soon"}):
            rows = readiness.check("offseason", keys=["pro"], settings=bad)
            r = row(rows, "the season is known")
            check(f"{bad} refuses", r is not None and not r.ok, True)


def test_a_boundary_that_cannot_be_read_is_not_a_refusal():
    """A guard that blocks when it cannot see is worse than the thing it guards against.

    `_refuse_to_cross_the_season` emits "cannot read the season boundary; not guarding those" and
    carries on. If this refused instead, the panel would say a sim is impossible while the sim
    itself would happily run - two answers to one question.
    """
    print("an unreadable season boundary")
    with Patch(**dict(clean_shared(),
                      simweek_ATTR__regular_season_left=lambda *a: None)):
        rows = readiness.check("sim", keys=["prep"])
        r = row(rows, "prep")
        check("it does NOT refuse", r.ok, True)
        check("but it says it cannot guard", "not be guarded" in r.detail, True)
        check("so a sim is still allowed", readiness.ready(rows), True)


def test_the_season_being_over_is_reported_but_allowed():
    """Zero days left is the playoffs, not an error. The run itself decides how to enter them."""
    print("a regular season with nothing left")
    with Patch(**dict(clean_shared(), simweek_ATTR__regular_season_left=lambda *a: 0)):
        rows = readiness.check("sim", keys=["pro"])
        r = row(rows, "pro")
        check("not a refusal", r.ok, True)
        check("and it points at the playoffs", "playoffs" in r.detail, True)

    with Patch(**dict(clean_shared(), simweek_ATTR__regular_season_left=lambda *a: 12)):
        rows = readiness.check("sim", keys=["pro"])
        check("days left reads as days left", "12 day(s)" in row(rows, "pro").detail, True)


def test_the_refusal_sentence_names_everything_wrong():
    print("the one-line refusal")
    with Patch(**dict(clean_shared(), readiness_ATTR__lock_is_free=lambda: False)):
        rows = readiness.check("sim", keys=[])
        line = readiness.refusal(rows)
        check("it names the failing check", "nothing else is running" in line, True)
    with Patch(**clean_shared()):
        check("a clean universe has no refusal",
              readiness.refusal(readiness.check("sim", keys=[])), "")


def test_nothing_here_holds_the_lock_it_is_reporting_on():
    """A readiness check that kept the lock would block the run it was asked about."""
    print("the check does not take what it is measuring")
    check("the lock is free before", simweek._SIM_LOCK.acquire(blocking=False), True)
    simweek._SIM_LOCK.release()
    readiness.check("sim", keys=[])
    got = simweek._SIM_LOCK.acquire(blocking=False)
    check("and still free after", got, True)
    if got:
        simweek._SIM_LOCK.release()


def main():
    test_the_kind_must_be_one_we_know()
    test_each_shared_check_refuses_on_its_own()
    test_an_unexported_postseason_refuses_the_offseason()
    test_an_undecided_postseason_refuses()
    test_a_league_that_does_not_exist_is_refused_not_passed()
    test_the_lock_row_is_read_once()
    test_a_bracket_that_will_not_read_refuses_rather_than_passing()
    test_an_unknown_season_refuses_the_offseason()
    test_a_boundary_that_cannot_be_read_is_not_a_refusal()
    test_the_season_being_over_is_reported_but_allowed()
    test_the_refusal_sentence_names_everything_wrong()
    test_nothing_here_holds_the_lock_it_is_reporting_on()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  readiness: an offseason refuses both a postseason that was never exported AND one "
          "still being played (in seasonflow's own words, so the two gates cannot drift), a "
          "misspelt league is refused rather than passed, a boundary that cannot be read is "
          "reported rather than used to block a sim, and the check never keeps the lock it is "
          "reporting on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
