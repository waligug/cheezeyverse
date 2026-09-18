"""The Sim Week pipeline: everything that happens between one set of results and the next.

    pull work -> back up -> apply -> sim -> export -> publish -> grant points

Two rules shape the whole thing:

**Nothing is published that was not verified.** Every codec write is read back out of the file
before the game is allowed to load it, and a failed check restores the backup and aborts. A wrong
week on the website is worse than no week, because people build on what they see.

**One sim at a time, ever.** Two of these driving the same game window would interleave clicks and
destroy a save, so `run_sim` holds a process-wide lock and refuses rather than queues.

The store is whichever is configured - Supabase when the owner has set it up, otherwise the local
JSON file - so this code path is identical before and after that switch.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from . import characters as ch
from . import localstore
from .codec.league_dat import LeagueDat
from .driver.fbpb3 import DOCS, FBPB3
from .publish.publish import publish
from .universe import config as cfg

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
MANIFEST = ROOT / "universe" / "manifest.json"

_SIM_LOCK = threading.Lock()
_RUNNING = {"active": False, "started": None, "steps": []}


class SimBusy(RuntimeError):
    pass


def store():
    """Supabase if it is configured, the local JSON file otherwise."""
    try:
        from . import store as supa
        if supa.is_configured():
            return supa
    except Exception:
        pass
    return localstore


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# ---- status ----------------------------------------------------------------------------------
def _league_status(spec, st):
    save_dir = DOCS / "leaguedata" / spec.save_name
    site = ROOT / "site" / "leagues" / spec.key
    chars = [c for c in st.characters(league=spec.key) if c.get("status") == "active"]
    claimed = [c.get("claimed_slot") or {} for c in chars]
    free = ch.free_slots(_manifest(), spec.key,
                         [{"name": s.get("name"), "dob": s.get("dob")} for s in claimed])
    row = {
        "key": spec.key, "name": spec.name, "save": spec.save_name,
        "teams": len(spec.teams), "characters": len(chars),
        "reserve_free": len(free), "reserve_total": spec.reserve_capacity,
        "season": st.get_settings().get("current_season", cfg.START_YEAR),
        "site": f"site/leagues/{spec.key}/index.htm" if (site / "index.htm").exists() else None,
        "site_pages": len(list(site.rglob("*.htm"))) if site.exists() else 0,
        "exists": (save_dir / "league.dat").exists(),
        "players": None, "games_played": None,
        "day": st.get_settings().get("current_week", 0) * 7,
    }
    row["games_played"] = _games_played(site / "standings.htm")
    row["players"] = _player_count(save_dir / "league.dat")
    row["stage"] = "Preseason" if not row["games_played"] else "Regular season"
    return row


def _games_played(standings):
    """Total games from the generated standings: every game appears as one W and one L."""
    if not standings.exists():
        return 0
    import re
    text = standings.read_text(encoding="latin-1", errors="replace")
    total = 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", c)).replace("&nbsp;", "").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        cells = [c for c in cells if c]
        if len(cells) >= 3 and cells[1].isdigit() and cells[2].isdigit():
            total += int(cells[1]) + int(cells[2])
    return total // 2


_PLAYER_COUNT = {}


def _player_count(path):
    """Cached, because parsing a 4 MB save on every status poll would make the panel crawl."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    hit = _PLAYER_COUNT.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        n = len(LeagueDat(path).players)
    except Exception:
        return None
    _PLAYER_COUNT[path] = (stamp, n)
    return n


def universe_status():
    st = store()
    return {
        "leagues": [_league_status(s, st) for s in cfg.LEAGUES],
        "store": {"kind": getattr(st, "kind", lambda: "supabase")(), "configured": True,
                  "detail": "local JSON file" if st is localstore else "Supabase"},
        "game_running": FBPB3.is_running(),
        "running": dict(_RUNNING),
        "last_sim": (st.runs(1) or [None])[0] if hasattr(st, "runs") else None,
    }


def run_history(limit=20):
    st = store()
    return st.runs(limit) if hasattr(st, "runs") else []


def pending_work():
    st = store()
    return {
        "characters": st.pending_characters(),
        "requests": st.queued_requests() if hasattr(st, "queued_requests") else [],
        "auto_approve": bool(st.get_settings().get("auto_approve", False)),
    }


def set_auto_approve(on):
    """Whether a point spend needs the owner to say yes before the next sim applies it."""
    return store().set_setting("auto_approve", bool(on))


def approve(request_ids):
    return store().approve_requests(list(request_ids))


def reject(request_id, note=""):
    return store().reject_request(request_id, note)


# ---- creating a character --------------------------------------------------------------------
def create_character(payload):
    """Record a character. It gets a reserve slot at the next sim, not immediately."""
    st = store()
    payload = dict(payload)
    payload.setdefault("league", "prep")
    payload.setdefault("status", "pending")
    return st.add_character(payload)


def _activate_pending(league_key, L, st, log):
    """Give every pending character in this league a reserve slot and stamp him into the save."""
    pending = [c for c in st.pending_characters() if c.get("league", "prep") == league_key]
    if not pending:
        return [], []
    active = [c for c in st.characters(league=league_key) if c.get("status") == "active"]
    taken = [{"name": (c.get("claimed_slot") or {}).get("name"),
              "dob": (c.get("claimed_slot") or {}).get("dob")} for c in active]
    slots = ch.free_slots(_manifest(), league_key, taken)
    done, expect = [], []
    for c in pending:
        slot = ch.pick_slot(slots, c.get("position"))
        if slot is None:
            log(f"no reserve slot left in {league_key} for {c['first_name']} {c['last_name']}")
            break
        slots = [s for s in slots if s is not slot]
        ch.stamp_character(L, slot, c)
        st.activate_character(c["id"], league_key, slot.team, slot.as_json(), c["dob"])
        if hasattr(st, "record_level"):
            try:
                placed = L.find(f'{c["first_name"]} {c["last_name"]}', c["dob"])
                st.record_level(c["id"], {
                    "level": league_key, "team_abbrev": slot.team, "player_id": placed.id,
                    "from_season": int(st.get_settings().get("current_season", 0)) or None,
                    "from_age": None, "to_season": None,
                    "how_it_started": "created", "how_it_ended": None,
                })
            except Exception as exc:
                log(f"   (could not record the level for {c['first_name']}: {exc})")
        done.append(c)
        expect.append((f'{c["first_name"]} {c["last_name"]}', c["dob"],
                       {"Height": int(c["height_inches"])}))
        log(f'{c["first_name"]} {c["last_name"]} claimed {slot.team} (was {slot.name})')
    return done, expect


def _apply_requests(league_key, L, st, log):
    """Apply approved point spends as deltas on the live values."""
    reqs = [r for r in st.pending_requests(league=league_key) if r.get("status") == "approved"]
    if not reqs:
        return [], []
    by_char = {}
    for r in reqs:
        by_char.setdefault(r["character_id"], []).append(r)
    applied, expect = [], []
    for char_id, rows in by_char.items():
        c = rows[0]["character"]
        slot = c.get("claimed_slot") or {}
        name = f'{c["first_name"]} {c["last_name"]}'
        deltas = {}
        for r in rows:
            deltas[r["rating"]] = deltas.get(r["rating"], 0) + int(r["delta"])
        moved = ch.apply_deltas(L, name, c.get("game_dob") or slot.get("dob"), deltas)
        expect.append((name, c.get("game_dob") or slot.get("dob"),
                       {k: v[1] for k, v in moved.items()}))
        applied += [r["id"] for r in rows]
        log(f"{name}: " + ", ".join(f"{k} {v[0]}->{v[1]}" for k, v in moved.items()))
    return applied, expect


# ---- the run ---------------------------------------------------------------------------------
def run_sim(leagues=None, days=7, on_step=None, dry_run=False):
    """Apply everything owed, sim `days` in each league, export, publish, grant points."""
    if not _SIM_LOCK.acquire(blocking=False):
        raise SimBusy("a sim is already running")
    keys = list(leagues or [s.key for s in cfg.LEAGUES])
    started = time.time()
    steps = []

    def emit(step, message, league=None, pct=None):
        row = {"step": step, "league": league, "message": message,
               "pct": pct if pct is not None else len(steps) * 100 // max(1, len(keys) * 5 + 2),
               "t": round(time.time() - started, 1)}
        steps.append(row)
        _RUNNING["steps"] = steps[-40:]
        if on_step:
            on_step(row)
        return row

    _RUNNING.update(active=True, started=datetime.now().isoformat(timespec="seconds"), steps=[])
    st = store()
    result = {"ok": False, "leagues": keys, "days": days, "dry_run": dry_run, "applied": 0,
              "activated": 0, "errors": []}
    game = None
    try:
        if FBPB3.is_running():
            emit("backup", "closing a stray FBPB3 first")
            FBPB3.kill()

        # ---- 1. apply everything owed, per league, before the game opens ----------------------
        for key in keys:
            spec = cfg.BY_KEY[key]
            path = ch.save_path(key)
            emit("backup", f"backing up {spec.save_name}", key)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = BACKUPS / f"{stamp}-{spec.save_name}"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest / "league.dat")

            # The game's own AI will cut a 14-year-old for an adult free agent the first chance
            # it gets - it took 80 of our players off Prep rosters on the very first sim. Defang
            # the pool and put anyone it already took back, before anything else is applied.
            if not dry_run:
                from tools.protect_rosters import protect as _protect
                guard = _protect(key, store_characters=st.characters())
                if guard.get("released") or guard.get("signed"):
                    emit("apply", f'AI roster churn undone: {guard["released"]} out, '
                                  f'{guard["signed"]} of ours back in', key)

            # Opened AFTER the guard, which writes the file itself - an object opened before it
            # would hold a stale copy and overwrite the repair on save.
            emit("apply", f"applying pending work to {spec.name}", key)
            L = LeagueDat(path)
            activated, expect_a = _activate_pending(key, L, st, lambda m: emit("apply", m, key))
            applied, expect_b = _apply_requests(key, L, st, lambda m: emit("apply", m, key))
            if dry_run:
                emit("apply", f"dry run: {len(activated)} characters, {len(applied)} requests "
                              f"would be written to {spec.save_name}", key)
                continue
            if activated or applied:
                try:
                    ch.commit(L, expect_a + expect_b)
                except Exception as exc:
                    shutil.copy2(dest / "league.dat", path)
                    raise RuntimeError(f"{key}: write check failed, save restored - {exc}") from exc
                st.mark_applied(applied)
                result["applied"] += len(applied)
                result["activated"] += len(activated)
                emit("apply", f"{len(activated)} characters in, {len(applied)} spends applied", key)
            else:
                emit("apply", "nothing pending", key)

        if dry_run:
            emit("done", "dry run complete - nothing was written or simmed", pct=100)
            result["ok"] = True
            return result

        # ---- 2. drive the game ---------------------------------------------------------------
        game = FBPB3().launch()
        for key in keys:
            spec = cfg.BY_KEY[key]
            emit("sim", f"loading {spec.save_name}", key)
            game.load_save(spec.save_name, wait=30)
            emit("sim", f"simming {days} days of {spec.name}", key)
            game.sim_days(days)
            emit("sim", "saving", key)
            game.save_game()
            emit("export", f"writing {spec.name} pages", key)
            out = game.html_output(spec.save_name)
            emit("export", f"{len(list(out.rglob('*.htm')))} pages", key)
        game.exit_game(save=False)
        game = None

        # ---- 3. publish and pay ---------------------------------------------------------------
        emit("publish", "skinning and staging the sites")
        rows = publish(keys)
        for row in rows:
            emit("publish", f'{row["league"]}: {row["pages"]} pages', row["league"])

        weeks = max(1, round(days / 7))
        for key in keys:
            n = st.grant_week_points(league=key, weeks=weeks)
            if n:
                emit("points", f"{weeks} point(s) to {n} character(s) in {key}", key)
        s = st.get_settings()
        st.set_setting("current_week", int(s.get("current_week", 0)) + weeks)

        result["ok"] = True
        emit("done", f"done in {round(time.time() - started)}s", pct=100)
    except Exception as exc:
        result["errors"].append(str(exc))
        emit("error", str(exc), pct=100)
        raise
    finally:
        if game is not None:
            try:
                game.exit_game(save=False)
            except Exception:
                FBPB3.kill()
        _RUNNING.update(active=False)
        st.record_run({**result, "seconds": round(time.time() - started)})
        _SIM_LOCK.release()
    return result
