"""The sim's Discord card must reach its terminal state, even when the caller exits at once.

WHAT WENT WRONG. A 28-day sim finished perfectly - every game played, saved, published and the
points paid - and its Discord card sat for ever at 99%, "Awarding weekly points". It looked stuck
to the only person watching it.

`SimStatus.finish()` does not send anything. It flips the card to terminal and wakes the worker,
and the worker is a DAEMON thread, so the PATCH only lands if the process is still alive when it
gets a turn. The panel is long-lived and never saw this. A one-shot caller - a tool, a test, a
recovery script run by hand - returns from run_sim, exits, and the interpreter kills the thread
mid-flight. The message id lives only in memory, so nothing can repair that card afterwards.

`run_sim` now waits for the worker after finishing it, bounded by the worker's own FINAL_BUDGET.
That does not put Discord in the game loop: every click, save and publish is already recorded by
the time it runs.

    python tests/test_sim_status_final.py
"""
from __future__ import annotations

import inspect
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner import simstatus  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}{(': ' + detail) if detail else ''}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


class Recorder:
    """Stands in for the webhook. Slow on purpose: a send that returns instantly would pass
    even without the wait, which is exactly how this shipped."""

    def __init__(self, delay=0.25):
        self.sent = []
        self.delay = delay
        self.message_id = "1"

    def send(self, payload):
        time.sleep(self.delay)
        self.sent.append(payload)

    def close(self):
        pass


def run():
    print("finish() alone does not send - it only wakes the worker")
    rec = Recorder()
    st = simstatus.SimStatus(7, ["prep"], transport=rec)
    st.start()
    st.finish(True, "all done")
    # NO WAIT, the way the broken caller behaved. The worker has not had time to send.
    check("nothing has gone out yet", rec.sent == [], f"{len(rec.sent)} sent")

    print("\nwait() is what gets the terminal card out")
    got = st.wait(simstatus.FINAL_BUDGET)
    check("the worker finished", got is True)
    check("and the terminal update was sent", len(rec.sent) >= 1, f"{len(rec.sent)} sent")
    check("the worker thread is gone", not st._thread.is_alive())

    print("\nrun_sim waits for it, so any caller gets a finished card")
    from commissioner import simweek
    src = inspect.getsource(simweek.run_sim)
    i_f, i_w = src.find("status.finish("), src.find("status.wait(")
    check("run_sim finishes the card", i_f != -1)
    # THE WHOLE BUG. Without this the card is stranded for every short-lived caller.
    check("and then waits for it to land", -1 < i_f < i_w, f"finish@{i_f} wait@{i_w}")
    check("bounded, so a Discord outage cannot hang the run",
          "status.wait(FINAL_BUDGET)" in src)
    check("still inside the guard that cannot fail the run",
          "Discord final status failed" in src)

    print("\nthe wait is bounded even if the worker never returns")
    class Hangs(Recorder):
        def send(self, payload):
            time.sleep(30)
    st2 = simstatus.SimStatus(7, ["prep"], transport=Hangs())
    st2.start()
    st2.finish(True, "done")
    began = time.monotonic()
    st2.wait(0.5)
    took = time.monotonic() - began
    check("wait respects its timeout", took < 3, f"{took:.2f}s")

    print("\na daemon worker is still the right shape - it must never hold the process open")
    check("the worker is a daemon thread",
          "daemon=True" in inspect.getsource(simstatus.SimStatus.start))

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  sim status: finish() only arms the terminal card, run_sim waits for the worker to "
          "actually send it under a bounded budget, and the wait cannot hang the run")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
