"""A sim the machine killed must not be run again on top of itself.

THE ONE FAILURE run_sim CANNOT SEE. Every failure it can, it already handles: a failed write
check restores the backup, a store that refuses restores the backup, and both stop before the
game is opened. None of that runs when the power goes or the box restarts mid-run, because the
cleanup is code and the code stops too.

What that leaves is a state nothing else can tell apart from a clean one. Point spends are
written into league.dat and verified FIRST and only then marked applied in the store - on
purpose, so a crash between them leaves the work done rather than lost. But the store still
lists those requests as pending, so the next run applies the same deltas to the same players
again and a character quietly gets double what he paid for. An activation is worse: he is still
`pending`, so the next run claims him a SECOND reserve slot and stamps him into it, and now
there are two of him.

So a marker is written before the first save is touched and removed when the run ends, by
whichever path it ends on. Surviving means: a run began and nothing got to tidy up.

WHAT THE MARKER HAS TO GET RIGHT, and it is the part that was wrong first time: a league is
at risk BETWEEN its save being written and its store writes finishing. Recording it after the
store agrees describes the safe state and misses the dangerous one entirely.

    python tests/test_interrupted_run.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import simweek  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cv-marker-"))
    real_marker = simweek.MARKER
    simweek.MARKER = tmp / "run_in_progress.json"
    try:
        # ---- nothing running, nothing to say -------------------------------------------
        assert simweek.interrupted_run() is None, "a clean machine must report no interruption"

        # ---- a run begins ---------------------------------------------------------------
        simweek._mark_running(["prep", "college"], 28, 2027)
        state = simweek.interrupted_run()
        assert state, "the marker was not written"
        assert state["leagues"] == ["prep", "college"] and state["days"] == 28, state
        assert state["unconfirmed"] == [], "nothing is at risk before a save is written"

        # ---- prep's save is written, store not told yet: AT RISK -------------------------
        simweek._mark_saved("prep", tmp / "20260920-000000-CV_Prep")
        risky = simweek.interrupted_run()["unconfirmed"]
        assert [r["league"] for r in risky] == ["prep"], risky
        assert "CV_Prep" in risky[0]["backup"], "the refusal could not name the backup to restore"

        told = simweek.describe_interruption(simweek.interrupted_run())
        assert "never finished" in told, told
        assert "prep" in told and "CV_Prep" in told, told
        assert "twice" in told, "the message must say WHY running again is not safe"
        assert "restore_backup" in told, "the message must say how to put it right"

        # ---- the store agrees: prep is safe again ---------------------------------------
        simweek._mark_synced("prep")
        assert simweek.interrupted_run()["unconfirmed"] == [], \
            "a league the store has confirmed is not at risk"
        safe = simweek.describe_interruption(simweek.interrupted_run())
        assert "Nothing had been committed" in safe, safe
        assert "run_in_progress.json" in safe, "it should still say how to clear the marker"

        # ---- two leagues, only the second unfinished ------------------------------------
        simweek._mark_saved("prep", tmp / "a")
        simweek._mark_synced("prep")
        simweek._mark_saved("college", tmp / "b")
        risky = simweek.interrupted_run()["unconfirmed"]
        assert [r["league"] for r in risky] == ["college"], \
            f"only the league that did not finish should be listed: {risky}"

        # ---- the run ends: the marker goes -----------------------------------------------
        simweek._clear_marker()
        assert simweek.interrupted_run() is None, "the marker outlived the run that wrote it"
        simweek._clear_marker()          # and clearing twice is not an error

        # ---- a damaged marker is not a crash --------------------------------------------
        simweek.MARKER.write_text("{not json", encoding="utf-8")
        assert simweek.interrupted_run() is None, \
            "an unreadable marker must read as 'no interruption', not raise into a sim"

        # ---- run_sim actually refuses ----------------------------------------------------
        src = (ROOT / "commissioner" / "simweek.py").read_text(encoding="utf-8")
        start = src.index("def run_sim(")
        body = src[start:]
        assert "interrupted_run()" in body and "describe_interruption" in body, \
            "run_sim no longer checks for an interrupted run"
        assert "_mark_running(" in body, "run_sim no longer writes the marker"
        # and the at-risk window is marked BEFORE the save is written, not after
        saved_at = body.index("_mark_saved(")
        commit_at = body.index("ch.commit(")
        synced_at = body.index("_mark_synced(")
        assert saved_at < commit_at < synced_at, \
            "the marker must go on before ch.commit and come off after the store writes; " \
            "any other order describes the safe state instead of the dangerous one"

        print("OK  interrupted run: a crashed sim is detected, names the saves that are ahead "
              "of the store, and refuses to run again on top of them")
        return 0
    finally:
        simweek.MARKER = real_marker
        for p in tmp.glob("*"):
            p.unlink()
        tmp.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
