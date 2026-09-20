"""A run says where its own time went.

WHY THIS EXISTS. Every speed decision on this pipeline so far was made by somebody timing a log
stream by hand with a stopwatch. That is how a flat 30-second sleep sat inside every save load
for months: nobody could say what a load cost, so nobody looked. The steps were always stamped
with a `t`; nothing ever did the subtraction.

WHAT IT MUST NOT DO is invent a number. A run with one step, or with a step log that is missing
its timings, has nothing to say about phases and must say nothing - a confident breakdown of a
run nobody measured would be worse than no breakdown, because the next change would be aimed
with it.

    python tests/test_phase_totals.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.simweek import phase_totals  # noqa: E402


def step(name, t):
    return {"step": name, "t": t, "message": "", "league": None, "pct": 0}


def main():
    # a run shaped like a real one: setup, three leagues of work, publish, done
    steps = [step("start", 0), step("backup", 5), step("apply", 15), step("sim", 60),
             step("export", 160), step("apply", 200), step("publish", 220), step("points", 280)]
    out = dict(phase_totals(steps, total=300))

    assert out["sim"] == 100, out          # 60 -> 160
    assert out["apply"] == 65, out         # 15 -> 60 AND 200 -> 220: a phase visited twice adds up
    assert out["export"] == 40, out        # 160 -> 200
    assert out["publish"] == 60, out       # 220 -> 280
    assert out["points"] == 20, out        # the last step runs to the end of the run
    assert abs(sum(out.values()) - 300) < 0.01, f"the phases must account for the whole run: {out}"

    ordered = phase_totals(steps, total=300)
    assert ordered[0][0] == "sim", f"biggest first: {ordered}"
    assert [s for _, s in ordered] == sorted([s for _, s in ordered], reverse=True), ordered

    # ---- what it refuses ---------------------------------------------------------------------
    assert phase_totals([], 300) == [], "no steps, no opinion"
    assert phase_totals(None, 300) == [], "no steps, no opinion"

    # a step with no timestamp is skipped rather than guessed at
    rough = phase_totals([step("a", 0), {"step": "b"}, step("c", 10)], total=20)
    names = dict(rough)
    assert "b" not in names, f"a step with no time must not be given one: {rough}"

    # a clock that goes backwards (a hand-edited log) must not produce a negative phase
    weird = dict(phase_totals([step("a", 50), step("b", 10), step("c", 60)], total=70))
    assert all(v >= 0 for v in weird.values()), weird

    # one step only: it owns the run, and that is honest
    one = phase_totals([step("sim", 0)], total=42)
    assert one == [("sim", 42)], one

    print("OK  phase totals: a run reports where its seconds went, and reports nothing when it "
          "cannot tell")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
