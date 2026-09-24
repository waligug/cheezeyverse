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

from . import points
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "universe" / "local_store.json"
_LOCK = threading.Lock()

DEFAULTS = {
    "max_characters": 3,
    "auto_approve": True,     # no queue to babysit while it is just the owner
    "current_season": 2026,
    "current_week": 0,
    "points_per_week": 1,
    # Income scales with level because the cost curve does: a step costs 1 under 50, 2 from
    # 50-69 and 3 from 70-84, and characters sit in those bands at prep, college and pro. A
    # flat rate means every promotion quietly halves what a season buys. See points.py.
    "points_per_week_prep": 1,
    "points_per_week_college": 2,
    "points_per_week_pro": 3,
    # A season is about 26 in-game weeks, so this is roughly half a season on top,
    # paid at the rollover. It is what stops a young character feeling becalmed.
    "offseason_points": 5,
    # Paid on top of the lump sum for a college season seen through; it is what makes
    # staying a real alternative to declaring the moment you are allowed to.
    "college_development_bonus": 5,
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank():
    return {"characters": [], "requests": [], "ledger": [], "runs": [], "snapshots": [],
            "settings": dict(DEFAULTS)}


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
        row.setdefault("points_reserved", 0)
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


def set_character_field(character_id, field, value):
    """Set one column on a character. The offseason uses it to bank college years."""
    with _LOCK:
        d = _read()
        for c in d["characters"]:
            if c["id"] == character_id:
                c[field] = value
                _write(d)
                return c
    raise KeyError(character_id)


def record_level(character_id, entry):
    """Append one level to a character's history, and remember his id in that league's save.

    The career page needs this: each league's site knows a player only inside that league, so the
    thread between Prep, College and Pro is ours to keep. `player_id` is what makes a direct link
    to `leagues/<level>/players/player<id>.htm` possible.
    """
    with _LOCK:
        d = _read()
        for c in d["characters"]:
            if c["id"] == character_id:
                history = list(c.get("level_history") or [])
                for row in history:                      # close the level he is leaving
                    if row.get("to_season") is None and row.get("level") != entry.get("level"):
                        row["to_season"] = entry.get("from_season")
                        row["how_it_ended"] = entry.get("how_it_started")
                history.append(entry)
                c["level_history"] = history
                ids = dict(c.get("league_player_ids") or {})
                if entry.get("player_id") is not None:
                    ids[entry["level"]] = entry["player_id"]
                c["league_player_ids"] = ids
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


def retire_character(character_id, season, reason, release_slot=True):
    """End a career, and say when and why in the same write.

    `release_slot` is the whole of the slot bookkeeping: **a character holds his reserve row
    for exactly as long as `claimed_slot` is set on him.** The offseason clears it only after
    `offseason.refill` has really handed the row back its manifest name. When FBPB3 has
    deleted the record itself there is no row to hand back, so the claim stays and nobody is
    offered a slot that no longer exists.
    """
    with _LOCK:
        d = _read()
        for c in d["characters"]:
            if c["id"] == character_id:
                c.update(status="retired", retired_season=int(season),
                         retired_reason=reason, retired_at=_now())
                if release_slot:
                    c["claimed_slot"] = None
                _write(d)
                return c
    raise KeyError(character_id)


# ---- upgrade requests ------------------------------------------------------------------------
class NotEnoughPoints(ValueError):
    pass


class BadRequest(ValueError):
    pass


# The cost curve, mirroring COST_BANDS in site/js/rules.js. It lives here as well because a price
# that arrives from a browser is a claim, not a fact: a request carrying cost=-50 minted points and
# cost=0 bought free upgrades. The client's number is now ignored entirely and this is the price.
COST_BANDS = ((50, 1), (70, 2), (85, 3), (10 ** 9, 5))
POTENTIAL_MULTIPLIER = 2
LOCKED_RATINGS = ("Fouling",)


def step_cost(value):
    for ceiling, price in COST_BANDS:
        if value < ceiling:
            return price
    return COST_BANDS[-1][1]


def upgrade_cost(current, delta, kind="rating"):
    """What it really costs to move `current` up by `delta`, priced step by step."""
    total = 0
    value = int(current)
    for _ in range(int(delta)):
        total += step_cost(value) * (POTENTIAL_MULTIPLIER if kind == "potential" else 1)
        value += 1
    return total


def add_request(character_id, rating, delta, kind_="rating", cost=None, note=""):
    """Queue an upgrade, RESERVING its cost immediately.

    Reserving at request time rather than at apply time is the whole point: a week's worth of
    requests is queued before any of it is written to the save, so charging on apply would let
    somebody queue ten times what he has and have it all land. Reserved points are returned if
    the request is rejected or cancelled, and become `points_spent` once it is applied.
    """
    delta = int(delta)
    if delta <= 0:
        raise BadRequest("an upgrade has to be at least +1")
    if rating in LOCKED_RATINGS:
        raise BadRequest(f"{rating} cannot be spent on")
    with _LOCK:
        d = _read()
        character = next((c for c in d["characters"] if c["id"] == character_id), None)
        if character is None:
            raise KeyError(character_id)

        # Price it here, from his real ratings plus whatever is already queued for the same
        # rating, and ignore whatever `cost` the caller passed.
        # Price a potential off the POTENTIAL, not off the rating underneath it. Both are keyed
        # by the rating name, so reading the wrong one is easy and silent - and since a potential
        # always sits above its rating, it made every ceiling cheaper than the curve says. The
        # database trigger already reads ch.potentials for this; the two have to agree.
        book = "potentials" if kind_ == "potential" else "ratings"
        current = int((character.get(book) or {}).get(rating, 0))
        for queued in d["requests"]:
            if (queued["character_id"] == character_id and queued["rating"] == rating
                    and queued.get("kind", "rating") == kind_
                    and queued["status"] in ("pending", "approved")):
                current += int(queued["delta"])
        price = upgrade_cost(current, delta, kind_)
        available = int(character.get("points_available", 0))
        if price > available:
            raise NotEnoughPoints(
                f'{character["first_name"]} {character["last_name"]} has {available} point(s) '
                f"and that costs {price}")
        character["points_available"] = available - price
        character["points_reserved"] = int(character.get("points_reserved", 0)) + price
        row = {"id": uuid.uuid4().hex, "character_id": character_id, "rating": rating,
               "delta": int(delta), "kind": kind_, "cost": price,
               "status": "approved" if d["settings"].get("auto_approve") else "pending",
               "requested_at": _now(), "applied_at": None, "note": note}
        d["requests"].append(row)
        _write(d)
    return row


def _return_points(d, request):
    """Hand a reserved cost back, for a request that will never be applied."""
    for c in d["characters"]:
        if c["id"] == request["character_id"]:
            c["points_reserved"] = max(0, int(c.get("points_reserved", 0)) - int(request["cost"]))
            c["points_available"] = int(c.get("points_available", 0)) + int(request["cost"])
            return


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
            if r["id"] == request_id and r["status"] in ("pending", "approved"):
                _return_points(d, r)
                r.update(status="rejected", note=note)
        _write(d)


def mark_applied(request_ids):
    """Reserved points become spent points. They do not come back."""
    ids = set(request_ids)
    with _LOCK:
        d = _read()
        for r in d["requests"]:
            if r["id"] in ids and r["status"] != "applied":
                for c in d["characters"]:
                    if c["id"] == r["character_id"]:
                        c["points_reserved"] = max(
                            0, int(c.get("points_reserved", 0)) - int(r["cost"]))
                        c["points_spent"] = int(c.get("points_spent", 0)) + int(r["cost"])
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
    """The weekly payout, at the rate this level pays. Mirrors the Supabase path exactly.

    The rate comes from `points.per_week` rather than being read here, so the two stores cannot
    drift: test_store_parity checks the signatures match, not the arithmetic behind them, and a
    flat rate here against a level rate there would pay different amounts with nothing failing.
    """
    per = points.per_week(league, get_settings())
    line = points.reason(league, per, reason)
    granted = 0
    for c in characters(league=league, status="active"):
        grant_points(c["id"], per * weeks, line)
        granted += 1
    return granted


# ---- rating history --------------------------------------------------------------------------
def add_snapshot(character_id, season, week, ratings, potentials,
                 height_inches=None, league=None):
    """Write down one character's sheet as league.dat held it this week.

    The characters row carries a single live sheet that every Sim Week overwrites, so without
    this nothing in the universe can say what anybody used to be. One row per character per
    run, not one per rating.
    """
    with _LOCK:
        d = _read()
        row = {"id": uuid.uuid4().hex, "character_id": character_id,
               "season": int(season), "week": int(week),
               "ratings": dict(ratings or {}), "potentials": dict(potentials or {}),
               "height_inches": height_inches, "league": league, "taken_at": _now()}
        d.setdefault("snapshots", []).append(row)
        _write(d)
    return row


def snapshots(character_id=None, league=None):
    """Every recorded sheet, oldest first. Filter by character for one career's line."""
    rows = _read().get("snapshots", [])
    if character_id:
        rows = [r for r in rows if r.get("character_id") == character_id]
    if league:
        rows = [r for r in rows if r.get("league") == league]
    return sorted(rows, key=lambda r: (r.get("season", 0), r.get("week", 0)))


# ---- run history -----------------------------------------------------------------------------
def record_run(row):
    """Log one sim run. Mirrors store.record_run, including what it refuses to throw away.

    A run that actually simmed is never trimmed: head-to-head derives each character's debut by
    summing days simmed before he existed, so dropping one silently hands a filler's games to
    somebody. Refused and dry attempts are trimmed instead - they advanced nothing.
    """
    with _LOCK:
        d = _read()
        entry = {**row, "at": _now()}
        entry.setdefault("dry_run", False)
        entry.setdefault("ok", False)
        log = [entry] + d["runs"]
        real = [r for r in log if r.get("ok") and not r.get("dry_run")]
        rest = [r for r in log if not (r.get("ok") and not r.get("dry_run"))]
        keep = real + rest[:max(0, 100 - len(real))]
        keep.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        d["runs"] = keep
        _write(d)
        return entry


def runs(limit=20):
    """The run log, newest first. limit=None returns all of it - see store.runs()."""
    rows = _read()["runs"]
    return rows if limit is None else rows[:limit]
