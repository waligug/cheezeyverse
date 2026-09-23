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

**The offseason is the one exception to "only simweek".** `commissioner.offseason` is its own
entry point (`run_offseason`), so the panel calls it directly - but it gets the store from
`simweek.store()`, the same object the sim writes through, and it runs under the same
`_SIM_LOCK`. An offseason drives the same three saves a sim does; the two must never overlap.
It imports separately from `simweek`, so the offseason panel can be dark while the sim works.
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

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"


class SimweekUnavailable(RuntimeError):
    """`commissioner.simweek` did not import. Every button that needs it says so."""


class OffseasonUnavailable(RuntimeError):
    """`commissioner.offseason` (or the store behind it) did not import. Same deal."""


# ---------------------------------------------------------------------------------------
# the simweek contract - the only thing this app is allowed to call
# ---------------------------------------------------------------------------------------
# Exactly these eight names, nothing else. A failure here is a banner, never a traceback:
# the panel still starts so the owner can see *why* it is broken.
SIMWEEK_OK = True
SIMWEEK_ERROR = ""
try:  # pragma: no cover - the failure path is exercised by starting with simweek absent
    # readiness rides in the same guard: it imports simweek, so if simweek is broken this is
    # broken too, and the panel must still start to say so.
    from . import readiness
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
# the offseason contract - imported apart from simweek, on purpose
# ---------------------------------------------------------------------------------------
# Four names from `offseason` plus the store from `simweek`, and this fails as its own thing:
# the sim can be perfectly healthy while the offseason is unimportable, and the panel should
# then still sim. `OffseasonError` is re-declared in the failure branch only so the `except`
# clauses further down stay valid - it can never be raised there, because nothing runs.
OFFSEASON_OK = True
OFFSEASON_ERROR = ""
try:  # pragma: no cover - exercised by importing with commissioner.offseason blocked
    from .offseason import (  # type: ignore
        OffseasonError,
        conversion_for,
        movers,
        run_offseason,
        saved_result,
    )
    from .simweek import store as offseason_store  # type: ignore
except Exception as _exc:  # noqa: BLE001
    OFFSEASON_OK = False
    OFFSEASON_ERROR = f"{type(_exc).__name__}: {_exc}"

    class OffseasonError(Exception):  # type: ignore[no-redef]
        """Placeholder. Unreachable: nothing that could raise it is importable."""

    def _no_offseason(*_a, **_k):
        raise OffseasonUnavailable(
            "commissioner.offseason is not importable, so the offseason cannot be previewed or "
            "run. " + OFFSEASON_ERROR
        )

    conversion_for = movers = run_offseason = offseason_store = _no_offseason  # type: ignore


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
    """A single `run_sim` *or* `run_offseason` call: parameters, event log, subscribers.

    The event list is append-only, so a browser that reconnects mid-sim replays everything it
    missed and then carries on live. Each subscriber gets its own `queue.Queue`; registering a
    queue and snapshotting the backlog happen under the same lock, so an event can be neither
    dropped between the two nor delivered twice.

    One class for both kinds deliberately: an offseason takes minutes and drives the same saves,
    so it wants the same live log, the same stream, the same "busy" chip and the same lock. Only
    `kind` and a couple of fields differ.
    """

    def __init__(self, leagues, days, dry_run, kind="sim", season=None, force=False,
                 allow_season_end=False):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind          # "sim" | "offseason"
        self.leagues = list(leagues) if leagues else None
        self.days = int(days)
        self.dry_run = bool(dry_run)
        # sim only: permission to sim across the last day of the regular season and into the
        # playoffs. Off by default because what lies past the playoffs is FBPB3's own rollover,
        # which nothing here has ever driven.
        self.allow_season_end = bool(allow_season_end)
        self.season = season      # offseason only; None means "whatever the store says"
        self.force = bool(force)  # offseason only
        self.refused = False      # offseason only: the already-run guard said no
        self.started_at = time.time()
        self.finished_at = None
        self.status = "running"   # running | ok | error | refused
        self.error = ""
        self.result = None
        self.events = []
        self._subs = set()
        self._seq = 0
        self._lock = threading.Lock()
        self._log_league = None   # the league the offseason's log lines are currently about
        self._log_pct = 0.0

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

    def log_line(self, *parts):
        """The `log=` callable handed to `run_offseason`, adapted to the same event stream.

        `run_offseason` has no `on_step`: it takes `log`, which is `print` by default and gets
        one already-indented string per thing that happened. So this is the whole adapter -
        read the line, guess which stage it belongs to, and emit the same shape the sim emits.
        The guessing is display only: the log line itself is always passed through verbatim, so
        a mis-guessed step costs a wrong colour and nothing else.
        """
        text = " ".join(str(p) for p in parts)
        step, league, pct = _offseason_step(text, self._log_league, self._log_pct)
        self._log_league = league
        if pct is not None:
            self._log_pct = pct
        return self.emit({"kind": "step", "step": step, "league": league,
                          "message": text.strip(), "pct": pct})

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
            "kind": self.kind,
            "status": self.status,
            "running": self.finished_at is None,
            "leagues": self.leagues,
            "days": self.days,
            "calendar_plan": getattr(self, "calendar_plan", None),
            "dry_run": self.dry_run,
            "allow_season_end": self.allow_season_end,
            "season": self.season,
            "force": self.force,
            "refused": self.refused,
            "has_result": self.result is not None,
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


# The offseason's log is prose, not steps, so the stage has to be read back out of the line.
# These are the literal `log(...)` calls in offseason.py, in the order they happen. The bar is
# an estimate and the panel says so: nothing in `run_offseason` reports progress.
_OFFSEASON_PCT = {"start": 0, "retire": 6, "growth": 14, "movers": 40, "promote": 52,
                  "convert": 50, "refill": 56, "draft": 72, "points": 90, "done": 98}


def _offseason_step(line, league=None, pct=0.0):
    """(step, league, pct) for one `log=` line. Guessing only - the text is never changed."""
    text = str(line).strip()
    low = text.lower()

    head = low.split(":", 1)[0]
    if head in ("prep", "college", "pro") and "growth" in low:
        league = head
        step = "growth"
    elif text.startswith("!"):
        # Every failure offseason.py reports to the log starts with "!": a character the codec
        # could not find, a promotion with no slot left, a pick that blew up. Never quiet.
        step = "failed"
    elif text.startswith("("):
        step = "warn"                     # "(could not record the level for ...)"
    elif low.startswith("offseason complete"):
        # Checked before the retirement rule: the closing line ends "... , 1 retired".
        step = "done"
    elif low.startswith("who is still here") or " retire" in low:
        step = "retire"                   # careers the offseason (or the game) has ended
    elif " grew " in low:
        step = "growth"
    elif low.startswith("moving up:"):
        league, step = None, "movers"
    elif low.startswith("draft order") or text.startswith("#"):
        step = "draft"
    elif "% across" in low or "carries" in low:
        step = "convert"
    elif "is free again" in low:
        step = "refill"
    elif "->" in text:
        step = "promote"
    elif low.startswith("paid "):
        step = "points"
    else:
        step = "info"

    want = _OFFSEASON_PCT.get(step)
    if want is None or step in ("failed", "warn"):
        return step, league, None         # a failure must not move the bar, in either direction
    return step, league, max(float(pct or 0.0), float(want))


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


def busy_run():
    """The run that is actually going, or None. A *finished* run is not a reason to be busy.

    A refusal answers with the run that is in the way, and the browser reattaches to whatever it
    is handed - so handing it the last finished run would leave the panel showing a live sim
    that ended ten minutes ago.
    """
    run = current_run()
    return run if (run is not None and run.finished_at is None) else None


def _acquire_or_refuse():
    """Take `_SIM_LOCK` or explain, in words, who has it. Returns "" on success.

    The offseason holds it too: growth, promotions and the draft all write the same three saves
    a sim does, so "one at a time" means one of *either*, not one of each.
    """
    if _SIM_LOCK.acquire(blocking=False):
        return ""
    running = current_run()
    if running is not None and running.finished_at is None:
        what = "An offseason" if running.kind == "offseason" else "A sim"
        where = f" (run {running.id}, started {running.summary()['started_at']})"
    else:
        # The lock is held by something with no run behind it - a dry-run preview, or a run in
        # the half-second between taking the lock and being registered.
        what, where = "Something else", " (a preview, or a run that has just started)"
    return (what + " is already using the saves" + where + ". Only one may run at a time - two "
            "would drive the same FBPB3 window and corrupt a save. Wait for it to finish.")


def start_sim(leagues=None, days=7, dry_run=False, allow_season_end=False):
    """Kick off a background sim. Returns (run, None) or (None, refusal message)."""
    global _CURRENT
    refusal = _acquire_or_refuse()
    if refusal:
        return None, refusal
    run = SimRun(leagues, days, dry_run, allow_season_end=allow_season_end)
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


def start_offseason(season=None, force=False):
    """Kick off a background offseason for real. Returns (run, None) or (None, refusal).

    Never a dry run: a dry run is `/api/offseason/preview`, which answers in one request
    because it takes half a second and writes nothing. This is the one that changes the world.
    """
    global _CURRENT
    refusal = _acquire_or_refuse()
    if refusal:
        return None, refusal
    run = SimRun(None, 0, False, kind="offseason", season=season, force=force)
    try:
        thread = threading.Thread(target=_offseason_worker, args=(run,),
                                  name=f"offseason-{run.id}", daemon=True)
        thread.start()
    except Exception as exc:  # noqa: BLE001 - never leak the lock
        _SIM_LOCK.release()
        return None, f"Could not start the offseason thread: {exc}"
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
            "message": "{}{} for {} day{}{} - {}".format(
                "DRY RUN: " if run.dry_run else "",
                ", ".join(run.leagues) if run.leagues else "all leagues",
                run.days, "" if run.days == 1 else "s",
                ", allowed into the playoffs" if run.allow_season_end else "",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        })
        # allow_season_end travels all the way to run_sim or it does nothing at all. The flag was
        # added at the endpoint first and stopped here, which is the inert-guard shape this
        # project keeps meeting: everything reports success and the behaviour never changes.
        result = run_sim(leagues=run.leagues, days=run.days, on_step=run.on_step,
                         dry_run=run.dry_run, allow_season_end=run.allow_season_end)
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
        # simweek keeps a lock of its own, so a sim started outside this panel (a script, a
        # second process) refuses us. That is not a crash and it must not read like one: this
        # panel started nothing and nothing was written.
        # A deliberate refusal is not a crash. The offseason already established this shape
        # (see `refused` below), and without it the useful sentence arrives dressed as a bug
        # report, under a red banner, with a traceback.
        if type(exc).__name__ == "SeasonEnd":
            run.status = "refused"
            run.refused = True
            run.error = str(exc)
            run.emit({"kind": "step", "step": "refused", "league": None, "pct": 100,
                      "message": str(exc)})
        elif type(exc).__name__ == "SimBusy":
            run.error = ("Another process is already simming - this panel did not start "
                         f"anything and nothing was written. ({exc})")
            run.emit({"kind": "step", "step": "error", "league": None, "pct": None,
                      "message": run.error})
        else:
            run.emit({"kind": "step", "step": "error", "league": None, "pct": None,
                      "message": run.error,
                      "traceback": traceback.format_exc(limit=8)})
    finally:
        _finish(run)


def _finish(run):
    """End one run: timestamp, terminal event, lock released. Always runs, for both kinds.

    A leaked lock bricks the panel until restart and a missing `end` leaves every attached
    browser spinning, so this is the only place either of those can be got wrong.
    """
    run.finished_at = time.time()
    _invalidate_status()
    took = f" after {round(run.finished_at - run.started_at, 1)}s"
    headline = {"ok": "Finished", "refused": "Refused"}.get(run.status, "Stopped with an error")
    run.emit({"kind": "end", "step": "end", "league": None, "pct": 100,
              "status": run.status, "error": run.error, "refused": run.refused,
              "kind_of_run": run.kind, "message": headline + took})
    _SIM_LOCK.release()


def _offseason_worker(run):
    """Run one real offseason to completion. Same rules as `_worker`: never leak, never crash.

    The refusal is the interesting path. `run_offseason` raises `OffseasonError` when that
    season has already been run, because every part of it is cumulative - inches, points,
    college years, promotions - so a second run pays everybody twice. That is a *message*, not
    an error page and not a traceback: the panel says what it says and offers the force button.
    """
    try:
        run.emit({
            "kind": "step", "step": "start", "league": None, "pct": 0,
            "message": "offseason for season {}{} - {}".format(
                run.season if run.season is not None else "(the store's current season)",
                ", FORCED" if run.force else "",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        })
        store = offseason_store()
        result = run_offseason(store, season=run.season, log=run.log_line,
                               dry_run=False, force=run.force, rollover=True)
        run.result = _offseason_view(result)
        if isinstance(result, dict) and result.get("season") is not None:
            run.season = result["season"]     # what it actually ran, not what was asked for
        if run.status == "running":
            run.status = "ok"
    except OffseasonError as exc:
        run.status = "refused"
        run.refused = True
        run.error = str(exc)
        run.emit({"kind": "step", "step": "refused", "league": None, "pct": None,
                  "message": str(exc)})
    except OffseasonUnavailable as exc:
        run.status = "error"
        run.error = str(exc)
        run.emit({"kind": "step", "step": "error", "league": None, "message": str(exc),
                  "pct": None})
    except BaseException as exc:  # noqa: BLE001 - a crash in offseason.py is a log line
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.emit({"kind": "step", "step": "error", "league": None, "pct": None,
                  "message": run.error, "traceback": traceback.format_exc(limit=8)})
    finally:
        _finish(run)


# ---------------------------------------------------------------------------------------
# reading what the offseason says
# ---------------------------------------------------------------------------------------
# `run_offseason` answers with live character dicts - ratings, potentials, claimed slots, the
# lot. None of that belongs on the wire, and `grown` is a count rather than a list, so these
# turn the result into exactly what the panel draws and nothing else.
def _person(character):
    if character is None:
        # `failed` can carry no character at all: a save that would not open is a failure
        # about a whole league, not about a person.
        return {"name": "(no character - the whole league)", "position": "", "league": "",
                "team": "", "id": None, "college_years": None, "declared": False}
    if not isinstance(character, dict):
        return {"name": str(character)}
    name = " ".join(str(character.get(k) or "").strip()
                    for k in ("first_name", "last_name")).strip()
    return {
        "id": character.get("id"),
        "name": name or character.get("name") or character.get("id") or "?",
        "position": character.get("position") or "",
        "league": character.get("league") or "",
        "team": character.get("team_abbrev") or "",
        "college_years": character.get("college_years"),
        "declared": bool(character.get("declared")),
    }


def _conversion_for(character, to_league, season):
    """`conversion_for`, but never a reason for the page to fail."""
    if not to_league:
        return None
    try:
        return conversion_for(character, to_league, season)
    except Exception:  # noqa: BLE001
        return None


def _conversion(conv, projected=False):
    if not isinstance(conv, dict):
        return None
    try:
        factor = float(conv.get("factor"))
    except (TypeError, ValueError):
        return None
    return {
        "factor": factor,
        "percent": int(round(factor * 100)),
        "early_years": int(conv.get("early_years") or 0),
        "early_penalty": conv.get("early_penalty"),
        "base": conv.get("base"),
        # True when the panel worked it out with `conversion_for` instead of reading it off a
        # completed promotion - a dry run never calls `promote`, so its picks carry no
        # conversion of their own.
        "projected": bool(projected),
    }


def _offseason_view(result):
    """The result dict, slimmed for the page. Every key it is given survives; none is invented.

    `paid` and `developed` are absent from a dry run (nobody is paid), so they come through as
    null rather than a misleading 0.
    """
    if not isinstance(result, dict):
        return {"error": f"run_offseason returned {type(result).__name__}, expected dict"}
    season = result.get("season")
    out = {
        "season": season,
        "season_records": result.get("season_records", []),
        "archive_only": bool(result.get("archive_only")),
        "dry_run": bool(result.get("dry_run")),
        "grown": result.get("grown", 0),
        "paid": result.get("paid"),
        "next_season": result.get("next_season"),
        "rollover": result.get("rollover", []),
        "publish_error": result.get("publish_error"),
        "developed": result.get("developed"),
        # The promotion grant, keyed by character id. It is the largest payment an offseason
        # makes and the view is a whitelist, so leaving it out meant a preview could report
        # seven promotions and say nothing about the points they came with - which is the one
        # number somebody checks before an irreversible rollover.
        "promotion_grants": result.get("promotion_grants") or {},
        # The pro contract payout, which REPLACES the flat lump for that league - so without it
        # the panel would show "paid N the offseason lump" and be wrong about every pro.
        "contract_payouts": result.get("contract_payouts") or {},
        "retired": [],
        "promoted": [],
        "drafted": [],
        "failed": [],
    }
    for row in result.get("retired") or []:
        row = row if isinstance(row, dict) else {}
        out["retired"].append({
            "character": _person(row.get("character")),
            "league": row.get("league") or "",
            "reason": str(row.get("reason") or ""),
            "slot_refilled": bool(row.get("slot_refilled")),
        })
    for row in result.get("promoted") or []:
        row = row if isinstance(row, dict) else {}
        character = row.get("character") or {}
        conv = row.get("conversion")
        out["promoted"].append({
            "character": _person(character),
            "to": row.get("to") or "",
            "team": row.get("team") or "",
            "slot": row.get("slot") or None,
            "conversion": _conversion(conv or _conversion_for(character, row.get("to"), season),
                                      projected=not conv),
        })
    for pick in result.get("drafted") or []:
        pick = pick if isinstance(pick, dict) else {}
        character = pick.get("character") or {}
        conv = pick.get("conversion")
        out["drafted"].append({
            "pick": pick.get("pick"),
            "round": pick.get("round"),
            "team": pick.get("team") or "",
            "character": _person(character),
            "conversion": _conversion(conv or _conversion_for(character, "pro", season),
                                      projected=not conv),
            "slot": pick.get("slot") or None,
            "error": str(pick.get("error") or ""),
            # WHY the pick happened, not just that it did. This view is a whitelist, so every
            # one of these was being dropped on the floor: the panel showed a name and a team
            # and none of the reasoning that draft night is actually made of - which is the same
            # way promotion_grants went missing from a preview somebody reads before an
            # irreversible rollover.
            "reason": str(pick.get("reason") or ""),
            "profile": str(pick.get("profile") or ""),
            "need": str(pick.get("need") or ""),
            "snub": str(pick.get("snub") or ""),
            "expected": pick.get("expected"),
            "is_character": bool(pick.get("is_character", True)),
            "contract": pick.get("contract") or None,
        })
    # `run_draft` returns the failures last; the board reads in pick order, failures in place.
    out["drafted"].sort(key=lambda p: (p.get("pick") is None, p.get("pick") or 0))
    for row in result.get("failed") or []:
        row = row if isinstance(row, dict) else {}
        out["failed"].append({
            "character": _person(row.get("character")),
            "league": row.get("league") or "",
            "stage": str(row.get("stage") or ""),
            "error": str(row.get("error") or ""),
        })
    # One number the page can shout with: anybody the save and the store now disagree about.
    out["trouble"] = len(out["failed"]) + sum(1 for p in out["drafted"] if p["error"])
    # Anything `run_offseason` grew that this panel has not been taught to draw. Counted, not
    # dropped: a stage that appears in the result and nowhere on the page is how a panel starts
    # lying. Lists become lengths so a future stage cannot dump character sheets onto the wire.
    known = set(out) | {"dry_run"}
    other = {}
    for key, value in result.items():
        if key in known:
            continue
        other[key] = len(value) if isinstance(value, (list, tuple, dict)) else value
    if other:
        out["other"] = other
    return json.loads(json.dumps(out, default=str))


def offseason_plan():
    """What the offseason is about to be asked to do - without opening a single save.

    `movers()` and `conversion_for()` need only the store, so this is cheap enough to render on
    every page load and safe while a sim is running: it never touches `league.dat`. The dry run,
    which does read the saves, is a button.
    """
    plan = {"available": False, "error": "", "season": None, "last_offseason": None,
            "already_run": False, "to_college": [], "to_draft": [], "staying": None,
            "characters": None, "store": None}
    if not OFFSEASON_OK:
        plan["error"] = OFFSEASON_ERROR or "commissioner.offseason is unavailable"
        return plan
    try:
        st = offseason_store()
        settings = st.get_settings() or {}
        season = settings.get("current_season")
        last = settings.get("last_offseason")
        plan["season"] = int(season) if season is not None else None
        plan["last_offseason"] = int(last) if last is not None else None
        plan["store"] = st.kind() if hasattr(st, "kind") else None
        if plan["season"] is None:
            plan["error"] = "the store has no current_season, so there is no season to run"
            return plan
        plan["already_run"] = (plan["last_offseason"] is not None
                               and plan["last_offseason"] >= plan["season"])
        rows = list(st.characters() or [])
        plan["characters"] = len(rows)
        split = movers(rows, plan["season"]) or {}
        for character in split.get("college") or []:
            plan["to_college"].append(dict(
                _person(character),
                conversion=_conversion(_conversion_for(character, "college", plan["season"]))))
        for character in split.get("draft") or []:
            plan["to_draft"].append(dict(
                _person(character),
                conversion=_conversion(_conversion_for(character, "pro", plan["season"]))))
        plan["staying"] = len(split.get("stay") or [])
        from .seasonflow import readiness
        plan["transition"] = readiness(plan["season"])
        plan["available"] = True
    except Exception as exc:  # noqa: BLE001 - the plan is informational; never fail the page
        plan["error"] = f"{type(exc).__name__}: {exc}"
    return json.loads(json.dumps(plan, default=str))


# ---------------------------------------------------------------------------------------
# universe status, cached
# ---------------------------------------------------------------------------------------
# `universe_status()` reads the three saves. CONVENTIONS.md: FBPB3 rewrites `league.dat` on
# save, so while a sim is driving the game we serve the last snapshot and mark it stale rather
# than reading a file the game has open. Otherwise a short TTL keeps a page refresh cheap.
_STATUS_TTL = 10.0
_status_cache = {"at": 0.0, "value": None, "error": ""}
_status_lock = threading.Lock()

SITE = ROOT / "site"


def _site_href(league):
    """A clickable URL for a league's published pages, or "" when nothing is published yet.

    `universe_status()` reports a repo-relative path (`site/leagues/prep/index.htm`); a browser
    cannot follow a file:// link from an http page, so the panel serves `site/` itself (read
    only, see `serve_site`) and this turns that path into `/site/leagues/prep/index.htm`.
    """
    raw = str(league.get("site") or "").replace("\\", "/").lstrip("/")
    if not raw:
        key = str(league.get("key") or "")
        raw = f"site/leagues/{key}/index.htm" if key else ""
    if not raw:
        return ""
    rel = raw[5:] if raw.startswith("site/") else raw
    return f"/site/{rel}" if (SITE / rel).exists() else ""


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
        except OffseasonUnavailable as exc:
            return jsonify({"ok": False, "error": str(exc), "offseason": False}), 503
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("%s failed", fn.__name__)
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
    wrapper.__name__ = fn.__name__
    return wrapper


def _body():
    return request.get_json(silent=True) or {}


def _simweek_block():
    return {"ok": SIMWEEK_OK, "error": SIMWEEK_ERROR}


# How long a sim takes, fitted from the runs that have actually happened.
_PACE_TTL = 120.0
_pace_cache = {"at": 0.0, "value": None}


def sim_pace(limit=60):
    """{"fixed", "per_day", "samples", "spread"} seconds, or None when the log cannot say.

    A SIM IS NOT PRICED BY THE DAY. Measured on this universe, seven days across three leagues
    takes about eleven minutes and twenty-one days takes about seventeen - because most of a run
    is fixed work that happens once per league however many days were asked for: loading the
    save, exporting, the MDB, the publish, the points. Multiplying a per-day figure by the days
    asked for therefore over-states a long run badly and under-states a short one, which is the
    wrong way round for the number people use to decide whether they have time.

    So it is a two-term least-squares fit, `seconds = fixed*leagues + per_day*leagues*days`, run
    against the store's own log rather than hardcoded. Constants measured today would drift as
    rosters grow and quietly become a lie; a fit re-reads reality every time.

    FITTED ONLY ON RUNS THAT DID THE WHOLE JOB. A resumed run skips the setup it already did -
    one such run sits 43% under the curve - and a refused or dry run did almost nothing at all.
    Including them teaches the model that sims are faster than they are, which is the direction
    that makes somebody start one they do not have time for.

    `spread` is the worst this fit has been wrong on its own samples, so the caller can say
    "about 17 minutes" with a band rather than a false precision.
    """
    now = time.time()
    if _pace_cache["value"] is not None and now - _pace_cache["at"] < _PACE_TTL:
        return _pace_cache["value"]

    rows = []
    try:
        rows = list(run_history(limit=limit) or [])
    except Exception:  # noqa: BLE001 - an estimate is never worth an error page
        rows = []

    points = []
    for r in rows:
        if not r.get("ok") or r.get("dry_run") or r.get("resumed"):
            continue
        try:
            days, secs = int(r.get("days") or 0), float(r.get("seconds") or 0)
        except (TypeError, ValueError):
            continue
        leagues = len(r.get("leagues") or []) or 3
        if r.get("days_by_league"):
            try:
                days = sum(float(n) for n in r["days_by_league"].values()) / leagues
            except (TypeError, ValueError):
                continue
        # 400 is the endpoint's own ceiling; anything past it is a mis-typed run, not a data point
        if days <= 0 or days > 400 or secs <= 0:
            continue
        points.append((leagues, days, secs))

    value = None
    if len(points) >= 3:
        s11 = s12 = s22 = t1 = t2 = 0.0
        for leagues, days, secs in points:
            x1, x2 = float(leagues), float(leagues * days)
            s11 += x1 * x1
            s12 += x1 * x2
            s22 += x2 * x2
            t1 += x1 * secs
            t2 += x2 * secs
        det = s11 * s22 - s12 * s12
        if det:
            fixed = (t1 * s22 - t2 * s12) / det
            per_day = (s11 * t2 - s12 * t1) / det
            # A negative term means the sample is too narrow to separate the two costs - every
            # run had the same shape. Refuse rather than promise a sim gets faster the longer
            # it runs, which is what a negative per_day renders as.
            if fixed > 0 and per_day > 0:
                worst = max(abs(fixed * lg + per_day * lg * dy - sc) / sc
                            for lg, dy, sc in points)
                value = {"fixed": round(fixed, 1), "per_day": round(per_day, 2),
                         "samples": len(points), "spread": round(worst, 2)}

    _pace_cache.update({"at": now, "value": value})
    return value


def _offseason_block():
    return {"ok": OFFSEASON_OK, "error": OFFSEASON_ERROR}


@app.get("/")
def index():
    """The whole product. Rendered server-side so the leagues are on the page without JS."""
    status = universe_snapshot()
    run = current_run()
    plan = offseason_plan()
    boot = {
        "simweek": _simweek_block(),
        "offseason": _offseason_block(),
        "plan": plan,
        "universe": status,
        "run": run.summary() if run else None,
        "busy": sim_busy(),
        "pace": sim_pace(),
        # 21, not 35. 35 was the old advice and the season boundary now refuses it; the
        # box should not pre-fill a number that gets rejected. 21 is three clean weeks
        # and sits under the tightest league's remaining room.
        "defaults": {"week": 7, "chunk": 21},
    }
    return render_template("index.html", boot=boot, status=status, run=boot["run"],
                           busy=boot["busy"],
                           simweek=boot["simweek"], offseason=boot["offseason"], plan=plan,
                           defaults=boot["defaults"],
                           now=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.get("/api/state")
@api
def api_state():
    run = current_run()
    return jsonify({
        "ok": True,
        "simweek": _simweek_block(),
        "offseason": _offseason_block(),
        "plan": offseason_plan(),
        "universe": universe_snapshot(force=request.args.get("refresh") == "1"),
        "run": run.summary() if run else None,
        "busy": sim_busy(),
        "pace": sim_pace(),
    })


# Calendar previews are recomputed server-side; a client never supplies arbitrary day counts.
@app.get("/api/calendar")
@api
def api_calendar():
    from .calendarplan import snapshot, CalendarError
    try:
        return jsonify({"ok": True, **snapshot()})
    except (CalendarError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409


@app.post("/api/calendar/plan")
@api
def api_calendar_plan():
    from .calendarplan import snapshot, plan, CalendarError
    body = _body()
    try:
        data = snapshot()
        result = plan(data, body.get("reference"), body.get("target"))
        return jsonify({"ok": True, "plan": result})
    except (CalendarError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409


@app.post("/api/calendar/start")
@api
def api_calendar_start():
    global _CURRENT
    from .calendarplan import snapshot, plan, CalendarError
    body = _body()
    refusal = _acquire_or_refuse()
    if refusal:
        return jsonify({"ok": False, "error": refusal}), 409
    run = None
    try:
        data = snapshot()
        if body.get("token") != data["token"]:
            raise CalendarError("The calendar changed. Preview the target again before starting.")
        chosen = plan(data, body.get("reference"), body.get("target"))
        rows = [r for r in chosen["leagues"] if r["days"] > 0]
        if not rows:
            raise CalendarError("Every league is already at or beyond this target.")
        run = SimRun([r["key"] for r in rows], max(r["days"] for r in rows), False, kind="calendar")
        run.calendar_plan = chosen
        with _STATE_LOCK:
            _CURRENT = run
            _HISTORY_HINT.insert(0, run)
            del _HISTORY_HINT[12:]
        threading.Thread(target=_calendar_worker, args=(run,), name=f"calendar-{run.id}", daemon=True).start()
        return jsonify({"ok": True, "run": run.summary()}), 202
    except Exception as exc:
        if run is not None:
            run.status, run.error, run.finished_at = "error", str(exc), time.time()
        _SIM_LOCK.release()
        return jsonify({"ok": False, "error": str(exc)}), 409


def _calendar_worker(run):
    try:
        rows = [r for r in run.calendar_plan["leagues"] if r["days"] > 0]
        for row in rows:
            run.emit({"kind": "step", "step": "start", "league": row["key"],
                      "message": f"{row['key']}: play through {row['through']} ({row['days']} days)", "pct": 0})
        result = run_sim(leagues=[r["key"] for r in rows], days=max(r["days"] for r in rows),
            days_by_league={r["key"]: r["days"] for r in rows}, allow_season_end=True,
            expected_states={r["key"]: (r["expected_day"], r["season"]) for r in rows},
            start_dates={r["key"]: r["from"] for r in rows},
            on_step=run.on_step)
        run.result = {"ok": True, "calendar": run.calendar_plan, "run": result}
        run.status = "ok"
    except BaseException as exc:
        run.status, run.error = "error", str(exc)
        run.emit({"kind": "step", "step": "error", "message": str(exc), "pct": None})
    finally:
        _finish(run)


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

    run, refusal = start_sim(leagues=leagues, days=days, dry_run=bool(body.get("dry_run")),
                             allow_season_end=bool(body.get("allow_season_end")))
    if run is None:
        existing = busy_run()
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
    # EventSource resends the last `id:` it saw as Last-Event-ID when it reconnects on its own,
    # so a dropped connection resumes exactly where it stopped instead of replaying the log.
    try:
        after = int(request.headers.get("Last-Event-ID") or request.args.get("after", 0))
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
                    if run.finished_at is not None:
                        return  # finished, nothing further coming: do not hold the socket open
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
    """One SSE frame. `id:` is the event sequence, which is what makes Last-Event-ID work."""
    seq = payload.get("seq")
    head = f"id: {seq}\n" if seq else ""
    return head + "data: " + json.dumps(payload, default=str) + "\n\n"


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


# -- the offseason ----------------------------------------------------------------------
@app.get("/api/offseason/plan")
@api
def api_offseason_plan():
    """Who has outgrown his level, read from the store alone. No save is opened."""
    plan = offseason_plan()
    return jsonify({"ok": bool(plan.get("available")), "plan": plan,
                    "error": plan.get("error") or ""})


@app.post("/api/offseason/preview")
@api
def api_offseason_preview():
    """A dry run, answered in this request. Reads the three saves, writes absolutely nothing.

    `dry_run=True` stages nothing: no backup is taken, `apply_growth` measures without writing,
    `promote` reports the slot it would claim and returns before stamping it, the draft is
    listed and not run, and no point, setting or college year is banked. It still takes the
    lock, because it *reads* `league.dat` and a sim has the game holding that file open.
    """
    if not OFFSEASON_OK:
        return jsonify({"ok": False, "offseason": False, "error": (
            "commissioner.offseason is not importable, so there is nothing to preview. "
            + OFFSEASON_ERROR)}), 503
    season = _season_arg(_body().get("season"))
    if season is False:
        return jsonify({"ok": False, "error": "season must be a year, e.g. 2047"}), 400

    refusal = _acquire_or_refuse()
    if refusal:
        existing = busy_run()
        return jsonify({"ok": False, "error": refusal, "busy": True,
                        "run": existing.summary() if existing else None}), 409
    lines = []
    try:
        result = run_offseason(offseason_store(), season=season, log=lines.append, dry_run=True)
    except OffseasonError as exc:   # the guard is skipped for a dry run, but never assume it
        return jsonify({"ok": False, "refused": True, "error": str(exc), "log": lines}), 409
    except Exception as exc:  # noqa: BLE001 - a readable sentence beats a 500 and a type name
        return jsonify({"ok": False, "log": lines, "error": (
            f"The dry run could not read the saves: {type(exc).__name__}: {exc}. FBPB3 rewrites "
            "league.dat as it saves, so the usual cause is the game being mid-save (or a league "
            "file that is not where the config says it is).")}), 500
    finally:
        _SIM_LOCK.release()
    return jsonify({"ok": True, "result": _offseason_view(result), "log": lines,
                    "plan": offseason_plan()})


@app.post("/api/offseason/start")
@api
def api_offseason_start():
    """Run the offseason for real, in the background, streaming to the same log as a sim.

    `confirm` is required. The browser asks first, but this endpoint is on an unauthenticated
    port and a stray POST must not age a universe by a year - so the confirmation is part of
    the request, not only part of the page.

    `force` skips the already-run guard and is for exactly one situation: a run that started
    and did not finish. Everything the offseason does is cumulative, so forcing a season that
    really did complete pays everybody twice and grows everybody twice. The panel only offers
    it after a refusal, behind its own checkbox and its own confirm.
    """
    if not OFFSEASON_OK:
        return jsonify({"ok": False, "offseason": False, "error": (
            "commissioner.offseason is not importable, so the offseason cannot run. "
            + OFFSEASON_ERROR)}), 503
    body = _body()
    if body.get("force"):
        return jsonify({"ok": False, "error": "A season transition cannot be forced. Reconcile an interrupted run first."}), 409
    if not body.get("confirm"):
        return jsonify({"ok": False, "error": (
            "The offseason writes to all three saves and pays every character. Send "
            '{"confirm": true} to mean it.')}), 400
    season = _season_arg(body.get("season"))
    if season is False:
        return jsonify({"ok": False, "error": "season must be a year, e.g. 2047"}), 400
    # THE POSTSEASON GATE, and it sits here on purpose - AFTER the cheap validation above, so a
    # malformed request is rejected without paying for three playoffs.htm parses, and after the
    # season is known, so it is checked against the season actually asked for.
    #
    # It is only on the REAL run. The PREVIEW is deliberately left through: it writes nothing,
    # and it is exactly what somebody should be able to run to find out they are not ready.
    #
    # IT FAILS CLOSED. The first version set `rows = []` on any exception and tested
    # `if rows and not ready(rows)` - but `all([])` is True, so a readiness check that itself
    # threw let the request straight through to the one irreversible path in the app, answering
    # 202 with nothing but a log line behind it. A gate that opens when it breaks is worse than
    # no gate, because it is trusted. `run_offseason` raises in the same situation.
    if not SIMWEEK_OK:
        return jsonify({"ok": False, "error": (
            "The readiness gate is unavailable because simweek did not import, so the offseason "
            "cannot be checked - and it will not run unchecked. " + SIMWEEK_ERROR)}), 503
    try:
        rows = readiness.check("offseason", season=season, store=offseason_store())
    except Exception as exc:                                            # noqa: BLE001
        app.logger.exception("readiness check failed")
        return jsonify({"ok": False, "refused": True, "error": (
            f"The universe could not be checked for readiness ({type(exc).__name__}: {exc}), "
            "so the rollover was refused rather than run unchecked.")}), 409
    if not readiness.ready(rows):
        # `busy` and `run` are carried so the panel can still re-attach to a live run's log;
        # without them app.js drops the stream for the whole of any running sim.
        existing = busy_run()
        return jsonify({"ok": False, "refused": True, "error": (
            "The universe is not ready to roll over: " + readiness.refusal(rows)),
            "busy": bool(existing and existing.finished_at is None),
            "run": existing.summary() if existing else None,
            "readiness": [r.__dict__ for r in rows]}), 409

    # A season the store has not reached yet is always a typo, and it is a dangerous one: the
    # already-run guard only refuses seasons that *have* run, so "2407" would sail past it,
    # age everybody and leave current_season at 2408. The store decides what is next; this can
    # only ever be that season or an earlier one (which is what force is for).
    current = offseason_plan().get("season")
    if season is not None and current is not None and season > int(current):
        return jsonify({"ok": False, "error": (
            f"The store's current season is {current}, so {season} cannot be run. The offseason "
            "runs the season the store is on, or - with force - one it has already done.")}), 400
    force = bool(body.get("force"))

    run, refusal = start_offseason(season=season, force=force)
    if run is None:
        existing = busy_run()
        return jsonify({"ok": False, "error": refusal, "busy": True,
                        "run": existing.summary() if existing else None}), 409
    return jsonify({"ok": True, "run": run.summary()}), 202


@app.get("/api/offseason/result")
@api
def api_offseason_result():
    """The most recent offseason run of this process: its summary, its result, its refusal.

    Separate from `/api/sim/log` because a sim started afterwards replaces `current_run()`, and
    the offseason panel still has to show what the offseason did.
    """
    wanted = request.args.get("run") or ""
    with _STATE_LOCK:
        runs = [r for r in _HISTORY_HINT if r.kind == "offseason"]
    run = next((r for r in runs if r.id == wanted), None) if wanted else (runs[0] if runs else None)
    if run is None:
        saved = saved_result() if OFFSEASON_OK and not wanted else None
        if saved:
            return jsonify({"ok": True, "run": {"status": "ok", "season": saved.get("season"),
                            "kind": "offseason", "archived": True}, "result": _offseason_view(saved),
                            "refused": False, "error": ""})
        return jsonify({"ok": True, "run": None, "result": None, "refused": False, "error": "",
                        "note": "No offseason report has been saved yet."})
    return jsonify({"ok": run.status in ("ok", "running"), "run": run.summary(),
                    "result": run.result, "refused": run.refused, "error": run.error})


def _season_arg(raw):
    """None (use the store's season), an int, or False meaning "that is not a year"."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return False


# Feats read existing exports only. Hold the sim lock briefly to avoid reading a half-publish.
_FEATS_PREVIEWS = {}


@app.post("/api/feats/scan")
@api
def api_feats_scan():
    from . import feats, settings
    if not _SIM_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Wait for the simulation to finish before scanning feats."}), 409
    try:
        settings.reload()
        result = feats.scan(_body().get("scope", "last"))
        token = uuid.uuid4().hex
        _FEATS_PREVIEWS.clear()
        _FEATS_PREVIEWS[token] = result["events"]
        return jsonify({"ok": True, "token": token, **result})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    finally:
        _SIM_LOCK.release()


@app.post("/api/feats/send")
@api
def api_feats_send():
    from . import feats
    events = _FEATS_PREVIEWS.get(_body().get("token"))
    if events is None:
        return jsonify({"ok": False, "error": "Scan again before posting; this preview has expired."}), 400
    try:
        count = feats.send(events)
        return jsonify({"ok": True, "sent": count})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


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
    result = approve(ids)
    # `approve` returns a count from the local store and a list of the updated rows from
    # Supabase, so the panel normalises rather than printing "Approved [object Object]".
    count = len(result) if isinstance(result, (list, tuple)) else result
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


@app.get("/site/<path:relpath>")
def serve_site(relpath):
    """Serve the published league pages read-only, so the cards' links actually open.

    The same folder GitHub Pages serves. `send_from_directory` refuses to escape the root, and
    nothing here ever writes - publishing is `simweek`'s job.
    """
    return send_from_directory(SITE, relpath)


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
    ap.add_argument("--lan", action="store_true",
                    help="serve on the local network so the panel can be opened from another "
                         "machine (the point of running the game on a server). No password: "
                         "anything on your network can press Sim Week.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interface to bind (default 127.0.0.1; see the note in the source)")
    args = ap.parse_args(argv)

    print(f"Cheezeyverse commissioner  ->  http://{args.host}:{args.port}")
    if not SIMWEEK_OK:
        print(f"  ! commissioner.simweek is unavailable: {SIMWEEK_ERROR}")
        print("    The panel will start and explain itself, but nothing can touch a save.")
    if not OFFSEASON_OK:
        print(f"  ! commissioner.offseason is unavailable: {OFFSEASON_ERROR}")
        print("    The offseason panel will say so; everything else still works.")

    # Loopback by default. This panel has no authentication of any kind and every button on it
    # can rewrite a save file, so the narrow bind is the only thing standing between the LAN and
    # the offseason.
    #
    # --lan widens it on purpose, because the game now runs on a headless server and the whole
    # point is pressing Sim Week from another machine. That is the owner's decision, made with
    # the trade-off stated: on a home network, with no password, anything that can reach port
    # 5095 can sim a week or roll the season over. It is off unless asked for, and it announces
    # itself when used so it can never be on by accident.
    #
    # use_reloader=False, deliberately: the reloader runs a second process, which would mean a
    # second `_SIM_LOCK` - and "only one sim at a time, ever" would quietly stop being true.
    # debug is off for the same reason (it implies the reloader) and because a debugger console
    # on an unauthenticated port is a remote shell.
    host = "0.0.0.0" if args.lan else args.host
    if args.lan:
        import socket
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))          # no packet is sent; this just picks the route
            mine = probe.getsockname()[0]
            probe.close()
        except OSError:
            mine = socket.gethostbyname(socket.gethostname())
        print()
        print("  SERVING ON THE LOCAL NETWORK, WITH NO PASSWORD.")
        print(f"  From another machine on this network:  http://{mine}:{args.port}")
        print("  Anything that can reach that address can press Sim Week or run the offseason.")
        print()
    app.run(host=host, port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
