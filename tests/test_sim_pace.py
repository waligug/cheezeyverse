"""The sim estimate is fitted from real runs, and refuses rather than guesses.

WHY A FIT AND NOT A CONSTANT. Most of a sim is fixed work done once per league whatever the days
asked for - loading the save, exporting, the MDB, the publish, the points. Measured on this
universe: 7 days across three leagues takes ~11 minutes and 21 days takes ~17, so a per-day rate
taken from the short run over-states the long one by nearly double. An estimate that says
"37 minutes" for something that takes 17 is not a smaller version of being right; it is the
number somebody uses to decide not to start.

WHAT MUST NOT HAPPEN is a confident number from a log that cannot support one. Every refusal
below produced a plausible-looking estimate in some draft of this:

  * too few runs -> None, not a line through two points
  * every run the same shape -> None: with one distinct (leagues, days) the two costs cannot be
    told apart, and the fit happily reports a negative per-day, which renders as a sim that gets
    faster the longer it runs
  * dry runs, refusals and resumes excluded -> they skipped the work being measured, and all
    three bias the estimate DOWNWARD, which is the direction that gets somebody started on a run
    they do not have time for

    python tests/test_sim_pace.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import app as panel  # noqa: E402


def pace_from(rows):
    real = panel.run_history
    panel.run_history = lambda limit=60: rows
    panel._pace_cache.update({"at": 0.0, "value": None})
    try:
        return panel.sim_pace()
    finally:
        panel.run_history = real
        panel._pace_cache.update({"at": 0.0, "value": None})


def run(days, seconds, leagues=("prep", "college", "pro"), **extra):
    row = {"ok": True, "dry_run": False, "days": days, "seconds": seconds,
           "leagues": list(leagues)}
    row.update(extra)
    return row


def main():
    # ---- the real shape of this universe's runs ------------------------------------------
    # three leagues: ~650 s for 7 days, ~1020 s for 21, 1210 s for 28
    rows = [run(7, 650), run(7, 660), run(21, 1020), run(21, 1010), run(28, 1210)]
    pace = pace_from(rows)
    assert pace, "a log with five ordinary runs must produce an estimate"
    assert pace["fixed"] > 0 and pace["per_day"] > 0, pace

    def predict(leagues, days):
        return pace["fixed"] * leagues + pace["per_day"] * leagues * days

    for leagues, days, actual in ((3, 7, 655), (3, 21, 1015), (3, 28, 1210)):
        off = abs(predict(leagues, days) - actual) / actual
        assert off < 0.12, f"{days}d predicted {predict(leagues, days):.0f}s vs {actual}s ({off:.0%})"

    # the whole point: doubling the days must NOT double the time
    assert predict(3, 14) < 2 * predict(3, 7) * 0.85, \
        "the fit has become linear in days, which is what a per-day constant already got wrong"

    # ---- refusals --------------------------------------------------------------------------
    assert pace_from([]) is None, "an empty log cannot be fitted"
    assert pace_from([run(7, 650), run(7, 660)]) is None, "two runs is not a fit"

    # every run identical in shape: the two costs are not separable
    same = [run(7, 650), run(7, 655), run(7, 660), run(7, 648)]
    assert pace_from(same) is None, \
        "one distinct shape cannot separate fixed from per-day - it must refuse, not extrapolate"

    # ---- what must be left out ---------------------------------------------------------------
    excluded = [
        run(7, 3, dry_run=True),                  # touched no save
        run(7, 2, ok=False),                      # refused before doing anything
        run(21, 467, resumed=True),               # skipped the setup it had already done
        run(999, 4, ok=False),                    # the 999 mis-type, which really happened
        run(0, 0),                                # nothing to measure
    ]
    assert pace_from(excluded) is None, \
        "a log of only dry runs, refusals and resumes must yield no estimate at all"

    mixed = pace_from(rows + excluded)
    assert mixed is not None
    assert abs(mixed["fixed"] - pace["fixed"]) < 1.0 \
        and abs(mixed["per_day"] - pace["per_day"]) < 0.05, \
        f"the excluded runs changed the fit: {mixed} vs {pace}"
    assert mixed["samples"] == len(rows), \
        f"samples must count only what was fitted, got {mixed['samples']}"

    # ---- and the spread is reported, so the UI can say "about" -------------------------------
    assert 0 <= pace["spread"] < 1, pace
    print(f"OK  sim pace: {pace['fixed']:.0f}s per league + {pace['per_day']:.1f}s per league-day "
          f"from {pace['samples']} runs; refuses on thin or unrepresentative logs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
