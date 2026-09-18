"""The offseason: everyone gets a year older, and some of them move up.

Four things happen between seasons, in this order, because each depends on the last:

1. **Growth.** Every character gets the inches his curve says he gets this year. `growth.py` owns
   the curve; this only writes the result into the save.
2. **Promotion.** A prep character who has finished his age-17 season moves to College; a college
   character who declared (or who has used up his eligibility) enters the pro draft.
3. **The draft.** The app runs it, not FBPB3 - the league files ship with the rookie draft off.
   Declared players are picked in reverse standings order and stamped onto pro rosters.
4. **Refill.** The reserve slot a departing character leaves behind is handed back its filler
   identity, so the level he left can take somebody new.

**A move between levels is not a trade.** The three leagues live in three separate saves that
cannot see each other, so a promotion is: claim a reserve slot in the destination, stamp the
character onto it carrying his ratings, and hand his old slot back. He keeps who he is; FBPB3's own
per-league stat history stays behind, which is why the career page stitches the levels together
from our side.

Nothing here touches a save until every change for that save has been staged and verified, and a
failed verification restores the backup - the same rule Sim Week runs under.
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import datetime
from pathlib import Path

from . import characters as ch
from . import growth
from .codec.league_dat import POTENTIALS, RATINGS, LeagueDat
from .universe import config as cfg

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
MANIFEST = ROOT / "universe" / "manifest.json"

NEXT_LEVEL = {"prep": "college", "college": "pro"}
# A prep player is done after his age-17 season; college eligibility runs four years.
PREP_LAST_AGE = 17
COLLEGE_MAX_YEARS = 4


class OffseasonError(Exception):
    pass


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def age_of(character, season):
    dob = character.get("game_dob") or (character.get("claimed_slot") or {}).get("dob")
    if not dob:
        return growth.START_AGE if hasattr(growth, "START_AGE") else 14
    try:
        born = int(str(dob).split("/")[-1]) if "/" in str(dob) else int(str(dob)[:4])
    except ValueError:
        return 14
    return int(season) - born


# ---- 1. growth ------------------------------------------------------------------------------
def apply_growth(league_key, characters, season, log=print, dry_run=False):
    """Write this year's inches into the save for every character in this league."""
    live = [c for c in characters if c.get("league") == league_key and c.get("status") == "active"]
    if not live:
        return [], []
    path = ch.save_path(league_key)
    L = LeagueDat(path)
    grown, expect = [], []
    for c in live:
        genes = (c.get("traits") or {}).get("height_genes")
        if genes is None:
            continue
        age = age_of(c, season)
        name = f'{c["first_name"]} {c["last_name"]}'
        try:
            pl = L.find(name, c.get("game_dob"))
        except Exception as exc:
            log(f"   ! {name}: {exc}")
            continue
        inches = growth.grew_this_offseason(c["id"], int(c["height_inches"]), int(genes), age)
        if inches <= 0:
            continue
        now = pl.values["Height"] + inches
        if not dry_run:
            L.set(pl, "Height", now)
        expect.append((name, c.get("game_dob"), {"Height": now}))
        grown.append({"character": c, "inches": inches, "height": now})
        log(f'   {name} grew {inches}" to {now // 12}\'{now % 12}" at {age}')
    if grown and not dry_run:
        ch.commit(L, expect)
    return grown, expect


# ---- 2. who moves ---------------------------------------------------------------------------
def movers(characters, season):
    """Characters who have outgrown their level, split by where they are going."""
    out = {"college": [], "draft": [], "stay": []}
    for c in characters:
        if c.get("status") != "active":
            continue
        age = age_of(c, season)
        if c.get("league") == "prep":
            (out["college"] if age >= PREP_LAST_AGE else out["stay"]).append(c)
        elif c.get("league") == "college":
            years = int(c.get("college_years") or 0) + 1
            if c.get("declared") or years >= COLLEGE_MAX_YEARS:
                out["draft"].append(c)
            else:
                out["stay"].append(c)
        else:
            out["stay"].append(c)
    return out


def _ratings_of(pl):
    return ({f: pl.values[f] for f in RATINGS}, {f: pl.values[f] for f in POTENTIALS})


def promote(character, to_league, store, log=print, dry_run=False):
    """Move one character up a level, carrying his ratings, and hand back his old slot."""
    from_league = character["league"]
    src_path, dst_path = ch.save_path(from_league), ch.save_path(to_league)
    name = f'{character["first_name"]} {character["last_name"]}'

    src = LeagueDat(src_path)
    pl = src.find(name, character.get("game_dob"))
    ratings, potentials = _ratings_of(pl)
    height = pl.values["Height"]

    # a free slot at the new level
    active = [c for c in store.characters(league=to_league) if c.get("status") == "active"]
    taken = [{"name": (c.get("claimed_slot") or {}).get("name"),
              "dob": (c.get("claimed_slot") or {}).get("dob")} for c in active]
    slots = ch.free_slots(_manifest(), to_league, taken)
    slot = ch.pick_slot(slots, character.get("position"))
    if slot is None:
        raise OffseasonError(f"no reserve slot left in {to_league} for {name}")

    if dry_run:
        log(f"   would move {name}: {from_league} -> {to_league} ({slot.team})")
        return {"character": character, "to": to_league, "team": slot.team, "slot": slot.as_json()}

    dst = LeagueDat(dst_path)
    ch.stamp_character(dst, slot, {
        "first_name": character["first_name"], "last_name": character["last_name"],
        "dob": character.get("game_dob") or slot.dob, "height_inches": height,
        "position": character.get("position"), "ratings": ratings, "potentials": potentials,
    })
    ch.commit(dst, [(name, character.get("game_dob") or slot.dob, {"Height": height})])

    refill(from_league, character, log=log)
    store.activate_character(character["id"], to_league, slot.team, slot.as_json(),
                             character.get("game_dob"))
    log(f"   {name}: {from_league} -> {to_league}, {slot.team}")
    return {"character": character, "to": to_league, "team": slot.team, "slot": slot.as_json()}


# ---- 4. give the slot back ------------------------------------------------------------------
def refill(league_key, character, log=print):
    """Hand a departing character's reserve slot back its original filler identity.

    Without this the level he left keeps a slot that looks taken forever, and the ceiling on
    concurrent characters ratchets down one person at a time.
    """
    slot = character.get("claimed_slot") or {}
    if not slot:
        return False
    path = ch.save_path(league_key)
    L = LeagueDat(path)
    name = f'{character["first_name"]} {character["last_name"]}'
    try:
        pl = L.find(name, character.get("game_dob") or slot.get("dob"))
    except Exception as exc:
        log(f"   ! could not find {name} in {league_key} to refill his slot: {exc}")
        return False

    original = next((r for r in _manifest()["players"]
                     if r["league"] == league_key and r["name"] == slot.get("name")
                     and r["dob"] == slot.get("dob")), None)
    if original is None:
        log(f"   ! {slot.get('name')} is not in the manifest; slot left as is")
        return False

    first, _, last = original["name"].partition(" ")
    L.rename(pl, first, last)
    pl = L.find(original["name"])
    month, day, year = (int(v) for v in original["dob"].split("/"))
    L.set(pl, "BirthMonth", month)
    L.set(pl, "BirthDay", day)
    L.set(pl, "BirthYear", year)
    rng = random.Random(f'{original["name"]}|{original["dob"]}')
    for field in RATINGS:
        if field in ("3pUsage", "Fouling", "Stamina"):
            continue
        L.set(pl, field, rng.randint(3, 12))
    for field in POTENTIALS:
        L.set(pl, field, rng.randint(10, 25))
    L.save(backup_dir=BACKUPS)
    log(f'   slot {original["name"]} is free again in {league_key}')
    return True


# ---- 3. the draft ---------------------------------------------------------------------------
def draft_order(pro_save=None):
    """Reverse order of last season's pro standings; falls back to the config's team order.

    FBPB3's own rookie draft is off in the league files, so the order has to come from us.
    """
    spec = cfg.BY_KEY["pro"]
    site = ROOT / "site" / "leagues" / "pro" / "standings.htm"
    if not site.exists():
        return [t.abbrev for t in spec.teams]
    import re
    text = site.read_text(encoding="latin-1", errors="replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", c)).replace("&nbsp;", "").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        cells = [c for c in cells if c]
        if len(cells) >= 3 and cells[1].isdigit() and cells[2].isdigit():
            nick = cells[0]
            team = next((t for t in spec.teams if t.nickname == nick or t.city == nick), None)
            if team:
                rows.append((int(cells[1]), int(cells[2]), team.abbrev))
    if not rows:
        return [t.abbrev for t in spec.teams]
    rows.sort(key=lambda r: (r[0] / max(1, r[0] + r[1]), r[0]))   # worst record picks first
    return [abbrev for _, _, abbrev in rows]


def run_draft(declared, store, log=print, dry_run=False):
    """Assign declared players to pro teams, worst record first. Returns the picks."""
    if not declared:
        return []
    order = draft_order()
    ranked = sorted(declared, key=lambda c: -_promise(c))
    picks = []
    for i, character in enumerate(ranked):
        team = order[i % len(order)]
        picks.append({"round": i // len(order) + 1, "pick": i + 1,
                      "team": team, "character": character})
    log(f"   draft order starts {', '.join(order[:4])}")
    for p in picks:
        c = p["character"]
        log(f'   #{p["pick"]:2} {p["team"]}  {c["first_name"]} {c["last_name"]}')
        if not dry_run:
            moved = promote(c, "pro", store, log=log)
            p["slot"] = moved["slot"]
    return picks


def _promise(character):
    """A rough ranking for draft night: what he is now, weighted by where he can still get to."""
    ratings = character.get("ratings") or {}
    if not ratings:
        return 0
    now = sum(ratings.get(f, 0) for f in RATINGS) / max(1, len(RATINGS))
    ceiling = sum((character.get("potentials") or {}).get(f, 0) for f in POTENTIALS)
    return now * 2 + ceiling / max(1, len(POTENTIALS))


# ---- the whole thing ------------------------------------------------------------------------
def run_offseason(store, season=None, log=print, dry_run=False):
    """Grow everyone, move whoever has outgrown his level, run the draft, free the slots."""
    settings = store.get_settings()
    season = int(season or settings.get("current_season", cfg.START_YEAR))
    characters = store.characters()
    result = {"season": season, "grown": 0, "promoted": [], "drafted": [], "dry_run": dry_run}

    for key in ("prep", "college", "pro"):
        path = ch.save_path(key)
        if not path.exists():
            continue
        if not dry_run:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = BACKUPS / f"{stamp}-offseason-{cfg.BY_KEY[key].save_name}"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest / "league.dat")
        log(f"{key}: growth")
        grown, _ = apply_growth(key, characters, season, log=log, dry_run=dry_run)
        result["grown"] += len(grown)

    moving = movers(characters, season)
    log(f"moving up: {len(moving['college'])} to college, {len(moving['draft'])} into the draft")
    for c in moving["college"]:
        try:
            result["promoted"].append(promote(c, "college", store, log=log, dry_run=dry_run))
        except OffseasonError as exc:
            log(f"   ! {exc}")
    result["drafted"] = run_draft(moving["draft"], store, log=log, dry_run=dry_run)

    if not dry_run:
        store.set_setting("current_season", season + 1)
        store.set_setting("current_week", 0)
    log(f"offseason complete: {result['grown']} grew, {len(result['promoted'])} promoted, "
        f"{len(result['drafted'])} drafted")
    return result
