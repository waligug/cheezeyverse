"""One Discord card per sim, edited by a coalescing background worker.

The simulation only updates memory. Discord latency, rate limits and outages never sit in
the game loop. A POST with an uncertain outcome is not repeated: that could create two cards.
Once the message ID is known, PATCH is safe to retry with the newest state.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from . import notify

INTERVAL = 5.0
TIMEOUT = (3, 5)
FINAL_BUDGET = 45.0
LEAGUES = {"prep": "Prep", "college": "College", "pro": "Pro"}
STAGES = {
    "prepare": "Preparing rosters and upgrades", "load": "Loading the league",
    "sim": "Playing the games", "save": "Saving results", "export": "Exporting league pages",
    "mdb": "Exporting game-by-game stats", "tidy": "Checking rosters",
    "snapshot": "Recording player progress", "publish": "Preparing the website",
    "upload": "Publishing the website", "points": "Awarding weekly points",
    "finish": "Finishing the run",
    # The offseason's own phases. It is the longest single thing this panel does - the first
    # real one took over twenty minutes - and until now it posted "Offseason started", went
    # silent for all of it, and then posted the report. A progress card is the difference
    # between "it is working" and "is it stuck?", and that question has been asked out loud.
    "archive": "Archiving the finished season", "grow": "Growing everybody a year",
    "bonus": "Working out season bonuses", "move": "Promotions and the draft",
    "ageout": "Moving the league's old players on",
    "rollover": "Rolling the game's own season over",
    "report": "Writing it up",
}


class RunProgress:
    """Progress through completed tasks and simulated days, never through elapsed time."""

    def __init__(self, leagues, days):
        day_counts = days if isinstance(days, dict) else {}
        self.days = max(1, max(day_counts.values()) if day_counts else int(days))
        keys = list(leagues)
        # Roster protection is real work: on an aged universe it can take two minutes per
        # league while it releases dozens of AI signings and restores reserved players. Giving
        # it weight 1 made a three-league calendar run display 0% for six minutes because the
        # simulated-day weights dwarf it and integer rounding erased every preparation step.
        plan = [("prepare", key, 12) for key in keys]
        for key in keys:
            plan.extend((stage, key, day_counts.get(key, self.days) if stage == "sim" else 1)
                        for stage in ("load", "sim", "save", "export", "mdb"))
        plan.extend((stage, key, 1) for stage in ("tidy", "snapshot") for key in keys)
        plan.extend((("publish", None, 1), ("upload", None, 1)))
        plan.extend(("points", key, 1) for key in keys)
        plan.append(("finish", None, 1))
        self.offsets, offset = {}, 0
        for stage, key, weight in plan:
            self.offsets[stage, key] = (offset, weight)
            offset += weight
        self.total, self.percent = offset, 0

    def at(self, stage, league=None, fraction=0):
        entry = self.offsets.get((stage, league))
        if entry is None:
            return self.percent  # An unrecognized display stage must never stop a sim.
        offset, weight = entry
        value = 100 * (offset + weight * max(0, min(1, fraction))) / self.total
        self.percent = max(self.percent, min(99, int(value)))
        return self.percent


def render(state, *, days, leagues, elapsed, preview=False, kind="sim", label=None):
    """A Discord embed matching the sketch: title, segmented bar, current work underneath.

    `kind` only changes the words. An offseason is not a sim - it plays no games and its "Run"
    is a pair of seasons rather than a number of days - but it is the same shape of thing to
    watch, so it is the same card rather than a second one to keep in step.
    """
    terminal = state.get("terminal", False)
    ok = state.get("ok", False)
    percent = max(0, min(100 if terminal and ok else 99, int(state.get("percent", 0))))
    filled = min(16, int(percent * 16 / 100))
    bar = "🟨" * filled + "⬛" * (16 - filled)
    if terminal and ok:
        bar = "🟩" * 16
    noun = "Offseason" if kind == "offseason" else "Sim"
    title = ((f"✅ {noun} complete" if ok else f"⚠️ {noun} stopped") if terminal
             else f"🏀 {noun} in progress")
    if preview:
        title = "Preview · " + title
    detail = str(state.get("detail") or "Preparing to start…")[:3000]
    stage = STAGES.get(state.get("stage"), "Starting the sim")
    seconds = max(0, int(elapsed))
    duration = f"{seconds // 60}m {seconds % 60:02d}s"
    league = LEAGUES.get(state.get("league"), state.get("league"))
    return {
        "content": "",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title,
            "description": f"**{percent}%**\n{bar}\n\n**{stage if not terminal else 'Result'}**\n{detail}",
            "color": (0x4CAF50 if ok else 0xE05A47) if terminal else 0xF2B705,
            "fields": [
                {"name": "League", "value": str(league or "All selected leagues"), "inline": True},
                {"name": "Elapsed", "value": duration, "inline": True},
                {"name": "Run", "value": (label if label else
                    (" · ".join(f"{k}: {v}d" for k, v in days.items()) if isinstance(days, dict)
                     else f"{days} days · {len(leagues)} league(s)")), "inline": True},
            ],
            "footer": {"text": ("Preview only — no games advanced" if preview else
                                  "Cheezeyverse · Finished" if terminal else
                                  "Cheezeyverse · Live updates every 5 seconds")},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }


class RateLimited(Exception):
    def __init__(self, delay):
        self.delay = delay


class Refused(Exception):
    def __init__(self, status):
        self.status = status


class WebhookMessage:
    """The URL remains private; responses expose only a message ID and channel ID."""

    def __init__(self, target, session=None):
        self.target = target
        self.session = session or requests.Session()
        self.message_id = None
        self.channel_id = None

    def endpoint(self):
        parts = urlsplit(self.target)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "wait"]
        path = parts.path.rstrip("/")
        if self.message_id:
            path += "/messages/" + self.message_id
        else:
            query.append(("wait", "true"))
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))

    def send(self, payload):
        response = self.session.request("PATCH" if self.message_id else "POST", self.endpoint(),
                                        json=payload, timeout=TIMEOUT)
        try:
            if response.status_code == 429:
                try:
                    delay = float(response.json().get("retry_after", response.headers.get("Retry-After", 5)))
                except (ValueError, TypeError, AttributeError):
                    delay = 5.0
                raise RateLimited(delay if math.isfinite(delay) and delay > 0 else 5.0)
            if not 200 <= response.status_code < 300:
                raise Refused(response.status_code)
            if not self.message_id:
                data = response.json()
                message_id = str(data.get("id", ""))
                if not message_id.isdigit():
                    raise ValueError("Discord returned no message ID")
                self.message_id = message_id
                self.channel_id = str(data.get("channel_id", ""))
        finally:
            response.close()

    def close(self):
        self.session.close()


class SimStatus:
    def __init__(self, days, leagues, *, log=print, preview=False, transport=None,
                 interval=INTERVAL, kind="sim", label=None):
        self.days, self.leagues = days, list(leagues)
        self.kind, self.label = kind, label
        self.log, self.preview = log, preview
        self.interval = max(0.01, interval)
        self.transport = transport
        self.started = time.monotonic()
        self._lock, self._wake = threading.Lock(), threading.Event()
        self._state = {"percent": 0, "stage": "start", "detail": "Preparing to start…"}
        self._thread = None

    def _log(self, message):
        try:
            self.log(message)
        except Exception:
            pass

    def start(self):
        try:
            if self._thread is not None:
                return self
            if self.transport is None:
                target = notify.url()
                if not target:
                    return self
                self.transport = WebhookMessage(target)
            self._thread = threading.Thread(target=self._run, name="discord-sim-status", daemon=True)
            self._thread.start()
        except Exception as exc:
            self._log(f"Discord status could not start ({type(exc).__name__}); sim continues")
        return self

    def update(self, *, percent=None, stage=None, detail=None, league=None):
        with self._lock:
            if self._state.get("terminal"):
                return
            if percent is not None:
                self._state["percent"] = max(self._state["percent"], min(99, int(percent)))
            if stage is not None:
                self._state["stage"] = stage
            if detail is not None:
                self._state["detail"] = str(detail)
            self._state["league"] = league
        self._wake.set()

    def finish(self, ok, detail):
        with self._lock:
            if self._state.get("terminal"):
                return
            self._state.update(terminal=True, ok=ok, detail=str(detail), league=None,
                               ended=time.monotonic())
            if ok:
                self._state["percent"] = 100
        self._wake.set()

    def wait(self, timeout=None):
        """Join the worker, for previews, tests, and the end of a run.

        The rule this module protects is that Discord never sits in the GAME LOOP - no click,
        save or publish waits on an HTTP round trip. Waiting once at the very end, after every
        result is already recorded, is a different thing: `finish()` only flips the card to
        terminal and wakes this worker, and the worker is a daemon thread, so a caller that
        exits straight after loses the final PATCH and strands the card at 99% for ever.
        """
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def _run(self):
        next_send, failures = 0.0, 0
        try:
            while True:
                with self._lock:
                    state = dict(self._state)
                now = time.monotonic()
                if state.get("terminal") and now - state["ended"] > FINAL_BUDGET:
                    self._log("Discord final update timed out; the sim result is saved in the panel")
                    return
                if now < next_send:
                    self._wake.wait(min(next_send - now, 1.0))
                    self._wake.clear()
                    continue
                payload = render(state, days=self.days, leagues=self.leagues,
                                 elapsed=state.get("ended", now) - self.started,
                                 preview=self.preview, kind=self.kind, label=self.label)
                try:
                    self.transport.send(payload)
                except RateLimited as exc:
                    next_send = time.monotonic() + max(self.interval, exc.delay)
                    continue
                except Exception as exc:
                    # A failed POST may already have created a message: never duplicate it.
                    # A deleted card or webhook is not an invitation to post another one.
                    permanent = isinstance(exc, Refused) and 400 <= exc.status < 500
                    failures += 1
                    if not self.transport.message_id or permanent or failures >= 4:
                        label = f"HTTP {exc.status}" if isinstance(exc, Refused) else type(exc).__name__
                        self._log(f"Discord status unavailable ({label}); sim continues")
                        return
                    next_send = time.monotonic() + max(self.interval, 2 ** failures)
                    continue
                failures = 0
                if state.get("terminal"):
                    return
                next_send = time.monotonic() + self.interval
        except Exception as exc:
            self._log(f"Discord status stopped ({type(exc).__name__}); sim continues")
        finally:
            try:
                self.transport.close()
            except Exception:
                pass
