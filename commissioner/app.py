"""The commissioner control panel: one local page for running the Cheezeyverse.

    python -m commissioner.app            # http://127.0.0.1:5095
    python -m commissioner.app --port 5096

One page is the product. It shows the three leagues, runs a Sim Week (or a longer chunk),
streams the sim's progress live, and holds the approval queue for characters and upgrade
requests. Nothing else.

Why it is shaped the way it is
------------------------------
**Everything that touches a save goes through `commissioner.simweek`.** This module owns the
HTTP, the lock, the log and the HTML, and nothing else. It never imports `store`, never opens
`league.dat`, never launches FBPB3. If a button here needs a new capability, that capability
belongs in `simweek`, not in a Flask route.

**One sim at a time, ever.** A sim drives a real FBPB3 window with a mouse. Two of them would
fight over the same window and write a corrupted save. `_SIM_LOCK` is a module-level
non-blocking lock: the second start is refused with a message, not queued.

**The live log is the point.** A sim takes minutes and the owner watches the game flail around
on screen; he needs to know which step it is on and that it has not hung. `run_sim`'s `on_step`
callbacks are appended to a per-run event list and fanned out to every attached SSE subscriber,
so a mid-sim page refresh replays the whole log and then keeps streaming.

**It degrades instead of exploding.** `simweek` may not import (it is written separately) and
Supabase may not be configured. Either way the app starts, serves, and says so in a banner.
"""
from __future__ import annotations

import argparse
import json
import queue
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"


class SimweekUnavailable(RuntimeError):
    """`commissioner.simweek` did not import. Every button that needs it says so."""


# ---------------------------------------------------------------------------------------
# the simweek contract - the only thing this app is allowed to call
# ---------------------------------------------------------------------------------------
# Exactly these seven names, nothing else. A failure here is a banner, never a traceback:
# the panel still starts so the owner can see *why* it is broken.
SIMWEEK_OK = True
SIMWEEK_ERROR = ""
try:  # pragma: no cover - the failure path is exercised by starting with simweek absent
    from .simweek import (  # type: ignore
        approve,
        create_character,
        pending_work,
        reject,
        run_history,
        run_sim,
        universe_status,
    )
except Exception as _exc:  # noqa: BLE001 - any import failure at all is a banner
    SIMWEEK_OK = False
    SIMWEEK_ERROR = f"{type(_exc).__name__}: {_exc}"

    def _unavailable(*_a, **_k):
        raise SimweekUnavailable(
            "commissioner.simweek is not importable, so nothing that touches a save can run. "
            + SIMWEEK_ERROR
        )

    approve = create_character = pending_work = _unavailable          # type: ignore
    reject = run_history = run_sim = universe_status = _unavailable   # type: ignore


# ---------------------------------------------------------------------------------------
# fallback league list
# ---------------------------------------------------------------------------------------
# Read-only, and only so the page still shows the three leagues when `universe_status()` is
# unavailable. Everything in these cards is static configuration (name, save, team count,
# reserve ceiling). Live values - season, stage, day, games played - are left blank rather
# than guessed, because a wrong "Preseason, day 0" is worse than an honest dash.
def _config_leagues():
    try:
        from .universe import config as cfg
    except Exception:  # noqa: BLE001
        return []
    out = []
    for spec in cfg.LEAGUES:
        out.append({
            "key": spec.key,
            "name": spec.name,
            "save": spec.save_name,
            "teams": len(spec.teams),
            "reserve_total": spec.reserve_capacity,
            "offline": True,
        })
    return out


# ---------------------------------------------------------------------------------------
# one run of the sim
# ---------------------------------------------------------------------------------------
STEP_ORDER = ("backup", "apply", "sim", "export", "publish", "points", "done")


class SimRun:
    """A single `run_sim` call: its parameters, its event log, and its subscribers.

    The event list is append-only, so a browser that reconnects mid-sim replays everything it
    missed and then carries on live. Each subscriber gets its own `queue.Queue`; registering a
    queue and snapshotting the backlog happen under the same lock, so an event can be neither
    dropped between the two nor delivered twice.
    """

    def __init__(self, leagues, days, dry_run):
        self.id = uuid.uuid4().hex[:12]
        self.leagues = list(leagues) if leagues else None
        self.days = int(days)
        self.dry_run = bool(dry_run)
        self.started_at = time.time()
        self.finished_at = None
        self.status = "running"   # running | ok | error
        self.error = ""
        self.result = None
        self.events = []
        self._subs = set()
        self._seq = 0
        self._lock = threading.Lock()

    # -- events ------------------------------------------------------------------------
    def emit(self, payload):
        """Append one event and hand it to every attached subscriber."""
        with self._lock:
            self._seq += 1
            now = time.time()
            prev = self.events[-1]["at"] if self.events else self.started_at
            event = dict(payload)
            event["seq"] = self._seq
            event["run"] = self.id
            event["at"] = now
            event["clock"] = datetime.fromtimestamp(now).strftime("%H:%M:%S")
            event["elapsed"] = round(now - self.started_at, 2)   # since the run started
            event["took"] = round(now - prev, 2)                 # since the previous event
            self.events.append(event)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # a subscriber that stopped reading is not worth stalling for
                pass
        return event

    def on_step(self, step):
        """The `on_step` callback handed to `run_sim`. Tolerates anything it is given."""
        return self.emit(_coerce_step(step))

    def subscribe(self, after=0):
        """(backlog, queue). Backlog is every event past `after` at the moment of joining."""
        q = queue.Queue(maxsize=2000)
        with self._lock:
            backlog = [e for e in self.events if e["seq"] > after]
            self._subs.add(q)
        return backlog, q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    # -- views -------------------------------------------------------------------------
    def summary(self):
        end = self.finished_at or time.time()
        return {
            "id": self.id,
            "status": self.status,
            "running": self.finished_at is None,
            "leagues": self.leagues,
            "days": self.days,
            "dry_run": self.dry_run,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds"),
            "finished_at": (datetime.fromtimestamp(self.finished_at).isoformat(timespec="seconds")
                            if self.finished_at else None),
            "elapsed": round(end - self.started_at, 1),
            "error": self.error,
            "events": len(self.events),
            "last_seq": self._seq,
        }


def _coerce_step(step):
    """Normalise whatever `run_sim` passes to `on_step` into a renderable event.

    The contract says a dict of step/league/message/pct, but a log line that arrives as a bare
    string, or with a missing key, must still show up in the log rather than kill the run.
    """
    if not isinstance(step, dict):
        return {"kind": "step", "step": "info", "league": None, "message": str(step), "pct": None}
    name = step.get("step") or "info"
    pct = step.get("pct")
    try:
        pct = max(0.0, min(100.0, float(pct))) if pct is not None else None
    except (TypeError, ValueError):
        pct = None
    event = {
        "kind": "step",
        "step": str(name),
        "league": step.get("league"),
        "message": str(step.get("message") or ""),
        "pct": pct,
    }
    extra = {k: v for k, v in step.items() if k not in ("step", "league", "message", "pct")}
    if extra:
        event["extra"] = json.loads(json.dumps(extra, default=str))
    return event


# ---------------------------------------------------------------------------------------
# the one-sim-at-a-time lock
# ---------------------------------------------------------------------------------------
# Module level on purpose: it is the whole safety story. Two sims would drive the same FBPB3
# window and write a corrupted save, so the second start is *refused*, never queued. This only
# holds inside one process, which is why the server never runs with the reloader (see `main`).
_SIM_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_CURRENT = None      # the most recent SimRun, running or finished
_HISTORY_HINT = []   # runs this process started, newest first - shown until run_history() has them


def current_run():
    with _STATE_LOCK:
        return _CURRENT


def sim_busy():
    run = current_run()
    return bool(run and run.finished_at is None)


def start_sim(leagues=None, days=7, dry_run=False):
    """Kick off a background sim. Returns (run, None) or (None, refusal message)."""
    global _CURRENT
    if not _SIM_LOCK.acquire(blocking=False):
        running = current_run()
        where = f" (run {running.id}, started {running.summary()['started_at']})" if running else ""
        return None, ("A sim is already running" + where + ". Only one sim may run at a time - two "
                      "would drive the same FBPB3 window and corrupt a save. Wait for it to finish.")
    run = SimRun(leagues, days, dry_run)
    try:
        thread = threading.Thread(target=_worker, args=(run,), name=f"simweek-{run.id}", daemon=True)
        thread.start()
    except Exception as exc:  # noqa: BLE001 - never leak the lock
        _SIM_LOCK.release()
        return None, f"Could not start the sim thread: {exc}"
    with _STATE_LOCK:
        _CURRENT = run
        _HISTORY_HINT.insert(0, run)
        del _HISTORY_HINT[12:]
    return run, None


def _worker(run):
    """Run one sim to completion. Must always end with the lock released and an `end` event.

    A leaked lock bricks the panel until restart, and a missing terminal event leaves every
    attached browser spinning, so both live in `finally`.
    """
    try:
        run.emit({
            "kind": "step", "step": "start", "league": None, "pct": 0,
            "message": "{}{} for {} day{} - {}".format(
                "DRY RUN: " if run.dry_run else "",
                ", ".join(run.leagues) if run.leagues else "all leagues",
                run.days, "" if run.days == 1 else "s",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        })
        result = run_sim(leagues=run.leagues, days=run.days, on_step=run.on_step,
                         dry_run=run.dry_run)
        run.result = json.loads(json.dumps(result, default=str)) if result is not None else None
        if run.status == "running":
            run.status = "ok"
    except SimweekUnavailable as exc:
        run.status = "error"
        run.error = str(exc)
        run.emit({"kind": "step", "step": "error", "league": None, "message": str(exc), "pct": None})
    except BaseException as exc:  # noqa: BLE001 - a crash in simweek is a log line, not a 500
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.emit({"kind": "step", "step": "error", "league": None, "pct": None,
                  "message": run.error,
                  "traceback": traceback.format_exc(limit=8)})
    finally:
        run.finished_at = time.time()
        _invalidate_status()
        run.emit({"kind": "end", "step": "end", "league": None, "pct": 100,
                  "status": run.status, "error": run.error,
                  "message": ("Finished" if run.status == "ok" else "Stopped with an error")
                             + f" after {round(run.finished_at - run.started_at, 1)}s"})
        _SIM_LOCK.release()


# ---------------------------------------------------------------------------------------
# universe status, cached
# ---------------------------------------------------------------------------------------
# `universe_status()` reads the three saves. CONVENTIONS.md: FBPB3 rewrites `league.dat` on
# save, so while a sim is driving the game we serve the last snapshot and mark it stale rather
# than reading a file the game has open. Otherwise a short TTL keeps a page refresh cheap.
_STATUS_TTL = 10.0
_status_cache = {"at": 0.0, "value": None, "error": ""}
_status_lock = threading.Lock()


def _invalidate_status():
    with _status_lock:
        _status_cache["at"] = 0.0


def universe_snapshot(force=False):
    """{"leagues": [...], "store": {...}, ...} plus `stale` / `error` / `degraded` markers."""
    with _status_lock:
        cached = dict(_status_cache)
    fresh_enough = cached["value"] is not None and (time.time() - cached["at"]) < _STATUS_TTL
    if sim_busy() and cached["value"] is not None:
        out = dict(cached["value"])
        out["stale"] = True
        out["stale_reason"] = "a sim is running - not reading league.dat while FBPB3 has it open"
        return out
    if fresh_enough and not force:
        return dict(cached["value"])

    error = ""
    value = None
    if SIMWEEK_OK:
        try:
            value = universe_status()
            if not isinstance(value, dict):
                raise TypeError(f"universe_status() returned {type(value).__name__}, expected dict")
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            value = None
    else:
        error = SIMWEEK_ERROR

    if value is None:
        value = {
            "leagues": _config_leagues(),
            "store": {"kind": "unknown", "configured": False, "detail": "simweek unavailable"},
            "game_running": None,
            "last_sim": None,
            "degraded": True,
            "error": error or "universe_status() is unavailable",
        }
    else:
        value = json.loads(json.dumps(value, default=str))
        value.setdefault("leagues", [])
        value.setdefault("store", {})
        value["degraded"] = False
        value["error"] = ""
    for league in value.get("leagues") or []:
        if isinstance(league, dict):
            league["site_href"] = _site_href(league)
    with _status_lock:
        _status_cache["at"] = time.time()
        _status_cache["value"] = value
        _status_cache["error"] = error
    return dict(value)


# ---------------------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------------------
app = Flask(__name__, template_folder=str(WEB / "templates"), static_folder=str(WEB / "static"))
try:  # keep dicts in the order they were built; cosmetic, and the attribute moved in Flask 2.3
    app.json.sort_keys = False
except Exception:  # noqa: BLE001
    pass


def api(fn):
    """Turn any exception into JSON. A control panel must never show a stack trace page."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SimweekUnavailable as exc:
            return jsonify({"ok": False, "error": str(exc), "simweek": False}), 503
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("%s failed", fn.__name__)
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
    wrapper.__name__ = fn.__name__
    return wrapper


def _body():
    return request.get_json(silent=True) or {}


def _simweek_block():
    return {"ok": SIMWEEK_OK, "error": SIMWEEK_ERROR}


@app.get("/")
def index():
    """The whole product. Rendered server-side so the leagues are on the page without JS."""
    status = universe_snapshot()
    run = current_run()
    boot = {
        "simweek": _simweek_block(),
        "universe": status,
        "run": run.summary() if run else None,
        "busy": sim_busy(),
        "defaults": {"week": 7, "chunk": 35},
    }
    return render_template("index.html", boot=boot, status=status, run=boot["run"],
                           simweek=boot["simweek"], defaults=boot["defaults"],
                           now=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.get("/api/state")
@api
def api_state():
    run = current_run()
    return jsonify({
        "ok": True,
        "simweek": _simweek_block(),
        "universe": universe_snapshot(force=request.args.get("refresh") == "1"),
        "run": run.summary() if run else None,
        "busy": sim_busy(),
    })


# -- running the sim --------------------------------------------------------------------
@app.post("/api/sim/start")
@api
def api_sim_start():
    body = _body()
    leagues = body.get("leagues") or None
    if leagues is not None:
        leagues = [str(k) for k in leagues if str(k).strip()]
        if not leagues:
            leagues = None
    try:
        days = int(body.get("days", 7))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "days must be a whole number"}), 400
    if not 1 <= days <= 400:
        return jsonify({"ok": False, "error": "days must be between 1 and 400"}), 400

    run, refusal = start_sim(leagues=leagues, days=days, dry_run=bool(body.get("dry_run")))
    if run is None:
        existing = current_run()
        return jsonify({"ok": False, "error": refusal, "busy": True,
                        "run": existing.summary() if existing else None}), 409
    return jsonify({"ok": True, "run": run.summary()}), 202


@app.get("/api/sim/stream")
def api_sim_stream():
    """Server-Sent Events for one run's log. No dependency beyond Flask.

    `?run=<id>` picks a run (default: the most recent), `?after=<seq>` resumes after an event
    the browser already has. With no run at all this answers a single `idle` event and closes,
    so a client - or curl - is never left hanging on an empty stream.
    """
    # Everything off `request` is read here, before the generator: inside it the request
    # context is already gone.
    wanted = request.args.get("run") or ""
    try:
        after = int(request.args.get("after", 0))
    except (TypeError, ValueError):
        after = 0
    run = current_run()
    if wanted and (run is None or run.id != wanted):
        run = None  # asked for a run this process does not have (restarted, or a stale tab)

    def stream():
        if run is None:
            yield _sse({"kind": "idle", "message": "No sim has been started yet.",
                        "simweek": SIMWEEK_OK})
            return
        backlog, q = run.subscribe(after=after)
        try:
            yield _sse({"kind": "open", "run": run.id, "summary": run.summary(),
                        "backlog": len(backlog)})
            for event in backlog:
                yield _sse(event)
                if event.get("kind") == "end":
                    return
            while True:
                try:
                    event = q.get(timeout=10)
                except queue.Empty:
                    # A comment keeps the connection (and any proxy) awake without
                    # reaching the browser's message handler.
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event)
                if event.get("kind") == "end":
                    return
        finally:
            run.unsubscribe(q)

    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["Connection"] = "keep-alive"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


def _sse(payload):
    return "data: " + json.dumps(payload, default=str) + "\n\n"


@app.get("/api/sim/log")
@api
def api_sim_log():
    """The current run's log as plain JSON, for anything that cannot hold a stream open."""
    run = current_run()
    if run is None:
        return jsonify({"ok": True, "run": None, "events": []})
    try:
        after = int(request.args.get("after", 0))
    except (TypeError, ValueError):
        after = 0
    return jsonify({"ok": True, "run": run.summary(),
                    "events": [e for e in run.events if e["seq"] > after]})


@app.get("/api/history")
@api
def api_history():
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    rows = []
    error = ""
    try:
        rows = list(run_history(limit=limit) or [])
    except SimweekUnavailable as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - history is informational; never fail the page
        error = f"{type(exc).__name__}: {exc}"
    return jsonify({
        "ok": not error,
        "error": error,
        "runs": json.loads(json.dumps(rows, default=str)),
        "this_session": [r.summary() for r in list(_HISTORY_HINT)],
    })


# -- the approval queue -----------------------------------------------------------------
@app.get("/api/pending")
@api
def api_pending():
    work = pending_work() or {}
    work = json.loads(json.dumps(work, default=str))
    return jsonify({
        "ok": True,
        "characters": work.get("characters") or [],
        "requests": work.get("requests") or [],
        "auto_approve": _auto_approve_from(work),
        "raw_keys": sorted(work.keys()),
    })


def _auto_approve_from(work):
    """The auto-approve flag as `pending_work()` reports it.

    The contract has no setter, so this is read-only: see `api_auto_approve`. Accepted shapes
    are a top-level `auto_approve` or one nested under `settings`.
    """
    if not isinstance(work, dict):
        return None
    if "auto_approve" in work:
        return bool(work["auto_approve"])
    settings = work.get("settings")
    if isinstance(settings, dict) and "auto_approve" in settings:
        return bool(settings["auto_approve"])
    return None


@app.post("/api/approve")
@api
def api_approve():
    ids = [str(i) for i in (_body().get("ids") or []) if str(i).strip()]
    if not ids:
        return jsonify({"ok": False, "error": "Nothing selected."}), 400
    count = approve(ids)
    return jsonify({"ok": True, "approved": count, "ids": ids})


@app.post("/api/reject")
@api
def api_reject():
    body = _body()
    request_id = str(body.get("id") or "").strip()
    if not request_id:
        return jsonify({"ok": False, "error": "No request id."}), 400
    reject(request_id, body.get("note") or "")
    return jsonify({"ok": True, "rejected": request_id})


@app.post("/api/auto-approve")
@api
def api_auto_approve():
    """Read the flag; refuse to write it, loudly.

    The simweek contract this panel is built against exposes `pending_work` (read) and
    `approve` / `reject` (act on specific request ids) and nothing else. There is no setter
    for the auto-approve setting, and writing it any other way would mean this app talking to
    the database directly, which is exactly what it must not do. So the toggle shows the live
    value and this endpoint says plainly that it cannot change it - better than a control that
    looks like it worked.
    """
    current = _auto_approve_from(pending_work() or {})
    return jsonify({
        "ok": False,
        "auto_approve": current,
        "error": ("The simweek contract has no setter for auto_approve - it exposes "
                  "pending_work (read) and approve/reject (per request). The panel will not "
                  "write the setting behind simweek's back. Add a setter to simweek and this "
                  "toggle turns on."),
    }), 501


@app.post("/api/character")
@api
def api_create_character():
    payload = _body().get("character") or _body()
    if not isinstance(payload, dict) or not payload:
        return jsonify({"ok": False, "error": "Empty character payload."}), 400
    created = create_character(payload)
    return jsonify({"ok": True, "character": json.loads(json.dumps(created, default=str))})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "simweek": SIMWEEK_OK, "busy": sim_busy()})


# ---------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m commissioner.app",
        description="The Cheezeyverse commissioner control panel (local, no auth).")
    ap.add_argument("--port", type=int, default=5095, help="port to serve on (default 5095)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interface to bind (default 127.0.0.1; see the note in the source)")
    args = ap.parse_args(argv)

    print(f"Cheezeyverse commissioner  ->  http://{args.host}:{args.port}")
    if not SIMWEEK_OK:
        print(f"  ! commissioner.simweek is unavailable: {SIMWEEK_ERROR}")
        print("    The panel will start and explain itself, but nothing can touch a save.")

    # 127.0.0.1, never 0.0.0.0: this panel has no authentication of any kind, and every button
    # on it can rewrite a save file or spend somebody's points. Binding the loopback interface
    # is what keeps it off the LAN. If it ever needs to be reachable from another machine, the
    # answer is a tunnel, not a wider bind.
    #
    # use_reloader=False, deliberately: the reloader runs a second process, which would mean a
    # second `_SIM_LOCK` - and "only one sim at a time, ever" would quietly stop being true.
    # debug is off for the same reason (it implies the reloader) and because a debugger console
    # on an unauthenticated port is a remote shell.
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
