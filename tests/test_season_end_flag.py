"""`allow_season_end` must travel from the HTTP body all the way into run_sim.

THE FLAG IS FOUR HANDOFFS LONG: the JSON body, start_sim, the SimRun it builds, and finally
_worker's call to run_sim. It was added at the endpoint first and stopped at the third, where
everything still returned 202 and the sim still refused to cross - a flag that reports success
and changes nothing. That is the same shape as the season guard that parsed a date format the
other machine does not produce: correct-looking at every layer, inert in the one that matters.

So this test asserts what RUN_SIM RECEIVED. Checking that the route returns 202, or that SimRun
holds the attribute, would both have passed against the broken version.

The counterpart property matters just as much: the default is False. A sim that crosses into the
playoffs because somebody forgot a keyword is exactly the accident the guard exists to prevent.

    python tests/test_season_end_flag.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import app as panel  # noqa: E402


def run_once(body):
    """POST /api/sim/start with `body`, and return the kwargs run_sim was called with."""
    seen = {}

    def fake_run_sim(**kwargs):
        seen.update(kwargs)
        return {"leagues": [], "days": kwargs.get("days")}

    real_run_sim, real_lock = panel.run_sim, panel._SIM_LOCK
    panel.run_sim = fake_run_sim
    try:
        client = panel.app.test_client()
        reply = client.post("/api/sim/start", json=body)
        assert reply.status_code == 202, (reply.status_code, reply.get_data(as_text=True))
        run_id = reply.get_json()["run"]["id"]
        # _worker runs on a thread; wait for it to finish rather than sleeping a guessed amount
        for _ in range(200):
            run = panel.run_by_id(run_id) if hasattr(panel, "run_by_id") else panel.current_run()
            if run is not None and run.finished_at is not None:
                break
            import time
            time.sleep(0.02)
        else:
            raise AssertionError("the sim thread never finished")
        return seen, reply.get_json()["run"]
    finally:
        panel.run_sim, panel._SIM_LOCK = real_run_sim, real_lock
        if panel._SIM_LOCK.locked():      # a failed assertion must not brick the next test
            panel._SIM_LOCK.release()


def main():
    # ---- asked for, and it must ARRIVE -------------------------------------------------
    seen, summary = run_once({"days": 3, "allow_season_end": True})
    assert "allow_season_end" in seen, \
        "run_sim was never given allow_season_end - the flag stops somewhere in app.py"
    assert seen["allow_season_end"] is True, seen
    assert summary["allow_season_end"] is True, \
        "the run summary must say so: the panel prints it, and a run that could end a season " \
        "should not look identical in the log to one that could not"

    # ---- not asked for, and it must NOT be on ------------------------------------------
    seen, summary = run_once({"days": 3})
    assert seen.get("allow_season_end") is False, \
        f"the default must be False, got {seen.get('allow_season_end')!r}"
    assert summary["allow_season_end"] is False, summary

    # ---- explicitly false ---------------------------------------------------------------
    seen, _ = run_once({"days": 3, "allow_season_end": False})
    assert seen["allow_season_end"] is False, seen

    # ---- and run_sim actually takes it, so the kwarg is not landing in **kwargs limbo ----
    import inspect

    from commissioner import simweek
    params = inspect.signature(simweek.run_sim).parameters
    assert "allow_season_end" in params, \
        "simweek.run_sim has no allow_season_end parameter - the panel would TypeError live"
    assert params["allow_season_end"].default is False, \
        "run_sim must default to refusing; the panel relies on not passing it being safe"

    print("OK  allow_season_end reaches run_sim, defaults to False, and run_sim accepts it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
