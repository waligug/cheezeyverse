"""A sim must not be able to run past the last day of the regular season by accident.

NOTHING IN THIS PROJECT HAS EVER CROSSED THAT LINE. `sim_days` clicks SIM DAY blind - no stage
check, no dialog check between clicks - and `offseason.py` moves everybody on by editing
league.dat through the codec, without ever driving FBPB3 through its own playoffs or its own
rollover. So what the game does on the day after the last one is genuinely unknown: whether it
stops on a modal that eats every later click, whether SIM DAY plays playoff games at all,
whether it starts its own aging and re-signing. Any of those is FBPB3 taking over a rollover
that offseason.py is supposed to own, on the live universe, with seven real people in it.

It is worth a guard rather than a memory. The panel takes whatever number is typed into it, and
the advice given the day before this was written was to use 35-day runs - which, from where the
universe now stands, would cross the boundary with four days to spare on the wrong side.

The fixture is written here rather than copied, for the same reason the other save-based tests
are: `fixtures/saves/` is gitignored and SERVERPC is a clone, and SERVERPC is the only machine
that can actually run a sim.

    python tests/test_season_boundary.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch  # noqa: E402
from commissioner import simweek  # noqa: E402


def _schedule(played_to, last):
    """A schedule page: regular-season dates, scored up to `played_to` and empty after it.

    "Playoffs" appears in the nav bar ABOVE the schedule as well as as a heading, which is why
    the reader searches for it after the Regular Season heading and not before.
    """
    rows = []
    for day in range(1, last + 1):
        date = f"3/{day}/2027"
        if day <= played_to:
            rows.append(f"<tr><td>&nbsp;{date}</td><td>&nbsp; @Clams 42, Derricks 35</td></tr>")
        else:
            rows.append(f"<tr><td>&nbsp;{date}</td><td>&nbsp; @Clams at Derricks</td></tr>")
    return ("<html><body><div>Standings Schedule Leaders Playoffs Champs</div>"
            "<table><tr><td>Preseason</td></tr>"
            "<tr><td>&nbsp;10/1/2026</td><td>&nbsp; @Lions 40, Kings 38</td></tr></table>"
            "<table><tr><td>Regular Season</td></tr>" + "".join(rows) + "</table>"
            "</body></html>")


def _universe(tmp, played_to, last):
    for key in ("prep", "college", "pro"):
        d = Path(tmp) / key / "html"
        d.mkdir(parents=True, exist_ok=True)
        (d / "schedule.htm").write_text(_schedule(played_to, last), encoding="latin-1")
    return Path(tmp)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="boundary-"))
    real = ch.save_path
    try:
        root = _universe(tmp, played_to=20, last=31)          # 11 days of season left
        ch.save_path = lambda key: root / key / "league.dat"
        simweek.ch.save_path = ch.save_path

        assert simweek._regular_season_left(root / "prep") == 11, \
            simweek._regular_season_left(root / "prep")

        keys = ["prep", "college", "pro"]
        for days in (1, 7, 10, 11):
            simweek._refuse_to_cross_the_season(keys, days, lambda *a, **k: None)

        for days in (12, 28, 35):
            try:
                simweek._refuse_to_cross_the_season(keys, days, lambda *a, **k: None)
            except simweek.SeasonEnd as exc:
                assert "11 day" in str(exc), exc
            else:
                raise AssertionError(f"{days} days was allowed past the end of the season")

        # The TIGHTEST league decides. One league a day further on must hold the others back,
        # because the run sims all three.
        (root / "college" / "html" / "schedule.htm").write_text(
            _schedule(played_to=28, last=31), encoding="latin-1")
        try:
            simweek._refuse_to_cross_the_season(keys, 5, lambda *a, **k: None)
        except simweek.SeasonEnd as exc:
            assert "college" in str(exc), exc
        else:
            raise AssertionError("the league with least room did not stop the run")
        simweek._refuse_to_cross_the_season(keys, 3, lambda *a, **k: None)

        # No opinion beats a wrong opinion: a league with no export, or one where nothing has
        # been played, must not block a sim. Refusing because a page is missing would be worse
        # than the thing being guarded against.
        assert simweek._regular_season_left(root / "nothing-here") is None
        blank = _universe(Path(tempfile.mkdtemp(prefix="blank-")), played_to=0, last=31)
        assert simweek._regular_season_left(blank / "prep") is None, "unplayed season blocked a sim"
        shutil.rmtree(blank, ignore_errors=True)

        # And the escape hatch has to exist, or the day the path IS designed this becomes the
        # thing standing in its way.
        import inspect
        assert "allow_season_end" in inspect.signature(simweek.run_sim).parameters
    finally:
        ch.save_path = real
        simweek.ch.save_path = real
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK  season boundary: a run cannot cross the end of the regular season unasked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
