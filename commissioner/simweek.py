"""The Sim Week pipeline: everything that happens between one set of results and the next.

    pull work -> back up -> apply -> sim -> export -> snapshot -> publish -> grant points

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

import copy
import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import announce
from . import characters as ch
from . import growth
from . import notify
from . import settings as cfgenv
from . import localstore, recovery
from .saveguard import SAVE_LOCK
from .simstatus import RunProgress, SimStatus
from .codec.league_dat import POTENTIALS, RATINGS, LeagueDat
from .driver.fbpb3 import DOCS, FBPB3
from .publish.publish import publish
from .universe import config as cfg

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
MANIFEST = ROOT / "universe" / "manifest.json"

RATINGS_SET, POTENTIALS_SET = set(RATINGS), set(POTENTIALS)

_SIM_LOCK = SAVE_LOCK
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
def _wait_for_game_to_exit(timeout=30):
    """True once no FBPB3 process is left, or False if one is still there after `timeout`."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe"],
                                 capture_output=True, text=True, timeout=10)
            if "FBPB3.exe" not in out.stdout:
                return True
        except Exception:
            return True          # cannot tell; do not block the week over it
        time.sleep(1)
    return False


def round_one_days(spec):
    """Calendar days to play a league's FIRST playoff round, from its own bracket.

    `playoff_rounds` is four series lengths with leading zeros for rounds a smaller bracket does
    not have, so the first non-zero entry is round one: 1 game in prep and college, best-of-five
    in the pros. FBPB3 played the rehearsal's playoff games every other day (4/24, 4/26, 4/28,
    4/30), so a series of N games spans 1 + 2(N-1) days - 1, 9 and 13 for a single game, a
    best-of-five and a best-of-seven.

    Derived rather than written down, because the brackets are per league and configurable, and
    a number typed into a panel is the only thing stopping a run mid-round.
    """
    live = [n for n in (spec.playoff_rounds or ()) if n]
    if not live:
        return None
    return 1 + 2 * (int(live[0]) - 1)


MARKER = ROOT / "universe" / "run_in_progress.json"


def interrupted_run():
    """Missing means clean; any unreadable recovery journal blocks another run."""
    return recovery.read(MARKER)


def _mark_running(keys, days, season, kind="sim"):
    recovery.write(MARKER, {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind, "phase": "starting", "leagues": list(keys),
        "days": days, "season": season, "unconfirmed": [], "backups": {},
        "store_writes_started": False,
    })


def _mark_saved(key, backup):
    def change(state):
        state.setdefault("unconfirmed", []).append({"league": key, "backup": str(backup)})
        state.setdefault("backups", {})[key] = str(backup)
    _update_marker(change)


def _mark_synced(key):
    def drop(state):
        state["unconfirmed"] = [r for r in state.get("unconfirmed") or []
                                if r.get("league") != key]
    _update_marker(drop)


def _update_marker(change):
    state = interrupted_run()
    if not state or state.get("read_error"):
        raise RuntimeError("Recovery journal is missing or unreadable; refusing further writes")
    change(state)
    recovery.write(MARKER, state)


def _clear_marker():
    MARKER.unlink(missing_ok=True)


def describe_interruption(state):
    when = state.get("started_at", "an unknown time")
    return (f"A {state.get('kind', 'sim')} started at {when} never finished. "
            f"Last recorded phase: {state.get('phase', 'unknown')}. "
            f"Backup evidence: {state.get('backups', {})}. "
            "Further runs are blocked. Reconcile the saves with activations, upgrades, points, "
            "season/week settings and run history before clearing universe/run_in_progress.json. "
            "Do not rerun or restore only the binary saves: database writes may already have landed.")


def phase_totals(steps, total):
    """[(phase, seconds)] for one run, biggest first, from the step log it already keeps.

    Every speed decision made on this pipeline so far came from somebody timing a log stream by
    hand, which is how a 30-second sleep sat inside every load for months without anybody being
    able to say what a load cost. The steps are already stamped with `t`; this is only the
    subtraction, and it turns "the sim feels slow" into a number per phase that the next change
    can be measured against.

    A step's time is charged to the step BEFORE the next one starts, because emit() is called as
    each piece of work begins. The last step runs until the end of the run.
    """
    if not steps:
        return []
    totals = {}
    for i, row in enumerate(steps):
        start = row.get("t")
        if start is None:
            continue
        end = steps[i + 1].get("t") if i + 1 < len(steps) else total
        if end is None or end < start:
            continue
        name = row.get("step") or "other"
        totals[name] = totals.get(name, 0.0) + (end - start)
    return sorted(totals.items(), key=lambda kv: -kv[1])


def _league_status(spec, st, settings=None, characters=None):
    """One league's row for the panel.

    `settings` and `characters` are passed in by universe_status so the whole poll makes ONE of
    each call instead of one per league. It was three round trips a league - get_settings twice
    and characters(league) once - which measured 6.8s for prep alone and made /api/state take
    between three and twenty-one seconds. Long enough that somebody clicks the button again.
    """
    save_dir = DOCS / "leaguedata" / spec.save_name
    site = ROOT / "site" / "leagues" / spec.key
    if settings is None:
        settings = st.get_settings()
    rows = ([c for c in characters if c.get("league") == spec.key] if characters is not None
            else st.characters(league=spec.key))
    chars = [c for c in rows if c.get("status") == "active"]
    # A slot is held by whoever still claims it, not by whoever is still playing: a career the
    # game ended keeps its claim, because that row is gone from the save and handing it out
    # again would only fail later. See the note at the top of offseason.py.
    claimed = [c["claimed_slot"] for c in rows if c.get("claimed_slot")]
    free = ch.free_slots(_manifest(), spec.key,
                         [{"name": s.get("name"), "dob": s.get("dob")} for s in claimed])
    row = {
        "key": spec.key, "name": spec.name, "save": spec.save_name,
        "teams": len(spec.teams), "characters": len(chars),
        "reserve_free": len(free), "reserve_total": spec.reserve_capacity,
        "season": settings.get("current_season", cfg.START_YEAR),
        "site": f"site/leagues/{spec.key}/index.htm" if (site / "index.htm").exists() else None,
        "site_pages": len(list(site.rglob("*.htm"))) if site.exists() else 0,
        "exists": (save_dir / "league.dat").exists(),
        "players": None, "games_played": None,
        "day": settings.get("current_week", 0) * 7,
        # What the panel needs at the season boundary, per league, because one number in one box
        # cannot be right for three leagues at once. None means NO OPINION and must be shown as
        # "cannot tell" rather than as a number: at this boundary a confident wrong answer is
        # the expensive kind.
        #   regular_season_left - dates with games still to play (the guard's own, conservative)
        #   days_to_season_end - SIM DAY clicks to the last regular-season day (what to type)
        #   champion          - set once the final is decided, after which nothing may sim here
        "regular_season_left": _regular_season_left(save_dir),
        "days_to_season_end": days_to_regular_end(spec.key),
        "champion": _champion(save_dir, settings.get("current_season")),
        #   round_one_days    - days to play the first playoff round, from the bracket
        "round_one_days": round_one_days(spec),
    }
    # Read how far the season has got from the GAME'S OWN export, not from the published copy.
    # They are usually the same file one step apart, but not always, and the difference matters
    # exactly when things are least clear: create_universe --force deletes the save's html
    # folder, so straight after a rebuild the game has exported nothing while site/leagues/
    # still holds the last publish of the OLD universe. The panel then reported a brand-new
    # Preseason save as "Regular season, 30 games" - the previous season's numbers, presented
    # as this one's, at the one moment somebody most needs to know what state the save is in.
    #
    # Same story after restoring a backup. The save is the truth; the published site is a
    # photograph of it.
    own = save_dir / "html" / "standings.htm"
    if own.exists():
        row["games_played"] = _games_played(own)
        row["stage"] = _stage(save_dir / "html" / "schedule.htm", row["games_played"])
    else:
        row["games_played"] = None
        row["stage"] = "not exported yet"
    row["published_games"] = _games_played(site / "standings.htm")
    row["players"] = _player_count(save_dir / "league.dat")
    return row


def _stage(schedule, games_played):
    """Preseason, Regular season or Playoffs, read from the schedule's own section headings.

    "any games played means the regular season has started" is wrong, and misleadingly so: the
    standings carry PRESEASON wins and losses while the preseason is being played, so the panel
    announced "Regular season, 18 games" during camp - and then dropped back to "Preseason,
    0 games" when FBPB3 zeroed the standings at the real season's start, which reads like
    something broke. The schedule page marks its own sections; find the last one that has a
    played game under it.
    """
    if not schedule.exists():
        return "Preseason" if not games_played else "Regular season"
    try:
        text = schedule.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return "Regular season" if games_played else "Preseason"
    stage = "Preseason"
    for label in ("Preseason", "Regular Season", "Playoffs"):
        at = text.find(label)
        if at == -1:
            continue
        # A played game is a score; an unplayed one is not. Look between this heading and the
        # next for anything that has been decided.
        nxt = min((text.find(n, at + 1) for n in ("Regular Season", "Playoffs")
                   if text.find(n, at + 1) != -1), default=len(text))
        section = text[at:nxt]
        # A played game is the only thing FBPB3 links to a box score. Matching on
        # "looks like a score" instead caught the Playoffs heading itself and
        # reported every league as being in the playoffs on day one.
        if "boxes/box" in section:
            stage = "Regular season" if label == "Regular Season" else label
    return stage


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
    # ONE of each call for the whole poll, not one per league. See _league_status.
    try:
        settings = st.get_settings()
    except Exception:
        settings = {}
    try:
        everyone = st.characters()
    except Exception:
        everyone = None          # let each league fetch its own rather than report nobody
    return {
        "leagues": [_league_status(s, st, settings=settings, characters=everyone)
                    for s in cfg.LEAGUES],
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
class NameTaken(ValueError):
    pass


def _arrival_dob(value, season):
    """Use the chosen/slot month and day, but make every new character fourteen.

    Reserve rows span Prep's whole age band. Inheriting the row's complete birthday made some
    created characters arrive at fifteen or seventeen even though the builder, growth model and
    career page all start at fourteen. The slot keeps its own DOB in claimed_slot for recycling;
    this is the character's identity while he owns it.
    """
    dob = ch.codec_dob(value)
    if not dob or season is None:
        return dob
    month, day, _year = (int(part) for part in dob.split("/"))
    return f"{month}/{day}/{int(season) - growth.START_AGE}"


def create_character(payload):
    """Record a character. It gets a reserve slot at the next sim, not immediately.

    Names have to be unique within a league: the codec finds a player by name and birthday, so two
    characters called the same thing in the same save are indistinguishable - `find` raises
    "2 players match" and NEITHER of them can ever be upgraded, promoted or retired again.
    """
    st = store()
    payload = dict(payload)
    payload.setdefault("league", "prep")
    payload.setdefault("status", "pending")
    wanted = f'{payload.get("first_name", "")} {payload.get("last_name", "")}'.strip().lower()
    for other in st.characters():
        if other.get("status") == "retired":
            continue
        if other.get("league") != payload["league"]:
            continue
        if f'{other.get("first_name", "")} {other.get("last_name", "")}'.strip().lower() == wanted:
            raise NameTaken(f"{wanted.title()} is already playing in {payload['league']}")
    return st.add_character(payload)


def _activate_pending(league_key, L, st, log, season=None):
    """Give every pending character in this league a reserve slot and stamp him into the save.

    Returns (done, expect, writes). NOTHING IS WRITTEN TO THE STORE HERE. `writes` is a list of
    thunks the caller runs only once ch.commit has proved the save really holds them.

    That split is the fix for a bug that cost a character his career on paper: the store writes
    used to happen here, before `if dry_run: continue` and before the commit. So asking the panel
    what a run WOULD do - the button says "touches no save" - marked him active on a filler's
    reserve row, and the run then wrote nothing. He was no longer pending, so no later run ever
    stamped him into league.dat: his slot stayed claimed, the roster guard released the filler
    row it could still see, every upgrade failed, he was paid weekly for games he was not in, and
    the offseason would have retired a character the save had never heard of.
    """
    pending = [c for c in st.pending_characters() if c.get("league", "prep") == league_key]
    if not pending:
        return [], [], []      # three, like every other exit: (done, expect, writes)
    # Held by whoever still claims it, whatever his status - a retired character only lets go
    # of his slot once the row really has its manifest name back (offseason.refill).
    holders = [c for c in st.characters(league=league_key) if c.get("claimed_slot")]
    taken = [{"name": c["claimed_slot"].get("name"),
              "dob": c["claimed_slot"].get("dob")} for c in holders]
    slots = ch.free_slots(_manifest(), league_key, taken)

    # Spread new characters across the league. The first five friends all landed on two teams,
    # because slots were taken in team order and each team's three reserves sit together.
    spec = cfg.BY_KEY[league_key]
    busy = {t.abbrev: 0 for t in spec.teams}
    for other in st.characters(league=league_key):
        if other.get("status") in ("active", "declared") and other.get("team_abbrev"):
            busy[other["team_abbrev"]] = busy.get(other["team_abbrev"], 0) + 1
    divisions = {t.abbrev: t.division for t in spec.teams}

    # Where each reserve row ACTUALLY is, read from the save. The manifest says where a row
    # started, and rows move - a CPU trade moves them, and so does relocating a character on
    # purpose - so trusting the manifest would place somebody on a team the slot has left.
    ids = sorted({p.values["Team"] for p in L.players if p.values["Team"] >= 1})
    abbrev_of = {tid: t.abbrev for tid, t in zip(ids, spec.teams)}

    def team_of(slot):
        try:
            return abbrev_of.get(L.find(slot.name, ch.codec_dob(slot.dob)).values["Team"], slot.team)
        except Exception:
            return slot.team           # not in the save under that name: fall back to the manifest

    # The day the save is sitting on is the first day he can possibly play, and it is read from
    # league.dat itself rather than reconstructed later from the run log. See
    # LeagueDat.season_day and headtohead.stored_debut.
    stamped = L.season_day()
    if stamped is None:
        log(f"   (could not read {league_key}'s season day; arrivals will fall back to the run log)")
    day_now, game_year = (stamped if stamped else (None, None))

    done, expect, writes = [], [], []
    for c in pending:
        slot = ch.pick_slot(slots, c.get("position"), busy=busy, divisions=divisions,
                            team_of=team_of)
        if slot is None:
            log(f"no reserve slot left in {league_key} for {c['first_name']} {c['last_name']}")
            break
        slots = [s for s in slots if s is not slot]
        landed = team_of(slot)
        busy[landed] = busy.get(landed, 0) + 1   # or everyone created this week stacks up again
        # The website does not ask for a birthday, so retain the reserve row's month/day. Its
        # YEAR cannot come from the slot: reserve rows span ages 14-17, while every character's
        # progression begins at fourteen. claimed_slot still records the slot's original DOB so
        # refill can restore its identity when the character leaves.
        dob = _arrival_dob(c.get("dob") or slot.dob, season)
        before_stamp = copy.deepcopy(L.__dict__)
        try:
            ch.stamp_character(L, slot, {**c, "dob": dob})
        except Exception as exc:
            L.__dict__.clear()
            L.__dict__.update(before_stamp)
            # A slot the STORE believes is free that the SAVE cannot produce. The offseason
            # leaves exactly that behind when it fails and restores: the store has already let
            # go of a claim, the restored save still has the old occupant's name on the row, and
            # the manifest name the stamp looks for is in neither. Unhandled, this raised out of
            # the activation phase and took the whole week with it - and the week is everybody
            # else's sim, not just his. He stays pending and gets a slot next time.
            log(f'   ! could not place {c["first_name"]} {c["last_name"]} in {league_key}: {exc}')
            busy[landed] = max(0, busy.get(landed, 1) - 1)
            continue

        def _write(c=c, slot=slot, dob=dob, landed=landed):
            st.activate_character(c["id"], league_key, slot.team, slot.as_json(), dob)
            # Announced from inside the write, after the save has already been committed, so
            # Discord never gets a card for a character who did not actually land.
            announce.arrival(c, league_key, spec.name, landed, log=log)
            if not hasattr(st, "record_level"):
                return
            try:
                placed = L.find(f'{c["first_name"]} {c["last_name"]}', dob)
                st.record_level(c["id"], {
                    "level": league_key, "team_abbrev": slot.team, "player_id": placed.id,
                    "from_season": season, "from_day": day_now, "from_game_year": game_year,
                    "from_age": None, "to_season": None,
                    "how_it_started": "created", "how_it_ended": None,
                })
            except Exception as exc:
                log(f"   (could not record the level for {c['first_name']}: {exc})")

        writes.append(_write)
        done.append(c)
        expect.append((f'{c["first_name"]} {c["last_name"]}', dob,
                       {"Height": int(c["height_inches"]),
                        # Weight is written now, so the commit check proves it landed. A weight
                        # that silently did not take is exactly the mismatch the slider exists
                        # to remove: his card says one number and the game plays another.
                        "Weight": ch.weight_for(c)}))
        log(f'{c["first_name"]} {c["last_name"]} claimed {slot.team} (was {slot.name})'
            + (f", from day {day_now}" if day_now else ""))
    return done, expect, writes


def _sync_teams(league_key, L, st, log):
    """Write back the team FBPB3 says each character is on.

    CPU trades are left on deliberately - the AI moving players around is flavour - so the team a
    character was placed on is not the team he is on next week. Nothing else updates it, so the
    site would show a player at a club he left a season ago.
    """
    spec = cfg.BY_KEY[league_key]
    try:
        ids = sorted(L.teams())
    except Exception as exc:
        log(f"could not read teams for {league_key}: {exc}")
        return 0
    by_id = {tid: spec.teams[i].abbrev for i, tid in enumerate(ids) if i < len(spec.teams)}
    moved = 0
    for c in st.characters(league=league_key):
        if c.get("status") != "active":
            continue
        try:
            pl = L.find(f'{c["first_name"]} {c["last_name"]}', ch.codec_dob(c.get("game_dob")))
        except Exception:
            continue
        now = by_id.get(pl.values["Team"])
        if now and now != c.get("team_abbrev") and hasattr(st, "set_character_field"):
            st.set_character_field(c["id"], "team_abbrev", now)
            log(f'{c["first_name"]} {c["last_name"]} is on {now} now, not {c.get("team_abbrev")}')
            moved += 1
    return moved


def _dress_characters(league_key, L, st, log):
    """Make sure every character is dressed and on the depth chart before the week is simmed.

    The AI coach rewrites depth charts constantly and will bench the worst man on the roster, so
    doing this once at creation is not enough: a character drops off the chart the first time the
    coach reshuffles, and from then on he silently never plays again. Re-asserting it every week
    is the difference between a career and a name on a bench.
    """
    fixed = []
    for c in st.characters(league=league_key):
        if c.get("status") != "active":
            continue
        name = f'{c["first_name"]} {c["last_name"]}'
        try:
            pl = L.find(name, ch.codec_dob(c.get("game_dob")))
        except Exception as exc:
            # This used to swallow the exception entirely. The weekly re-dress is what keeps a
            # character on the depth chart after the AI coach reshuffles it, so silently
            # skipping it meant a character slowly stopped playing and nothing ever said so.
            log(f"could not re-dress {name}: {exc}")
            continue
        try:
            if L.dress(pl):
                fixed.append(name)
        except Exception as exc:
            log(f"could not dress {name}: {exc}")
    if fixed:
        log(f"dressed {len(fixed)}: {', '.join(fixed[:4])}")
    return fixed


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

        # Drop what cannot be applied and keep the rest. One malformed request - a rating that
        # does not exist, or the locked one - used to raise straight through run_sim and abort
        # the week for EVERYBODY. A bad request is one person's problem; it must never be
        # everyone's. Rejecting it also returns the points it reserved.
        # Potentials first, then ratings. apply_deltas will not push a rating above its own
        # potential - that is the game's rule - so buying +5 potential and +5 rating in the same
        # week only works if the ceiling is raised before the rating is pushed at it. Applied the
        # other way round the rating silently stops at the old ceiling and the points are gone.
        rows = sorted(rows, key=lambda r: 0 if (r.get("kind") or "rating") == "potential" else 1)
        deltas, keep = {}, []
        for r in rows:
            # A request names the RATING it is about and says in `kind` whether it is buying the
            # rating or its ceiling: cv_potentials() returns rating names, so the name alone
            # cannot tell them apart. Reading only r["rating"] meant every potential purchase was
            # applied as a rating purchase - which apply_deltas then caps at the potential the
            # buyer was trying to raise, so it did nothing at all while the points were spent.
            kind = (r.get("kind") or "rating").lower()
            field = r["rating"]
            if kind == "potential":
                field = ch.POT_BY_RATING.get(field, field)
            if field in ch.LOCKED or (field not in RATINGS_SET and field not in POTENTIALS_SET):
                why = ("that rating cannot be spent on" if field in ch.LOCKED
                       else f'there is no {kind} called {r["rating"]}')
                log(f"{name}: rejected {field} - {why}")
                try:
                    st.reject_request(r["id"], why)
                except Exception:
                    pass
                continue
            deltas[field] = deltas.get(field, 0) + int(r["delta"])
            keep.append(r)
        if not deltas:
            continue
        try:
            moved = ch.apply_deltas(L, name, ch.codec_dob(c.get("game_dob") or slot.get("dob")),
                                    deltas)
        except Exception as exc:
            log(f"{name}: none of his requests could be applied - {exc}")
            continue
        expect.append((name, ch.codec_dob(c.get("game_dob") or slot.get("dob")),
                       {k: v[1] for k, v in moved.items()}))
        applied += [r["id"] for r in keep]
        log(f"{name}: " + ", ".join(f"{k} {v[0]}->{v[1]}" for k, v in moved.items()))
    return applied, expect


def _snapshot_league(league_key, st, season, week, log):
    """Record what the save now says about every active character in this league.

    `characters.ratings` is one live sheet that the next Sim Week overwrites, so this is the
    only place a career's shape is ever written down - the career page's growth chart reads
    it, and the offseason's decline test decides a veteran is finished by comparing his live
    sheet against the best one in here. One row per character per run, not one per rating.
    """
    if not hasattr(st, "add_snapshot"):
        return 0
    live = [c for c in st.characters(league=league_key) if c.get("status") == "active"]
    if not live:
        return 0
    L = LeagueDat(ch.save_path(league_key))
    written = 0
    for c in live:
        name = f'{c["first_name"]} {c["last_name"]}'
        try:
            # codec_dob, like every other place a birthday reaches the codec. game_dob is a
            # Postgres date and comes back as 2015-11-27, while LeagueDat.find compares the
            # string the save builds: 11/27/2015. Without this the lookup matches nobody, the
            # snapshot is skipped with a one-line log nobody reads, and the career graph has no
            # points in it - which is invisible until somebody opens their player's page weeks
            # later and finds a flat line. Fourteen call sites were fixed and this was the
            # fifteenth.
            pl = L.find(name, ch.codec_dob(c.get("game_dob")
                                           or (c.get("claimed_slot") or {}).get("dob")))
        except Exception as exc:
            # Not fatal and not a verdict: the offseason is where a character the save has
            # lost is investigated and his career formally ended.
            log(f"no sheet for {name}: {exc}")
            continue
        st.add_snapshot(character_id=c["id"], season=season, week=week,
                        ratings={f: pl.values[f] for f in RATINGS},
                        # keyed by the RATING, which is how the website, the database and
                        # the career graph all name a potential. POTENTIALS holds the codec's
                        # own names (PotInside...), which nothing outside the codec reads.
                        potentials=ch.store_potentials(pl.values),
                        height_inches=pl.values["Height"], league=league_key)
        written += 1
    return written


# ---- the run ---------------------------------------------------------------------------------
def _discord_report(steps, result, seconds):
    """The end-of-run message, built from the step log rather than from new plumbing.

    Everything worth telling people was already emitted during the run - who got placed, how
    many points were paid, whether the push landed - so this reads the log instead of threading
    return values back through five stages that do not otherwise need them.
    """
    placed = [r["message"] for r in steps
              if r.get("step") == "apply" and " claimed " in str(r.get("message", ""))]
    points = [r["message"] for r in steps if r.get("step") == "points"]
    published = any("the public site is live" == r.get("message") for r in steps)

    weeks = max(1, round(int(result.get("days") or 7) / 7))
    if result.get("days_by_league"):
        advanced = ", ".join(f"{key}: {count} days" for key, count in result["days_by_league"].items())
        lines = [f'**Sim done** - {advanced} in {round(seconds / 60)} min']
    else:
        lines = [f'**Sim done** - {weeks} week(s) of '
                 f'{", ".join(result.get("leagues") or [])} in {round(seconds / 60)} min']
    for line in placed:
        lines.append(f"- {line}")
    for line in points:
        lines.append(f"- {line}")
    try:
        from .leaguenews import report_lines
        news = report_lines(result.get("news"))
    except Exception:
        news = []
    if news:
        lines.append("**League news**")
        lines.extend(news)
    if published:
        site = (cfgenv.get("SITE_URL", "") or "").strip()
        lines.append(f"- the site is live{': ' + site if site else ''}")
    elif not result.get("dry_run"):
        lines.append("- the site did NOT publish; the week itself is saved")
    return "\n".join(lines)


class SeasonEnd(RuntimeError):
    """A run was asked for that would sim past the last day of the regular season."""


def _season_blocks(save_dir):
    """[(date string, was it played)] for the regular season, in the order the page prints them.

    NO DATE PARSING. The first version of this did `strptime(..., "%m/%d/%Y")`, which was read
    off a published page from SERVERPC - and FBPB3 is VB6, so it renders the WINDOWS SHORT DATE
    of whatever machine it runs on. This desktop's own export is ISO (2030-10-15); SERVERPC's is
    10/20/2026. The guard would therefore have gone silently inert the day anybody changed a
    regional setting or rebuilt the universe on a different box, which is the whole failure this
    project keeps meeting: true of the context it was measured in, false of the one that matters.
    A schedule is printed in date order by definition, so position in the document is all that is
    needed and it cannot be wrong about a format.

    "Played" is the presence of a box-score link, which is what `_stage` settled on after
    "looks like a score" matched the Playoffs heading itself. The score pattern is kept only as a
    fallback: `restyle` strips box links from the PUBLISHED copy, and while this reads the raw
    export today, a reader pointed at the published one should degrade rather than see nothing.
    """
    schedule = Path(save_dir) / "html" / "schedule.htm"
    try:
        raw = schedule.read_text(encoding="latin-1", errors="replace")
    except OSError:
        return None
    text = re.sub("[ ]+", " ", re.sub("<[^>]+>", " ", raw.replace("./boxes/box", "boxes/box")))
    at = text.find("Regular Season")
    if at == -1:
        return None
    nxt = text.find("Playoffs", at + 1)
    section = text[at:nxt if nxt != -1 else len(text)]
    parts = re.split("&nbsp;([0-9]{1,4}[-/][0-9]{1,2}[-/][0-9]{1,4})", section)
    out = []
    for i in range(1, len(parts), 2):
        body = parts[i + 1] if i + 1 < len(parts) else ""
        played = ("boxes/box" in body) or bool(re.search("[0-9]+ ?, ?@?[A-Za-z]", body))
        out.append((parts[i], played))
    return out or None


def _regular_season_left(save_dir):
    """How many more SIM DAY clicks are certainly still inside the regular season, or None.

    Counted as the number of scheduled dates AFTER the last one that has been played. That is a
    deliberate under-estimate: one click advances one calendar day, there are at least as many
    calendar days left as there are remaining game days, so spending the count can never reach
    the end. It also sidesteps the fact that the game's CURRENT date is not in the export at all
    - only days with games get a heading, so after a quiet stretch the save is already some days
    ahead of the last played date, and any calendar-arithmetic answer is too generous by exactly
    that much.

    None means "no opinion": no export, no regular-season section, or nothing played yet. The
    caller says so out loud rather than treating it as zero.
    """
    blocks = _season_blocks(save_dir)
    if not blocks:
        return None
    last = max((i for i, (_d, played) in enumerate(blocks) if played), default=None)
    if last is None:
        return None
    return len(blocks) - 1 - last


def _champion(save_dir, season=None):
    """The team that has won THIS season's final, or None while it is undecided.

    Read from playoffs.htm through seasonbonus, which is the page that is complete the moment
    the final ends - champs.htm stays empty for some days after it, as the season-end rehearsal
    found by exporting at both points.

    THE YEAR MATTERS. The rollover clears the playoffs from the schedule but the EXPORT keeps
    last season's bracket page until somebody plays a new one, so the day after a rollover the
    file still reads "2026 Playoff Brackets" and still names a champion. Without this check the
    boundary guard read that as "the season is over" and refused every sim of the new season -
    permanently, since only playing the new playoffs would have replaced the page.
    """
    try:
        from .seasonbonus import playoff_bracket, _text
        html = Path(save_dir) / "html"
        if season is not None:
            year = re.search(r"(\d{4})\s+Playoff Brackets", _text(html / "playoffs.htm"))
            if year and int(year.group(1)) != int(season):
                return None
        return playoff_bracket(html)[1]
    except Exception:
        return None


_SEASON_END_CACHE = {}
_SEASON_END_WORKING = {}


def _fill_season_end(key):
    """Work the answer out in the background and leave it in the cache for the next poll."""
    try:
        days_to_regular_end(key, compute=True)
    finally:
        _SEASON_END_WORKING[key] = False


def days_to_regular_end(key, compute=False):
    """SIM DAY clicks from where the save sits to the last day of the regular season, or None.

    EXACT, unlike `_regular_season_left`, which counts remaining dates that have GAMES and is
    deliberately an under-estimate. A click is a calendar day, so the two differ by every quiet
    day in between - today they are 8 and 10 in prep, and the panel needs the one a person types
    into the day box.

    Both numbers come from data already written: the day the save is sitting on is read out of
    league.dat, and the last regular-season day number is `MAX(Day) WHERE Type = 1` in the MDB
    the sim exports every run. No date parsing, so no dependence on the machine's date format -
    which is what made the first version of the boundary guard silently inert.

    Cached against both files' timestamps, because the panel asks on every poll and the answer
    only changes when a sim moves the save or rewrites the MDB.
    """
    save = ch.save_path(key)
    mdb = save.parent / "LeagueOutput.mdb"
    if not save.exists() or not mdb.exists():
        return None
    stamp = (save.stat().st_mtime, mdb.stat().st_mtime)
    cached = _SEASON_END_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    # NEVER BLOCK THE PANEL. Reading the MDB means launching the 32-bit PowerShell, about two
    # seconds a league, which made the first /api/state after a restart take sixteen. Callers
    # ask without `compute` and get the cached answer or None; the work happens on a background
    # thread and lands before the next poll. None is already rendered as "cannot tell" rather
    # than as a number, so a moment of not knowing is honest rather than wrong. (Flask serves
    # requests on worker threads, so "is this the main thread?" cannot be the test here.)
    if not compute:
        if not _SEASON_END_WORKING.get(key):
            _SEASON_END_WORKING[key] = True
            threading.Thread(target=_fill_season_end, args=(key,), daemon=True).start()
        return cached[1] if cached else None
    value = None
    try:
        from .codec.league_dat import find_season_day
        from . import headtohead
        today = find_season_day(save.read_bytes())
        rows = headtohead.query(mdb, "SELECT MAX(Day) AS LastDay FROM Schedule WHERE Type = 1")
        last = int((rows or [{}])[0].get("LastDay") or 0)
        if today and last:
            value = max(0, last - int(today[0]) + 1)
    except Exception:
        value = None
    _SEASON_END_CACHE[key] = (stamp, value)
    return value


def _refuse_to_cross_the_season(keys, days, emit, allow_season_end=False, season=None):
    """Stop a run that would sim past the last day of the regular season.

    `allow_season_end` is the deliberate way into the PLAYOFFS, and it is not a bypass: what it
    permits is exactly what the rehearsal demonstrated on a copy - crossing the last day raises
    no dialog, SIM DAY plays playoff games one a day, and sim_days now stops by itself when the
    calendar stops moving, which is what the 6/21 button swap looks like.

    What it does NOT permit is simming a league whose final is already decided. After that the
    only thing left is FBPB3's own rollover behind END SEASON, and offseason.py owns the
    rollover through the codec. Nothing has ever run both, so that refusal stands whatever flag
    is passed.

    WHAT THE GAME DOES PAST IT IS NOW KNOWN, and it is not the driver that is the danger. A
    rehearsal on a copy of CV_Prep (2026-09-19) simmed straight through: no dialog at the season
    end, SIM DAY plays playoff games one a day, then 51 idle days, and on 6/21 the Hot Seat's sim
    buttons are REPLACED by the offseason panel - END SEASON, DRAFT LOTTERY and the rest - while
    the stage label still reads POSTSEASON. sim_days catches that now, because it waits for the
    calendar to move and raises when it does not.

    THE REASON TO STILL REFUSE is what comes after: END SEASON is FBPB3's own rollover, and
    offseason.py owns that - it ages, promotes, drafts and pays everybody through the codec.
    Nothing has ever run both, and what the game would do to a universe offseason.py then also
    processed is untested, on a live universe with seven real people in it. Crossing is safe;
    what is on the other side is not designed yet.

    Worth guarding rather than remembering: the panel takes a number from whoever is typing.
    """
    # FIRST, and whatever the flags say: a league whose final is over has nothing left to sim.
    for key in keys:
        champ = _champion(ch.save_path(key).parent, season)
        if champ:
            raise SeasonEnd(
                f"{key}'s season is over - {champ} won it. What comes next is FBPB3's own "
                "season rollover. Finish the other leagues, then choose Start next season "
                "in the Offseason panel.")

    limits, blind = [], []
    for key in keys:
        left = _regular_season_left(ch.save_path(key).parent)
        (blind if left is None else limits).append(key if left is None else (key, left))
    if blind:
        # Never silent. A guard that cannot read its own boundary and says nothing is
        # indistinguishable from one that is working, which is how the first version of this
        # shipped believing itself tested.
        emit("start", f"cannot read the season boundary for {', '.join(blind)}; "
                      "not guarding those")
    if not limits:
        return
    key, left = min(limits, key=lambda kv: kv[1])
    if allow_season_end:
        crossing = [f"{k} ({n} left)" for k, n in sorted(limits, key=lambda kv: kv[1])
                    if days > n]
        if crossing:
            emit("start", "into the playoffs: the regular season ends mid-run for "
                          + ", ".join(crossing) + ". SIM DAY plays playoff games a day at a "
                          "time, and the run stops by itself if the calendar stops moving - "
                          "which is what the offseason panel looks like.")
        return
    if days <= left:
        return
    if left <= 0:
        raise SeasonEnd(
            f"{key}'s regular season has no days left to sim - every remaining game has been "
            "played. The playoffs are what comes next: start the run 'into the playoffs' "
            "(allow_season_end) and they sim a day at a time like any other day.")
    raise SeasonEnd(
        f"{days} days would run past the end of the regular season: {key} has {left} more "
        f"day(s) with games scheduled. Sim {left} days or fewer, or start the run 'into the "
        f"playoffs' (allow_season_end) to carry on through them - which the season-end "
        f"rehearsal showed is safe, and which stops by itself at the offseason panel that "
        f"nothing here is built to drive.")


def _restore_calendar_checkpoint(game, checkpoint, live_path):
    """Close FBPB and replace a wrong-date save with its post-prepare checkpoint."""
    try:
        game.exit_game(save=False)
    except Exception:
        FBPB3.kill()
    checkpoint = Path(checkpoint) if checkpoint else None
    if checkpoint is None or not checkpoint.is_file():
        raise RuntimeError("the post-prepare recovery checkpoint is missing")
    shutil.copy2(checkpoint, live_path)


def run_sim(leagues=None, days=7, on_step=None, dry_run=False,
            allow_season_end=False, expected_state=None, days_by_league=None, expected_states=None,
            start_dates=None):
    """Apply everything owed, sim `days` in each league, export, publish, grant points."""
    if not _SIM_LOCK.acquire(blocking=False):
        raise SimBusy("a sim is already running")
    keys = list(leagues or [s.key for s in cfg.LEAGUES])
    started = time.time()
    steps = []
    progress, status = None, None
    failure_summary = None

    journal_phase = None

    def at(stage, league=None, fraction=0):
        nonlocal journal_phase
        if marked and journal_phase != (stage, league):
            _update_marker(lambda state: state.update(phase=stage, league=league))
            journal_phase = (stage, league)
        value = progress.at(stage, league, fraction)
        if status is not None:
            status.update(percent=value, stage=stage, league=league)
        return value

    def emit(step, message, league=None, pct=None):
        row = {"step": step, "league": league, "message": message,
               "pct": pct if pct is not None else (progress.percent if progress else 0),
               "t": round(time.time() - started, 1)}
        steps.append(row)
        _RUNNING["steps"] = steps[-40:]
        if status is not None:
            status.update(percent=row["pct"], detail=message, league=league)
        if on_step:
            on_step(row)
        return row

    _RUNNING.update(active=True, started=datetime.now().isoformat(timespec="seconds"), steps=[])
    st = store()
    result = {"ok": False, "leagues": keys, "days": days, "dry_run": dry_run, "applied": 0,
              "activated": 0, "snapshots": 0, "news": {}, "errors": []}
    game = None
    prepared_backups = {}
    fresh_mdb = set()
    # Defined before the try because the finally logs it, and a refusal raises above the read.
    season_now, settings = None, {}
    marked = False
    try:
        if not dry_run and interrupted_run():
            raise RuntimeError(describe_interruption(interrupted_run()))
        day_counts = {key: int((days_by_league or {}).get(key, days)) for key in keys}
        if days_by_league is not None and any(n < 1 or n > 400 for n in day_counts.values()):
            raise ValueError("Calendar day counts must be between 1 and 400")
        if days_by_league:
            result["days_by_league"] = day_counts
        if expected_state is not None or expected_states is not None:
            from .codec.league_dat import find_season_day
            if FBPB3.is_running():
                raise SeasonEnd("Close the game and refresh the calendar before starting.")
            for key in keys:
                actual = find_season_day(ch.save_path(key).read_bytes())
                expected = (expected_states or {}).get(key, expected_state)
                if expected is None or actual != tuple(expected):
                    raise SeasonEnd("The save changed since the calendar preview. Refresh and choose again.")
        progress = RunProgress(keys, day_counts if days_by_league else days)
        # INSIDE the try, and first. _SIM_LOCK was acquired above and the only thing that ever
        # releases it is this try's finally - so raising above this line held the lock for the
        # life of the process, and offseason.py deliberately shares that lock, meaning one
        # mistyped number would have bricked Sim Week AND the rollover until a restart. The
        # panel would have reported "another process is already simming", which would have been
        # false and sent somebody to the server to hunt a process that did not exist.
        #
        # Also skipped for a dry run: that path never launches FBPB3 and never clicks anything,
        # and it is the natural way to ask what a long run would do - refusing it defeats the
        # one safe way to find out.
        # Called even WITH allow_season_end, because the flag opens the playoffs and nothing
        # else: a league whose final is decided is refused either way, since the only thing
        # after that is the rollover behind END SEASON.
        # ONE read of the settings for the whole run. There were three, with three different
        # defaults, and a fourth inside the finally - which went out over the network while
        # _SIM_LOCK was still held, and whose failure quietly stamped the run's log row with no
        # season at all, turning head-to-head's season filter off for that row ever after.
        # Nothing else writes these while a run holds the lock, so one read is also one answer.
        #
        # BEFORE THE GUARD, because the guard needs the season. It used to sit after, so the
        # guard was handed None, could not tell last season's leftover bracket from this
        # season's result, and refused a 28-day run in a season that had not started.
        try:
            settings = st.get_settings()
        except Exception as exc:
            emit("start", f"could not read the settings ({exc}); using defaults")
            settings = {}
        season_now = int(settings.get("current_season", cfg.START_YEAR) or cfg.START_YEAR)

        if not dry_run:
            for key in keys:
                _refuse_to_cross_the_season([key], day_counts[key], emit, allow_season_end=allow_season_end,
                                            season=season_now)

        if FBPB3.is_running():
            emit("backup", "closing a stray FBPB3 first")
            FBPB3.kill()

        # A run that died with the machine leaves work in the saves that the store was never
        # told about. Running again on top of that is the one way to double somebody's points,
        # so it stops here - a dry run is exempt, because it writes nothing and is a reasonable
        # thing to do while working out what happened.
        if not dry_run:
            stale = interrupted_run()
            if stale:
                raise RuntimeError(describe_interruption(stale))
            _mark_running(keys, days, season_now)
            marked = True
            if days_by_league:
                _update_marker(lambda state: state.update(days_by_league=day_counts))
            # One message for this run. All HTTP happens on its worker, never in a game click.
            status = SimStatus(day_counts if days_by_league else days, keys).start()

        # ---- 1. apply everything owed, per league, before the game opens ----------------------
        for key in keys:
            spec = cfg.BY_KEY[key]
            path = ch.save_path(key)
            at("prepare", key)
            emit("backup", f"backing up {spec.save_name}", key)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = BACKUPS / f"{stamp}-{spec.save_name}"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest / "league.dat")
            if marked:
                _update_marker(lambda state: state["backups"].update({key: str(dest / "league.dat")}))

            # The game's own AI will cut a 14-year-old for an adult free agent the first chance
            # it gets - it took 80 of our players off Prep rosters on the very first sim. Defang
            # the pool and put anyone it already took back, before anything else is applied.
            if not dry_run:
                from tools.protect_rosters import protect as _protect
                pct = at("prepare", key, .15)
                emit("apply", f"checking {spec.name} rosters before the games", key, pct=pct)
                # The guard repairs the roster; it must never be able to stop the week from
                # running. A sim that skips the repair is recoverable, a sim that refuses to
                # start because the repair threw is not.
                try:
                    guard = _protect(key, store_characters=st.characters())
                except Exception as exc:
                    emit("apply", f"roster guard failed for {key} ({exc}); continuing", key)
                    guard = {}
                if guard.get("released") or guard.get("signed"):
                    pct = at("prepare", key, .75)
                    emit("apply", f'AI roster churn undone: {guard["released"]} out, '
                                  f'{guard["signed"]} of ours back in', key, pct=pct)

            # Opened AFTER the guard, which writes the file itself - an object opened before it
            # would hold a stale copy and overwrite the repair on save.
            emit("apply", f"applying pending work to {spec.name}", key)
            L = LeagueDat(path)
            if marked:
                _update_marker(lambda state: state.update(store_writes_started=True))
            traded = _sync_teams(key, L, st, lambda m: emit("apply", m, key))
            if traded:
                emit("apply", f"{traded} character(s) had been traded since last week", key)
            dressed = _dress_characters(key, L, st, lambda m: emit("apply", m, key))
            activated, expect_a, activations = _activate_pending(
                key, L, st, lambda m: emit("apply", m, key), season=season_now)
            applied, expect_b = _apply_requests(key, L, st, lambda m: emit("apply", m, key))
            if dry_run:
                emit("apply", f"dry run: {len(activated)} characters, {len(applied)} requests "
                              f"would be written to {spec.save_name}", key)
                continue
            if activated or applied or dressed:
                # Listed as at-risk BEFORE the save is written, not after: a crash between the
                # write and the store is the whole point of this, and a marker updated after
                # the write would miss exactly that window.
                _mark_saved(key, dest)
                try:
                    ch.commit(L, expect_a + expect_b)
                except Exception as exc:
                    shutil.copy2(dest / "league.dat", path)
                    raise RuntimeError(f"{key}: write check failed, save restored - {exc}") from exc
                # Only now: the save demonstrably holds these characters. If the store refuses
                # here the two would disagree the other way round, so the save goes back and the
                # run stops - before the game is ever opened - naming anyone already written.
                written = []
                for write in activations:
                    try:
                        write()
                    except Exception as exc:
                        shutil.copy2(dest / "league.dat", path)
                        raise RuntimeError(
                            f"{key}: the save was written but the store refused ({exc}); save "
                            f"restored. Already marked active in the store: "
                            f"{', '.join(written) or 'none'}") from exc
                    written.append(f'{activated[len(written)]["first_name"]} '
                                   f'{activated[len(written)]["last_name"]}')
                st.mark_applied(applied)
                # The store agrees now, so this league comes back off the at-risk list. The
                # window that just closed is the one between ch.commit and this line.
                _mark_synced(key)
                result["applied"] += len(applied)
                result["activated"] += len(activated)
                emit("apply", f"{len(activated)} characters in, {len(applied)} spends applied", key)
            else:
                emit("apply", "nothing pending", key)
            at("prepare", key, 1)
            # The ordinary backup predates activation and point purchases. A calendar miss must
            # preserve those store-synchronised writes while undoing only the basketball, so
            # retain the exact post-prepare file as a second recovery point.
            prepared = dest / "prepared-league.dat"
            shutil.copy2(path, prepared)
            prepared_backups[key] = prepared

        if dry_run:
            emit("done", "dry run complete - nothing was written or simmed", pct=100)
            result["ok"] = True
            return result

        # Point purchases and any store-only corrections are now in the save. Capture them with
        # the complete snapshot history before FBPB3 gets a chance to run development logic.
        from . import seasonflow
        progress_floors = {key: seasonflow.capture_character_progress(st, key) for key in keys}

        # ---- 2. drive the game ---------------------------------------------------------------
        from . import leaguenews
        news_before = {key: leaguenews.snapshot(ch.save_path(key).parent / "html") for key in keys}
        try:
            news_characters = st.characters()
        except Exception as exc:
            # News is optional reporting after the basketball; it must never stop a run.
            emit("export", f"real-player trade news unavailable ({exc}); omitting league news")
            news_characters = []
        at("load", keys[0])
        emit("sim", "starting the basketball game", keys[0])
        game = FBPB3().launch()
        for key in keys:
            spec = cfg.BY_KEY[key]
            at("load", key)
            emit("sim", f"loading {spec.save_name}", key)
            game.load_save(spec.save_name, wait=30)
            at("sim", key)
            emit("sim", f"simming {day_counts[key]} days of {spec.name}", key)

            def day_finished(day, total):
                pct = at("sim", key, day / max(1, total))
                emit("sim", f"{spec.name}: day {day} of {total} completed", key, pct=pct)

            start_date = (start_dates or {}).get(key)
            used_calendar = False
            if start_date and day_counts[key] > 1:
                emit("sim", f"{spec.name}: selecting the exact stop date on the game calendar", key)
                used_calendar = game.sim_to_date(day_counts[key], start_date,
                                                 on_day=day_finished)
                if not used_calendar:
                    emit("sim", f"{spec.name}: calendar jump reaches the playoffs; "
                          "finishing with verified daily steps", key)
            if not used_calendar:
                game.sim_days(day_counts[key], on_day=day_finished)
            at("save", key)
            emit("sim", "saving", key)
            # The path lets the driver watch the file finish instead of sleeping a fixed 15 s,
            # and turns a save that silently did not happen into an error rather than an export
            # of yesterday's league.
            game.save_game(path=ch.save_path(key))
            expected = (expected_states or {}).get(key, expected_state)
            if expected is not None:
                from .codec.league_dat import find_season_day
                stored = find_season_day(ch.save_path(key).read_bytes())
                wanted = (int(expected[0]) + day_counts[key], int(expected[1]))
                if used_calendar and stored and stored[1] == wanted[1] and stored[0] < wanted[0]:
                    # SIM TO GAME stops immediately before the selected Hot Seat team's game.
                    # That is usually one morning short of "play through this date". Finish the
                    # small remainder with the exact daily path, then trust only the saved day.
                    missing = wanted[0] - stored[0]
                    emit("sim", f"{spec.name}: fast jump stopped {missing} day(s) short; "
                         "finishing with verified daily steps", key)

                    def finish_short(day, total):
                        day_finished(day_counts[key] - missing + day, day_counts[key])

                    game.sim_days(missing, on_day=finish_short)
                    game.save_game(path=ch.save_path(key))
                    stored = find_season_day(ch.save_path(key).read_bytes())
                if used_calendar and stored and stored[1] == wanted[1] and stored[0] > wanted[0]:
                    # An idle target date makes SIM TO GAME seek the selected team's next game,
                    # which can overshoot. Undo that saved result and replay only this league
                    # from the post-prepare checkpoint with the slower exact path.
                    emit("sim", f"{spec.name}: fast jump passed the target; restoring the "
                         "checkpoint and replaying exact daily steps", key)
                    active_game, game = game, None
                    _restore_calendar_checkpoint(
                        active_game, prepared_backups.get(key), ch.save_path(key))
                    game = FBPB3().launch()
                    game.load_save(spec.save_name, wait=30)
                    game.sim_days(day_counts[key], on_day=day_finished)
                    game.save_game(path=ch.save_path(key))
                    stored = find_season_day(ch.save_path(key).read_bytes())
                if stored != wanted:
                    # The wrong date is already on disk because league.dat exposes its day only
                    # after SAVE. Close FBPB before replacing a file it owns, then restore the
                    # post-prepare checkpoint so applied requests and activations stay aligned
                    # with the store while none of this league's accidental basketball survives.
                    active_game, game = game, None
                    try:
                        _restore_calendar_checkpoint(
                            active_game, prepared_backups.get(key), ch.save_path(key))
                    except Exception as restore_exc:
                        raise RuntimeError(
                            f"{spec.name}: save landed on season day {stored}, expected {wanted}; "
                            f"recovery failed ({restore_exc})") from restore_exc
                    raise RuntimeError(
                        f"{spec.name}: save landed on season day {stored}, expected {wanted}; "
                        "the league was restored to its pre-sim checkpoint and export/publish "
                        "were stopped")
                if used_calendar:
                    day_finished(day_counts[key], day_counts[key])
            at("export", key)
            emit("export", f"writing {spec.name} pages", key)
            # old_boxes=True writes the BOX SCORES for the games in the export's window, so the
            # schedule's links lead somewhere instead of 404ing. The control is a DATE dropdown -
            # "Yes" was never one of its options, which is why every earlier test of this
            # question set it to nothing and reported that FBPB3 cannot write box scores at all.
            # The window reaches about a month back from the save's own date, so a weekly run
            # covers its own games comfortably and a season left unexported cannot be recovered.
            out = game.html_output(spec.save_name, old_boxes=True)
            emit("export", f"{len(list(out.rglob('*.htm')))} pages", key)
            league_news_characters = [c for c in news_characters if c.get("league") == key]
            new_business = leaguenews.real_player_trades(
                leaguenews.new_rows(news_before[key], leaguenews.snapshot(out)),
                league_news_characters)
            if new_business:
                result["news"][key] = new_business
                emit("export", f"{len(new_business)} real-player trade(s) recorded", key)
            # The MDB is where the per-game and season-total tables come from, and it is also
            # the only chance to write a career down before the save retires the man: SeasonStats
            # is rebuilt from scratch every export, so whatever is not captured here is gone.
            #
            # NOT "about 7 s a league" - that was measured when the leagues were new and it is
            # what made a 180 s budget look generous. Measured 2026-09-20: prep 8 s, college 9 s,
            # PRO 211 s, because pro has twenty teams and a 9.8 MB database. It grows with every
            # season played, so this is the line to watch as the universe ages.
            #
            # Done while the save is still loaded, and never fatal: the
            # week's basketball is already saved and exported by this point, and head-to-head
            # going stale is not worth losing it.
            # Midseason MDBs exist for character game logs. A league with no live character has
            # nobody whose head-to-head or career page can change, while its current standings,
            # leaders and player pages already come from the fresh HTML above. Exporting those
            # empty leagues cost minutes and, in this run, held a completed sim for nine more.
            # Season-ending runs still archive everyone before retirements can remove them.
            try:
                league_characters = st.characters(league=key)
            except TypeError:  # small test/local stores may only expose characters()
                league_characters = [c for c in st.characters() if c.get("league") == key]
            has_characters = any(c.get("status") in ("active", "declared")
                                 for c in league_characters)
            if has_characters or allow_season_end:
                try:
                    at("mdb", key)
                    emit("export", "writing the game-by-game table", key)
                    game.output_mdb(spec.save_name, attempts=1, timeout=45, max_seconds=120)
                    fresh_mdb.add(key)
                except Exception as exc:
                    emit("export", f"no MDB for {key} ({exc}); head-to-head will not update", key)
            else:
                emit("export", "MDB deferred: no active characters; current HTML is complete", key)
        game.exit_game(save=False)
        game = None

        # ---- 2b. tidy up after the game --------------------------------------------------------
        # The guard runs before the sim so the week is played with the right rosters. It has to
        # run AFTER as well, because the AI churns during the week it just played: the first 2026
        # week took all three leagues through preseason camp and left rosters at 17-20, with 241
        # signings (all at our defang floor, so it is filling seats rather than chasing quality)
        # and fifteen of OUR reserve slots cut.
        #
        # A cut reserve slot is not cosmetic. `_activate_pending` stamps a new character onto a
        # slot the manifest says exists, then dresses him - and dress() raises "is not on a team"
        # for a slot sitting in free agency. So the next person to sign up would fail to be
        # placed, for a reason that has nothing to do with him.
        #
        # This cannot be done any earlier. The export reads the game's own memory, not the file,
        # so a codec repair before it would not reach the published pages anyway - and CONVENTIONS
        # forbids touching league.dat under a running FBPB3, which rewrites it whenever it saves.
        # The consequence is that the pages published this week still show the churn; next week's
        # export shows the repair. The SAVE is correct the moment the game closes, which is what
        # everything else depends on.
        # Wait for FBPB3 to be really gone before writing anything. exit_game ends the process
        # when the quit box never appears, and until it actually dies it still holds the league it
        # last loaded - so the first post-sim write can hit a lock the save's own retry budget
        # (about five seconds) may not outlast. The cost of losing that write is concrete: an
        # untidied save with our reserves left in free agency, which is the exact state in which
        # the next person to sign up cannot be placed.
        #
        # Costs nothing when the game has already exited, which is the normal case.
        gone = _wait_for_game_to_exit()
        if not gone:
            emit("apply", "FBPB3 is still running after 30s; tidying anyway, writes may be locked")

        for key in keys:
            at("tidy", key)
            emit("apply", f"checking {cfg.BY_KEY[key].name} rosters after the games", key)
            try:
                from tools.protect_rosters import protect as _protect
                after = _protect(key, store_characters=st.characters())
            except Exception as exc:
                emit("apply", f"post-sim tidy failed for {key} ({exc}); the week itself is fine", key)
                after = {}
            if after.get("released") or after.get("signed"):
                emit("apply", f'tidied up after the week: {after["released"]} of the AI\'s '
                              f'signings out, {after["signed"]} of ours back on a roster', key)

            # Putting them back on the roster is not the same as putting them back in the team.
            # The AI coach benches the worst man when a roster is over-full, and a character
            # built for prep IS the worst man in college or in the pros - so preseason padding
            # left two of the three rehearsal characters at Inactive -1 with no depth-chart
            # minutes and an empty game log. They had been dressed before the week; the coach
            # undressed them during it.
            #
            # _dress_characters already runs BEFORE the sim, which cannot help: by then the
            # padding has not happened yet. Running it again here means the save sits between
            # weeks with every character dressed, so a person opening the site does not find
            # their player benched, and the next week starts from a correct state rather than
            # relying on the pre-sim pass to notice.
            #
            # It cannot stop a character being benched DURING a week - only simming the
            # preseason as its own step, then guarding and dressing, then simming the week,
            # would do that. Worth doing if regular-season weeks turn out to pad too.
            try:
                # ch.save_path(key), NOT `path`. `path` is left over from the earlier per-league
                # loop and holds whichever league that loop finished on - so this opened CV_Pro
                # while re-dressing prep, found none of the prep characters, and reported "0
                # players match" for every one of them. It has therefore never worked. Worse: had
                # a name ever matched in the wrong file, the L2.save() below would have written
                # that league instead.
                L2 = LeagueDat(ch.save_path(key))
                redressed = _dress_characters(key, L2, st, lambda m: emit("apply", m, key))
                if redressed:
                    L2.save(backup_dir=BACKUPS)
                    emit("apply", f'put {len(redressed)} character(s) back in the lineup after '
                                  f'the coach benched them: {", ".join(redressed[:4])}', key)
            except Exception as exc:
                emit("apply", f"could not re-dress in {key} ({exc}); the week itself is fine", key)

            # Native development has reduced real ratings (especially Jumping) and potential
            # ceilings at season boundaries. Apply the lifetime floor before snapshots or the
            # site can record and publish the damaged sheet as if it were legitimate progress.
            repaired = seasonflow.protect_character_progress(st, key, progress_floors.get(key))
            changed = [row for row in repaired if row["changed"]]
            if changed:
                emit("apply", f"restored protected progress for {len(changed)} character(s)", key)

        # ---- 2c. where each league now stands ---------------------------------------------------
        # Said out loud every run, because the interesting transitions are invisible otherwise:
        # the regular season ending, the playoffs starting, and a champion appearing - after
        # which nothing may sim that league again until the rollover exists.
        result["stages"] = {}
        for key in keys:
            html = ch.save_path(key).parent / "html"
            try:
                blocks = _season_blocks(ch.save_path(key).parent) or []
                stage = _stage(html / "schedule.htm", sum(1 for _d, was in blocks if was))
            except Exception:
                stage = "unknown"
            champ = _champion(ch.save_path(key).parent, season_now)
            result["stages"][key] = {"stage": stage, "champion": champ}
            if champ:
                emit("done", f"{key}: {champ} have won it. The season is over here - the "
                             "rollover is not built, so this league cannot sim again yet.", key)
            elif stage == "Playoffs":
                emit("done", f"{key} is in the playoffs", key)

        # ---- 3. write down everybody's sheet ---------------------------------------------------
        # After the game has closed, never while it is open: CONVENTIONS forbids reading
        # league.dat under a running FBPB3, which rewrites it whenever it saves. Wrapped per
        # league because a missing snapshot is cosmetic and a missing publish is not.
        weeks = max(1, round(days / 7))
        season = season_now
        # The week just completed, i.e. what current_week will read once this run finishes -
        # it is bumped at the end, so the stale value would date every snapshot a week early.
        week_done = int(settings.get("current_week", 0)) + weeks
        for key in keys:
            at("snapshot", key)
            emit("snapshot", f"recording {cfg.BY_KEY[key].name} player progress", key)
            try:
                n = _snapshot_league(key, st, season, week_done,
                                     lambda m: emit("snapshot", m, key))
                result["snapshots"] += n
                if n:
                    emit("snapshot", f"{n} sheet(s) written down", key)
            except Exception as exc:
                emit("snapshot", f"no snapshots for {key}: {exc}", key)

        # ---- 4. publish and pay ---------------------------------------------------------------
        at("publish")
        emit("publish", "skinning and staging the sites")
        rows = publish(keys, fresh_mdb)
        for row in rows:
            emit("publish", f'{row["league"]}: {row["pages"]} pages', row["league"])

        # ...and then actually put it where people can see it. `publish()` only re-skins the
        # game's HTML into site/leagues/; the deploy is a separate push to the gh-pages branch.
        # Leaving that manual meant Sim Week finished, reported success, and the public site
        # still showed last week - the one part of "press the button and everything else
        # happens" that did not happen. Nobody would notice from the panel, which reports the
        # staging as "published".
        #
        # A failed push must not fail the week: the sim is done, the saves are written, and
        # every other outcome is already recorded. It is retried by simply running the publish
        # again, so say so and carry on.
        if st.get_settings().get("auto_publish", True):
            at("upload")
            emit("publish", "pushing to the public site")
            try:
                from .publish.publish import git_push
                git_push(f"Sim Week {datetime.now():%Y-%m-%d %H:%M}")
                emit("publish", "the public site is live")
            except Exception as exc:
                emit("publish", f"the site did NOT publish ({exc}); the week itself is saved")

        for key in keys:
            at("points", key)
            emit("points", f"awarding weekly points in {cfg.BY_KEY[key].name}", key)
            league_weeks = max(1, round(day_counts[key] / 7))
            n = st.grant_week_points(league=key, weeks=league_weeks)
            if n:
                emit("points", f"{league_weeks} point(s) to {n} character(s) in {key}", key)
        at("finish")
        emit("points", "updating the completed week")
        st.set_setting("current_week", week_done)

        result["ok"] = True
        emit("done", "saving the run history")
    except Exception as exc:
        result["errors"].append(str(exc))
        emit("error", str(exc))
        # A REFUSAL IS NOT A STOP. SeasonEnd means nothing ran: no backup, no save touched, no
        # click. Announcing "Sim stopped" for it told the Discord server the sim had fallen over
        # when it had simply declined to start, which is a worse lie than saying nothing - and it
        # is what fired the day this guard landed, because the test that proves the lock releases
        # calls run_sim(days=999) and SERVERPC has a webhook.
        if not dry_run and not isinstance(exc, SeasonEnd):
            # The last step is the useful part: "stopped" on its own sends somebody to the
            # server to find out what, which is exactly the trip this is meant to save.
            where = next((r["message"] for r in reversed(steps)
                          if r.get("step") not in ("error",)), "before it started")
            failure_summary = f"**Sim stopped** — {exc}\nLast step: {where}"
        raise
    finally:
        if game is not None:
            try:
                game.exit_game(save=False)
            except Exception:
                FBPB3.kill()
        _RUNNING.update(active=False)
        # started_at and season, both needed by head-to-head's arrival-day maths. `at` is
        # stamped at the END of a run, and Seasonday restarts every year while this log does
        # not - so without these two a character created mid-run loses a week, and after the
        # first rollover everybody's debut is nonsense. The season is the one read at the top of
        # the run rather than a fresh request: this is the worst possible place for a network
        # call, since the lock is still held.
        # Failed runs retain evidence even when some local cleanup succeeded.
        try:
            phases = phase_totals(steps, time.time() - started)
            if phases and not dry_run:
                emit("done", "where the time went: " + ", ".join(
                    f"{name} {secs:.0f}s" for name, secs in phases[:6]))
            recorded = st.record_run({**result, "seconds": round(time.time() - started),
                           "started_at": datetime.fromtimestamp(started, timezone.utc)
                           .isoformat(timespec="seconds"),
                           "season": season_now,
                           "phases": {name: round(secs, 1) for name, secs in phases}})
            if result["ok"] and marked:
                if recorded is None:
                    raise OSError("run history was not recorded; recovery journal retained")
                _clear_marker()
            if result["ok"] and not dry_run:
                emit("done", f"done in {round(time.time() - started)}s", pct=100)
        except Exception:
            result["ok"] = False
            failure_summary = (failure_summary or
                               "The run finished its work, but its history could not be recorded. "
                               "Check the commissioner panel before starting another run.")
            raise
        finally:
            # ALWAYS, even if the log write raised. record_run swallows OSError but not a
            # PermissionError from a held file, nor a TypeError from a hand-edited log - and a
            # leaked lock is not a lost log line, it is Sim Week AND the offseason refusing to
            # run with "another process is already simming" until somebody restarts the panel.
            try:
                if status is not None:
                    status.finish(result["ok"], failure_summary or
                                  _discord_report(steps, result, time.time() - started))
            except Exception as exc:
                print(f"Discord final status failed ({type(exc).__name__}); run is unaffected")
            finally:
                _SIM_LOCK.release()
    return result
