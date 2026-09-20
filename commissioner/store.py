"""Supabase access for the local commissioner app, over plain HTTPS.

Deliberately thin: PostgREST is a REST API, `requests` is already a dependency of nothing
in particular but ships with the environment, and a full SDK would be a lot of weight for
eight calls. Everything here uses the **service role key**, which bypasses Row Level
Security - so this module must never be reachable from the public site.

Nothing raises on import. If `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` are not set, the
first call that needs them raises `StoreNotConfigured` and the rest of the app carries on
offline. See `commissioner/settings.py` and `.env.example`.

    python -m commissioner.store --selftest     # prints every request it would make
    python -m commissioner.store --settings     # live: dump the settings table

The RPC names below (`grant_points`, `apply_upgrade_requests`, `activate_character`) are
defined in `supabase/schema.sql`. Change one and you change both files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import requests

from datetime import datetime, timezone
from pathlib import Path

from . import points
from . import settings as cfg

__all__ = [
    "StoreNotConfigured", "StoreError",
    "pending_requests", "mark_applied", "pending_characters", "activate_character",
    "retire_character", "grant_points", "grant_week_points", "get_settings", "set_setting",
    "add_snapshot", "snapshots", "characters_for_export", "characters", "add_character", "set_character_field", "record_level", "queued_requests", "record_run", "runs", "kind",
]

# The character columns the public career pages need. Kept explicit rather than `*` so a
# new column added to the table does not silently start leaking into the export.
CHARACTER_COLUMNS = (
    "id,owner,first_name,last_name,position,height_inches,weight_lbs,archetype,league,team_abbrev,"
    "status,game_dob,ratings,potentials,points_available,points_spent,claimed_slot,"
    # league_player_ids is how anything outside the codec finds a character in the
    # generated league site: it maps each level to his FBPB3 player id. Leaving it out
    # of the select made every caller see None and conclude he had never been placed.
    # declared and college_years decide whether the offseason moves somebody up or into
    # the draft. Omitting them made every caller read None and conclude "not declared,
    # no college years" - which is indistinguishable from the real thing and is how a
    # write was three times believed not to have landed when it had.
    "league_player_ids,level_history,declared,college_years,"
    # traits carries height_genes, which apply_growth needs. Without it every character
    # read None, growth skipped all of them, and the offseason reported "0 grew" - so
    # nobody would ever have got taller, in a game whose whole premise is growing up.
    # build is the fallback half of weight: characters made before the slider have a null
    # weight_lbs and stamp_character derives height+build for them. Left out, build reads
    # None, every one of them derives as "solid", and a wiry or heavy kid is quietly handed
    # a weight up to 20 lbs from the one his own page has always shown him.
    "traits,build,created_at"
)

DRY_RUN = False  # set by --selftest; makes every call describe itself instead of firing


class StoreError(RuntimeError):
    """Supabase answered, but with an error."""


class StoreNotConfigured(StoreError):
    """No SUPABASE_URL / SUPABASE_SERVICE_KEY yet. The app is expected to keep running."""

    def __init__(self, detail=""):
        super().__init__(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_KEY in the "
            f"environment or in {cfg.ENV_FILE} (copy .env.example). {detail}".strip()
        )


# ---------------------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------------------

def is_configured():
    """True when a real call would work. Cheap; safe to poll from a Flask status page."""
    return cfg.is_configured()


def _headers(prefer=None):
    key = cfg.supabase_service_key()
    head = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer:
        head["Prefer"] = prefer
    return head


def _request(method, path, params=None, body=None, prefer=None):
    """One PostgREST call. In DRY_RUN, returns a description of it instead."""
    plan = {"method": method, "path": path, "params": params or {}, "body": body, "prefer": prefer}
    if DRY_RUN:
        _print_plan(plan)
        return plan
    if not cfg.is_configured():
        raise StoreNotConfigured(f"Wanted: {method} {path}")
    url = f"{cfg.supabase_url()}{path}"
    try:
        resp = requests.request(method, url, headers=_headers(prefer), params=params,
                                json=body, timeout=cfg.timeout())
    except requests.RequestException as exc:
        raise StoreError(f"{method} {path} failed to reach Supabase: {exc}") from exc
    if resp.status_code >= 400:
        raise StoreError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}")
    if not resp.content or resp.status_code == 204:
        return []
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _print_plan(plan):
    q = "&".join(f"{k}={v}" for k, v in plan["params"].items())
    line = f"  {plan['method']:6} {plan['path']}" + (f"?{q}" if q else "")
    print(line)
    if plan["prefer"]:
        print(f"         Prefer: {plan['prefer']}")
    if plan["body"] is not None:
        print("         body: " + json.dumps(plan["body"], default=str)[:400])


# Columns this code selects that the database may not have yet, and what is lost while it does
# not. A migration is a thing a person pastes into the Supabase SQL editor, so there is always a
# window between the code that wants a column and the column existing - and PostgREST does not
# ignore an unknown column in `select`, it 400s the whole request. Selecting one before it
# exists therefore does not degrade the read, it kills it: every character, for every caller,
# including the panel and a running sim.
PENDING_COLUMNS = {
    "weight_lbs": "the weight he chose in the builder; until supabase/weight_column.sql is run, "
                  "the commissioner derives it from height and build as it always has",
}
_warned_missing = set()


def _table(name, params=None, prefer=None):
    try:
        return _request("GET", f"/rest/v1/{name}", params=params, prefer=prefer)
    except StoreError as exc:
        missing = _missing_column(exc, params)
        if not missing:
            raise
        # Drop it and ask again, once. Only ever a column named in PENDING_COLUMNS: an unknown
        # column that nobody declared as pending is a typo in a select, and a typo that silently
        # answers with the field missing is the exact failure CHARACTER_COLUMNS already cost us
        # five times in a day.
        if missing not in _warned_missing:
            _warned_missing.add(missing)
            print(f"[store] {name}.{missing} does not exist yet - {PENDING_COLUMNS[missing]}")
        thinner = dict(params or {})
        thinner["select"] = _drop_column(thinner.get("select", ""), missing)
        return _request("GET", f"/rest/v1/{name}", params=thinner, prefer=prefer)


def _missing_column(exc, params):
    """The PENDING_COLUMNS name PostgREST just refused, or None."""
    m = re.search(r"column \w+\.(\w+) does not exist", str(exc))
    if not m or "42703" not in str(exc):
        return None
    name = m.group(1)
    return name if name in PENDING_COLUMNS and name in (params or {}).get("select", "") else None


def _drop_column(select, name):
    """`select` without `name`, at the top level and inside any embedded resource."""
    return re.sub(rf"(?<![\w.]){re.escape(name)},|,{re.escape(name)}(?![\w(])", "", select)


def _rpc(name, body):
    return _request("POST", f"/rest/v1/rpc/{name}", body=body)


# ---------------------------------------------------------------------------------------
# upgrade requests
# ---------------------------------------------------------------------------------------

def pending_requests(league=None):
    """Approved-but-not-applied upgrade requests, each with its character embedded.

    These are the ones the Sim Week pipeline hands to the codec. A request the owner has
    filed but the commissioner has not approved yet is status 'pending' and is *not*
    returned - approve it first (in the dashboard, or by turning on the `auto_approve`
    setting so requests skip the queue).
    """
    params = {
        "select": f"*,character:characters({CHARACTER_COLUMNS})",
        "status": "eq.approved",
        "applied_at": "is.null",
        "order": "requested_at.asc",
    }
    if league:
        params["character.league"] = f"eq.{league}"
    rows = _table("upgrade_requests", params)
    if DRY_RUN or not league:
        return rows
    # PostgREST filters on an embedded resource null it out rather than dropping the row
    return [r for r in rows if r.get("character")]


def mark_applied(request_ids):
    """Apply the given approved requests: rating moves, points move, ledger row, stamp.

    One RPC, so the whole batch is a single transaction - a half-applied upgrade cannot
    happen, which matters because the matching edit to `league.dat` is not transactional
    at all. Call this only after the codec write has succeeded.

    Returns the rows that were applied (ids that were not 'approved' are skipped).
    """
    ids = [str(i) for i in (request_ids or [])]
    if not ids:
        return []
    return _rpc("apply_upgrade_requests", {"p_ids": ids})


def reject_request(request_id, note=None):
    """Turn a request down. Its reserved cost is released, nothing is charged."""
    body = {"status": "rejected"}
    if note:
        body["note"] = note
    return _request("PATCH", "/rest/v1/upgrade_requests",
                    params={"id": f"eq.{request_id}"}, body=body,
                    prefer="return=representation")


def approve_requests(request_ids):
    """Move 'pending' requests to 'approved' so the next Sim Week picks them up."""
    ids = [str(i) for i in (request_ids or [])]
    if not ids:
        return []
    return _request("PATCH", "/rest/v1/upgrade_requests",
                    params={"id": f"in.({','.join(ids)})", "status": "eq.pending"},
                    body={"status": "approved"}, prefer="return=representation")


# ---------------------------------------------------------------------------------------
# characters
# ---------------------------------------------------------------------------------------

def pending_characters():
    """Characters waiting for a reserve slot, oldest first, with their owner attached.

    The commissioner picks a free reserve row out of `universe/manifest.json`, has the
    codec rename and re-rate it, then calls `activate_character` with that slot.
    """
    params = {
        "select": f"{CHARACTER_COLUMNS},owner_profile:profiles(id,discord_username,display_name)",
        "status": "eq.pending",
        "order": "created_at.asc",
    }
    return _table("characters", params)


def activate_character(character_id, league, team_abbrev, claimed_slot, game_dob):
    """Record that a character now occupies a real roster slot and switch him on.

    `claimed_slot` is the manifest entry the codec stamped over:
        {"league": "prep", "team": "BKI", "name": "Milo Trask", "dob": "3/14/2016"}
    `game_dob` is the date the save now holds for him (ISO `YYYY-MM-DD`). CONVENTIONS.md
    notes FBPB3 sometimes rewrites a DOB to 12/1/<year> at season rollover, so re-assert
    it after each offseason if the exact birthday matters.
    """
    if league not in ("prep", "college", "pro"):
        raise ValueError(f"league must be prep, college or pro (got {league!r})")
    if hasattr(game_dob, "isoformat"):
        game_dob = game_dob.isoformat()
    return _rpc("activate_character", {
        "p_character": str(character_id),
        "p_league": league,
        "p_team_abbrev": team_abbrev,
        "p_claimed_slot": claimed_slot,
        "p_game_dob": game_dob,
    })


def set_character_status(character_id, status):
    """pending / active / declared / retired. Used when a character graduates or retires."""
    if status not in ("pending", "active", "declared", "retired"):
        raise ValueError(f"unknown status {status!r}")
    return _request("PATCH", "/rest/v1/characters",
                    params={"id": f"eq.{character_id}"}, body={"status": status},
                    prefer="return=representation")


def retire_character(character_id, season, reason, release_slot=True):
    """End a career: status, the season it happened and a one-line reason, in one write.

    `release_slot` is the slot bookkeeping, and it is the same rule the local store keeps: a
    character holds his reserve row for exactly as long as `claimed_slot` is set on him. The
    offseason clears it only once `offseason.refill` has really given the row its manifest
    name back, so when FBPB3 has deleted the record itself the claim stays and nobody is
    handed a slot that no longer exists in the save.
    """
    body = {"status": "retired", "retired_season": int(season), "retired_reason": reason}
    if release_slot:
        body["claimed_slot"] = None
    return _request("PATCH", "/rest/v1/characters",
                    params={"id": f"eq.{character_id}"}, body=body,
                    prefer="return=representation")


# ---------------------------------------------------------------------------------------
# rating history
# ---------------------------------------------------------------------------------------

def add_snapshot(character_id, season, week, ratings, potentials,
                 height_inches=None, league=None):
    """Record one character's sheet as league.dat held it this week.

    `characters.ratings` is a single live sheet the commissioner overwrites every Sim Week,
    so this table is the only thing that can answer what he used to be. One row per
    character per run - the career page reads whole sheets, so a row per rating would be
    eighteen times the writes for the same chart.
    """
    return _request("POST", "/rest/v1/rating_snapshots", body=[{
        "character_id": str(character_id),
        "season": int(season),
        "week": int(week),
        "ratings": dict(ratings or {}),
        "potentials": dict(potentials or {}),
        "height_inches": height_inches,
        "league": league,
    }], prefer="return=representation")


def snapshots(character_id=None, league=None):
    """Recorded sheets, oldest first. Filter by character for one career's line."""
    params = {"select": "*", "order": "season.asc,week.asc"}
    if character_id:
        params["character_id"] = f"eq.{character_id}"
    if league:
        params["league"] = f"eq.{league}"
    return _table("rating_snapshots", params)


def _normalise_dobs(rows):
    """Hand back every character with `game_dob` in the form the SAVE FILE uses.

    game_dob is a Postgres `date`. Whatever string goes in, PostgREST hands back `2015-11-27`,
    while LeagueDat.find() compares the string the save itself builds from its own bytes:
    `11/27/2015`. The two never match.

    Normalising here rather than at each call site is the whole point. This bug was found once
    and fixed at fourteen places, and FOUR more were still wrong a day later - the weekly
    re-dress, the trade sync, the rating snapshot and verify_save - three of them inside
    `except Exception: continue`, so they failed in total silence. Every one of those callers
    got its birthday from this module. Fixing the source means no future caller can get it
    wrong, which a convention spread across twenty call sites can never promise.

    The local JSON store keeps whatever string it was given, which is why the end-to-end test
    passed on the desktop and the bug only ever appeared against Supabase.
    """
    from .characters import codec_dob
    for row in rows or []:
        if isinstance(row, dict) and row.get("game_dob"):
            try:
                row["game_dob"] = codec_dob(row["game_dob"])
            except Exception:
                pass          # an unreadable date is the caller's problem, not a reason to fail
        slot = isinstance(row, dict) and row.get("claimed_slot")
        if isinstance(slot, dict) and slot.get("dob"):
            try:
                slot["dob"] = codec_dob(slot["dob"])
            except Exception:
                pass
    return rows


def characters(league=None, status=None):
    """Every character, with `claimed_slot` - the roster-protection view.

    This is the twin of localstore.characters(), and it was missing. Both callers of it reach
    the store through simweek.store(), which returns THIS module once Supabase is configured,
    so its absence did not raise where anyone would see it:

      * simweek wraps its protect_rosters call in a try/except so a guard failure can never
        abort a sim - the AttributeError was swallowed and roster protection silently never
        ran on any week;
      * tools/protect_rosters.py falls back to `chars = []` on any exception, which is worse
        than not running at all. With no characters, a claimed reserve row answers to a name
        the manifest does not know, so it looks BOTH like a missing reserve and like a
        stranger: it would be defanged to floor ratings and renamed back to its manifest
        identity. That is a deleted character.

    `claimed_slot` is deliberately included here and deliberately excluded from
    characters_for_export(): this view feeds the save file, that one feeds the public site.
    """
    params = {"select": CHARACTER_COLUMNS, "order": "created_at.asc"}
    if league:
        params["league"] = f"eq.{league}"
    if status:
        params["status"] = f"eq.{status}"
    return _normalise_dobs(_table("characters", params))


def add_character(payload):
    """Insert a character. The commissioner's own path; the website inserts as the signed-in user.

    `owner` is required and must be a real profiles.id - the table's foreign key says so, and a
    character with no owner is one nobody can ever sign in and see. The database trigger forces
    `status`, the point columns and the rest of the plumbing whatever we send, so this only has
    to send what a person actually chose.
    """
    row = dict(payload)
    if not row.get("owner"):
        raise StoreError(
            "add_character needs an `owner` (a profiles.id). Sign in on the site once to create "
            "the profile row, or pass the id of an existing one.")
    return _one(_request("POST", "/rest/v1/characters", body=row,
                         prefer="return=representation"))


# Columns the commissioner may set directly. Everything absent from this list is either the
# owner's to choose, or the database's to compute - and a typo'd field name silently does
# NOTHING over PostgREST if it is not validated here, which is the kind of bug that only shows
# up a season later when a draft pick is missing.
SETTABLE_FIELDS = {
    "game_dob", "team_abbrev", "league", "college_years", "declared",
    "draft_round", "draft_pick", "draft_season",
    "retired_season", "retired_reason", "archetype", "height_inches",
    "ratings", "potentials", "league_player_ids", "level_history",
}


def set_character_field(character_id, field, value):
    """Set one column on a character. The offseason banks college years and draft slots here."""
    if field not in SETTABLE_FIELDS:
        raise StoreError(f"{field!r} is not a field the commissioner may set directly; "
                         f"allowed: {', '.join(sorted(SETTABLE_FIELDS))}")
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    return _one(_request("PATCH", "/rest/v1/characters",
                         params={"id": f"eq.{character_id}"}, body={field: value},
                         prefer="return=representation"))


def record_level(character_id, entry):
    """Append one level to a character's history, and remember his id in that league's save.

    The career page needs this: each league's generated site knows a player only inside that
    league, so the thread between Prep, College and Pro is ours to keep. `player_id` is what
    makes a direct link to `leagues/<level>/players/player<id>.htm` possible.

    Read-modify-write on a jsonb column. That is safe here and only here: every caller runs
    inside simweek's _SIM_LOCK, so there is exactly one writer.
    """
    rows = _table("characters", {"select": "id,level_history,league_player_ids",
                                 "id": f"eq.{character_id}"})
    if not rows:
        raise StoreError(f"no character {character_id}")
    current = rows[0]
    history = list(current.get("level_history") or [])
    for row in history:                      # close the level he is leaving
        if row.get("to_season") is None and row.get("level") != entry.get("level"):
            row["to_season"] = entry.get("from_season")
            row["how_it_ended"] = entry.get("how_it_started")
    history.append(entry)
    ids = dict(current.get("league_player_ids") or {})
    if entry.get("player_id") is not None:
        ids[entry["level"]] = entry["player_id"]
    return _one(_request("PATCH", "/rest/v1/characters",
                         params={"id": f"eq.{character_id}"},
                         body={"level_history": history, "league_player_ids": ids},
                         prefer="return=representation"))


def queued_requests():
    """Requests still waiting on the owner to approve them."""
    return _table("upgrade_requests", {
        "select": f"*,character:characters({CHARACTER_COLUMNS})",
        "status": "eq.pending",
        "order": "requested_at.asc",
    })


# ---------------------------------------------------------------------------------------
# the sim log
#
# Deliberately NOT in Supabase. A run row is operational: how long the week took, what the
# driver did, what it emitted. The website never reads one, no player ever sees one, and
# putting it in the database would mean a schema migration before a sim could run at all.
# It lives next to the save files, where the rest of the commissioner's own state lives.
# ---------------------------------------------------------------------------------------

RUNS_FILE = Path(__file__).resolve().parents[1] / "universe" / "sim_runs.json"
RUNS_KEPT = 100


def _read_runs():
    try:
        return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def record_run(row):
    """Log one sim run. Never raises: a full disk must not be able to fail a week that worked.

    THIS FILE IS NOT A LOG, IT IS A LEDGER. Head-to-head works out which games belong to which
    character by summing the days simmed before he existed, so losing a row silently reassigns a
    filler's season to somebody's name - and losing the file entirely makes every character a
    day-one player. Three consequences follow, none of them true of an ordinary log:

      * WRITTEN ATOMICALLY, temp file then replace, the way localstore already did. write_text
        truncates in place, so a crash or a full disk mid-write left a half-written file that
        _read_runs turns into [], which is the total-loss case.
      * A RUN THAT SIMMED IS NEVER DROPPED. RUNS_KEPT trimmed the oldest rows to a hundred, and
        a season simmed in short chunks passes a hundred easily. Refused and dry attempts are
        trimmed instead: they advance nothing, so forgetting them costs nothing.
      * `dry_run` AND `ok` ARE ALWAYS RECORDED, even when the caller omits them, so a reader
        never has to guess what a missing field meant.
    """
    try:
        entry = {**row, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        entry.setdefault("dry_run", False)
        entry.setdefault("ok", False)
        log = [entry] + _read_runs()
        real = [r for r in log if r.get("ok") and not r.get("dry_run")]
        rest = [r for r in log if not (r.get("ok") and not r.get("dry_run"))]
        # every run that moved the calendar, plus whatever recent noise fits around it
        keep = real + rest[:max(0, RUNS_KEPT - len(real))]
        keep.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
        RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RUNS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(keep, indent=1, default=str), encoding="utf-8")
        tmp.replace(RUNS_FILE)
        return entry
    except OSError as exc:
        print(f"could not write the sim log: {exc}")
        return None


def runs(limit=20):
    """The run log, newest first. limit=None returns all of it.

    The default is a display limit. Anything DERIVING from history - head-to-head works out
    each character's debut by summing days simmed before he existed - must pass limit=None, or
    it silently computes against the newest twenty and makes everybody a day-one player.
    """
    rows = _read_runs()
    # `rows[:None]` already returns everything, so limit=None was a no-op dressed as a feature.
    # It stays because it is the CALLER's declaration of intent - anything deriving from history
    # must say so - and because the docstring above is what stops the next person passing 20.
    return rows if limit is None else rows[:limit]


def kind():
    return "supabase"


def _one(result):
    """PostgREST returns a list even for a single-row write."""
    if isinstance(result, list):
        return result[0] if result else None
    return result


def characters_for_export():
    """Everything the public career pages need, in one call.

    Each row is a character plus its owner's display name, its applied upgrades and its
    point ledger, so the site generator can render a career page without a second round
    trip. Pending characters are included (they show as "awaiting a roster spot"); the
    save-file plumbing in `claimed_slot` is not.
    """
    public_cols = CHARACTER_COLUMNS.replace(",claimed_slot", "")
    params = {
        "select": (
            f"{public_cols},"
            "owner_profile:profiles(display_name,discord_username),"
            "upgrades:upgrade_requests(rating,delta,kind,cost,status,requested_at,applied_at),"
            "ledger:point_ledger(amount,reason,created_at)"
        ),
        "order": "created_at.asc",
    }
    return _table("characters", params)


# ---------------------------------------------------------------------------------------
# points
# ---------------------------------------------------------------------------------------

def grant_points(character_id, amount, reason="admin grant"):
    """Write the ledger row and bump `points_available` atomically (one RPC, one txn).

    Negative amounts are allowed and are how a correction is made; the `points_available
    >= 0` check constraint makes an over-withdrawal roll the whole thing back.
    """
    amount = int(amount)
    if amount == 0:
        raise ValueError("amount must be non-zero")
    return _rpc("grant_points", {
        "p_character": str(character_id),
        "p_amount": amount,
        "p_reason": reason,
    })


def grant_week_points(league=None, weeks=1, reason="week simmed"):
    """The weekly payout: one point (settings.points_per_week) to every live character.

    Pass a league to pay only that save's characters - a Sim Week is three separate
    launch/sim/save cycles, so the three leagues are paid as each one finishes.
    Returns the number of characters paid.

    `weeks` exists because run_sim can sim more than seven days at once, and localstore has
    always taken it. This signature did not, so every single Sim Week raised TypeError on the
    Supabase path at the moment it went to pay people. The RPC pays exactly one week and takes
    no count, so call it once per week rather than migrate the schema: the ledger then carries
    one row per week, which reads better than a single lump anyway.
    """
    weeks = max(1, int(weeks or 1))
    rate = points.per_week(league, get_settings())
    line = points.reason(league, rate, reason)
    paid = 0
    for _ in range(weeks):
        paid = _grant_one_week(league, line, rate)
    return paid


def _grant_one_week(league, reason, rate):
    """One week's pay, tolerating a database that has not had level_income.sql run on it yet.

    PostgREST matches an RPC by its ARGUMENT NAMES, so sending p_per to the old two-argument
    grant_week_points is not ignored - it fails to resolve the function at all (PGRST202). A
    deploy that lands before its migration would therefore not pay the flat rate, it would fail
    the points step of every single Sim Week, and the sim would report an error at the very last
    stage after all the basketball had already been played.

    Code and schema cannot be deployed in the same instant, so the code has to survive the gap.
    Fall back, say so loudly enough to be fixed, and pay people the old rate in the meantime -
    underpaying for a week is recoverable, not paying at all is a broken universe.
    """
    try:
        return _rpc("grant_week_points",
                    {"p_league": league, "p_reason": reason, "p_per": rate})
    except StoreError as exc:
        # Match the "no such function" case only. A genuine failure inside the function must
        # still raise: retrying it without p_per would just fail again, one message later and
        # with the real cause buried under a misleading one about migrations.
        if not any(m in str(exc) for m in ("PGRST202", "Could not find the function")):
            raise
        print(f"grant_week_points does not take p_per yet, so {league or 'everyone'} is being "
              f"paid the flat points_per_week instead of {rate}. "
              f"Run supabase/level_income.sql in the SQL editor.", flush=True)
        return _rpc("grant_week_points", {"p_league": league, "p_reason": reason})


# ---------------------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------------------

def get_settings():
    """The whole settings table as a plain dict, with the documented defaults filled in."""
    defaults = {
        "max_characters": 2,
        "starting_points": 20,
        "points_per_week": 1,
        # Per-level income. Absent from the table until somebody sets them, and absent here
        # would make per_week() fall back to the flat rate - which is the right behaviour for
        # a live universe, but means the panel and the site cannot SHOW the rates. Defaulting
        # them here makes the intended curve visible everywhere without a migration.
        "points_per_week_prep": 1,
        "points_per_week_college": 2,
        "points_per_week_pro": 3,
        "auto_approve": False,
        "current_season": 2026,
        "current_week": 0,
    }
    rows = _table("settings", {"select": "key,value"})
    if DRY_RUN:
        return dict(defaults)
    out = dict(defaults)
    for row in rows or []:
        out[row["key"]] = row["value"]
    return out


def set_setting(key, value):
    """Upsert one config key. `value` is stored as JSON, so ints and bools stay typed."""
    return _request("POST", "/rest/v1/settings",
                    params={"on_conflict": "key"},
                    body=[{"key": key, "value": value}],
                    prefer="resolution=merge-duplicates,return=representation")


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------

def _selftest():
    """Walk every public call with DRY_RUN on, printing the HTTP it would perform.

    Runs happily with no credentials at all - the point is to prove the module imports,
    that the paths and RPC names are what schema.sql defines, and that nothing here needs
    Supabase to be reachable before the rest of the commissioner app can start.
    """
    global DRY_RUN
    DRY_RUN = True
    print("commissioner.store selftest - no network, no credentials needed\n")
    print("configuration")
    for k, v in cfg.describe().items():
        print(f"  {k:24} {v}")
    if not cfg.is_configured():
        print("\n  not configured yet -> every real call below would raise StoreNotConfigured")

    steps = [
        ("pending_requests()", lambda: pending_requests()),
        ("pending_requests(league='prep')", lambda: pending_requests("prep")),
        ("mark_applied([...])", lambda: mark_applied(
            ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"])),
        ("approve_requests([...])", lambda: approve_requests(["33333333-3333-3333-3333-333333333333"])),
        ("reject_request(...)", lambda: reject_request("44444444-4444-4444-4444-444444444444",
                                                       "over the archetype cap")),
        ("pending_characters()", lambda: pending_characters()),
        ("activate_character(...)", lambda: activate_character(
            "55555555-5555-5555-5555-555555555555", "prep", "BKI",
            {"league": "prep", "team": "BKI", "name": "Milo Trask", "dob": "3/14/2016"},
            "2016-03-14")),
        ("set_character_status(..., 'retired')", lambda: set_character_status(
            "55555555-5555-5555-5555-555555555555", "retired")),
        ("retire_character(...)", lambda: retire_character(
            "55555555-5555-5555-5555-555555555555", 2048, "declining at 35")),
        ("add_snapshot(...)", lambda: add_snapshot(
            "55555555-5555-5555-5555-555555555555", 2048, 12,
            {"InsideScoring": 61}, {"PotInside": 84}, 78, "pro")),
        ("snapshots(character_id=...)", lambda: snapshots(
            "55555555-5555-5555-5555-555555555555")),
        ("grant_points(..., 1, 'week simmed')", lambda: grant_points(
            "55555555-5555-5555-5555-555555555555", 1, "week simmed")),
        ("grant_week_points('prep')", lambda: grant_week_points("prep")),
        ("get_settings()", lambda: get_settings()),
        ("set_setting('current_week', 4)", lambda: set_setting("current_week", 4)),
        ("characters_for_export()", lambda: characters_for_export()),
    ]
    for label, fn in steps:
        print(f"\n{label}")
        fn()

    print("\ndefaults get_settings() falls back to when a key is missing:")
    for k, v in get_settings().items():
        print(f"  {k:18} {v!r}")

    DRY_RUN = False

    # and prove the real path fails loudly rather than silently, when unconfigured
    if not cfg.is_configured():
        print("\nwith DRY_RUN off and no credentials:")
        try:
            get_settings()
        except StoreNotConfigured as exc:
            print(f"  StoreNotConfigured: {exc}")
        else:
            print("  ERROR: expected StoreNotConfigured")
            return 1
    print("\nok")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m commissioner.store",
                                 description="Supabase access for the Cheezeyverse commissioner.")
    ap.add_argument("--selftest", action="store_true",
                    help="print every request this module would make; needs no credentials")
    ap.add_argument("--settings", action="store_true", help="live: dump the settings table")
    ap.add_argument("--pending", action="store_true",
                    help="live: pending characters and approved-not-applied requests")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    try:
        if args.settings:
            print(json.dumps(get_settings(), indent=2, default=str))
            return 0
        if args.pending:
            print(json.dumps({"characters": pending_characters(),
                              "requests": pending_requests()}, indent=2, default=str))
            return 0
    except StoreNotConfigured as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except StoreError as exc:
        print(f"Supabase error: {exc}", file=sys.stderr)
        return 3
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
