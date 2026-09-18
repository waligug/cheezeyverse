"""A file-backed stand-in for Supabase, so the whole commissioner loop runs before any account exists.

Same call signatures as `commissioner/store.py`. `simweek` picks whichever is configured, which
means the pipeline can be built, run and debugged today and swapped to the real thing by setting
two environment variables - no code path changes, so the version that gets tested is the version
that ships.

Everything lives in one JSON file. That is fine for a few dozen characters and an owner clicking a
button; it is not a database and does not pretend to be one.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "universe" / "local_store.json"
_LOCK = threading.Lock()

DEFAULTS = {
    "max_characters": 2,
    "auto_approve": True,     # no queue to babysit while it is just the owner
    "current_season": 2030,
    "current_week": 0,
    "points_per_week": 1,
    # A season is about 26 in-game weeks, so this is roughly half a season on top,
    # paid at the rollover. It is what stops a young character feeling becalmed.
    "offseason_points": 15,
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank():
    return {"characters": [], "requests": [], "ledger": [], "runs": [], "settings": dict(DEFAULTS)}


def _read():
    if not PATH.exists():
        return _blank()
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank()
    base = _blank()
    base.update(data)
    base["settings"] = {**DEFAULTS, **data.get("settings", {})}
    return base


def _write(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def is_configured():
    return True


def kind():
    return "local"


# ---- settings --------------------------------------------------------------------------------
def get_settings():
    return _read()["settings"]


def set_setting(key, value):
    with _LOCK:
        d = _read()
        d["settings"][key] = value
        _write(d)
    return value


# ---- characters ------------------------------------------------------------------------------
def characters(league=None, status=None):
    rows = _read()["characters"]
    if league:
        rows = [c for c in rows if c.get("league") == league]
    if status:
        rows = [c for c in rows if c.get("status") == status]
    return rows


def add_character(payload):
    with _LOCK:
        d = _read()
        row = dict(payload)
        row.setdefault("id", uuid.uuid4().hex)
        row.setdefault("status", "pending")
        row.setdefault("points_available", 0)
        row.setdefault("points_spent", 0)
        row.setdefault("created_at", _now())
        d["characters"].append(row)
        _write(d)
    return row


def pending_characters():
    return characters(status="pending")


def activate_character(character_id, league, team_abbrev, claimed_slot, game_dob):
    with _LOCK:
        d = _read()
        for c in d["characters"]:
            if c["id"] == character_id:
                c.update(status="active", league=league, team_abbrev=team_abbrev,
                         claimed_slot=claimed_slot, game_dob=game_dob, activated_at=_now())
                _write(d)
                return c
    raise KeyError(character_id)


def set_character_status(character_id, status):
    with _LOCK:
        d = _read()
        for c in d["characters"]:
            if c["id"] == character_id:
                c["status"] = status
                _write(d)
                return c
    raise KeyError(character_id)


# ---- upgrade requests ------------------------------------------------------------------------
def add_request(character_id, rating, delta, kind_="rating", cost=None, note=""):
    with _LOCK:
        d = _read()
        row = {"id": uuid.uuid4().hex, "character_id": character_id, "rating": rating,
               "delta": int(delta), "kind": kind_, "cost": int(cost if cost is not None else delta),
               "status": "approved" if d["settings"].get("auto_approve") else "pending",
               "requested_at": _now(), "applied_at": None, "note": note}
        d["requests"].append(row)
        _write(d)
    return row


def pending_requests(league=None):
    d = _read()
    by_id = {c["id"]: c for c in d["characters"]}
    out = []
    for r in d["requests"]:
        if r["status"] != "approved":
            continue
        ch = by_id.get(r["character_id"])
        if not ch or (league and ch.get("league") != league):
            continue
        out.append({**r, "character": ch})
    return out


def queued_requests():
    """Requests still waiting on the owner."""
    d = _read()
    by_id = {c["id"]: c for c in d["characters"]}
    return [{**r, "character": by_id.get(r["character_id"])}
            for r in d["requests"] if r["status"] == "pending"]


def approve_requests(request_ids):
    ids = set(request_ids)
    with _LOCK:
        d = _read()
        n = 0
        for r in d["requests"]:
            if r["id"] in ids and r["status"] == "pending":
                r["status"] = "approved"
                n += 1
        _write(d)
    return n


def reject_request(request_id, note=""):
    with _LOCK:
        d = _read()
        for r in d["requests"]:
            if r["id"] == request_id:
                r.update(status="rejected", note=note)
        _write(d)


def mark_applied(request_ids):
    ids = set(request_ids)
    with _LOCK:
        d = _read()
        for r in d["requests"]:
            if r["id"] in ids:
                r.update(status="applied", applied_at=_now())
        _write(d)
    return len(ids)


# ---- points ----------------------------------------------------------------------------------
def grant_points(character_id, amount, reason="week simmed"):
    with _LOCK:
        d = _read()
        for c in d["characters"]:
            if c["id"] == character_id:
                c["points_available"] = c.get("points_available", 0) + int(amount)
        d["ledger"].append({"id": uuid.uuid4().hex, "character_id": character_id,
                            "amount": int(amount), "reason": reason, "created_at": _now()})
        _write(d)


def grant_week_points(league=None, weeks=1, reason="week simmed"):
    per = get_settings().get("points_per_week", 1)
    granted = 0
    for c in characters(league=league, status="active"):
        grant_points(c["id"], per * weeks, reason)
        granted += 1
    return granted


# ---- run history -----------------------------------------------------------------------------
def record_run(row):
    with _LOCK:
        d = _read()
        d["runs"].insert(0, {**row, "at": _now()})
        d["runs"] = d["runs"][:100]
        _write(d)


def runs(limit=20):
    return _read()["runs"][:limit]
