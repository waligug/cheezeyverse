"""The offseason: everyone gets a year older, some of them move up, and some are done.

Five things happen between seasons, in this order, because each depends on the last:

0. **Who is still here.** Every active character is looked for in his own save before anything
   pays or moves anybody. A career that is over ends here, and a character FBPB3 has aged out
   of the file is found out here rather than surfacing later as an unexplained growth error.
1. **Growth.** Every character gets the inches his curve says he gets this year. `growth.py` owns
   the curve; this only writes the result into the save.
2. **Promotion.** A prep character who has finished his age-17 season moves to College; a college
   character who declared (or who has used up his eligibility) enters the pro draft.
3. **The draft.** The app runs it, not FBPB3 - the league files ship with the rookie draft off.
   Declared players are picked in reverse standings order and stamped onto pro rosters.
4. **Refill.** The reserve slot a departing character leaves behind - promoted, drafted or
   retired - is handed back its filler identity, so the level he left can take somebody new.

**A character holds his reserve row for exactly as long as `claimed_slot` is set on him.** That
is the one rule the slot bookkeeping runs on: the store clears it only once the row has really
been renamed back, so "free" always means a row that exists and answers to its manifest name.
`tools/protect_rosters.py` reads the same field and would release a refilled row as an intruder
if a character who no longer holds it still claimed it.

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
from . import seasonbonus
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

# ---- when a career ends ---------------------------------------------------------------------
# Only the pros retire. Prep and College are levels a character is promoted OUT of, so an
# eighteen year old still in prep is a promotion bug, not a retiree.
#
# The age cap is the backstop, not the rule. 34 is the oldest a generated pro filler is
# (`universe/config.py`), so from the season he turns 35 a character is older than anybody the
# universe made, and from there the decline test asks the better question: is he still the
# player he was? That test needs recorded history, which only exists from the first Sim Week
# after `rating_snapshots` was added - so the cap is what guarantees that a career ends at all
# for a veteran with nothing written down.
PRO_DECLINE_AGE = 35
PRO_RETIREMENT_AGE = 38
# How far below his best recorded sheet counts as done. A tenth of the whole eighteen-rating
# average is a large, unmistakable fall; anything tighter and ordinary wobble ends careers.
DECLINE_DROP = 0.10

# ---- conversion -----------------------------------------------------------------------------
# Moving up a level always costs something. The same skill is worth less against bigger, older,
# better opposition, and a rating in FBPB3 is relative to the league he is in. So a promotion
# carries ratings across at less than face value.
#
# Going up EARLY costs more, and that is the whole trade: declare after one college year and you
# start earning pro points three years sooner, but you arrive rawer than you left. Potentials are
# never touched - the ceiling is who he can still become, and leaving early must not close it.
# The lost points are earnable again; the lost years are not.
LEVEL_CONVERSION = {"college": 0.97, "pro": 0.94}
EARLY_PENALTY_PER_YEAR = 0.07     # each year skipped takes another 7% off
EARLY_PENALTY_MAX = 0.24          # never worse than a 24% haircut
# Staying pays in points, leaving pays in time. Without this the maths made declaring after one
# year strictly best by about 34 points over a career, which is not a choice, it is an answer.
# A completed college season is worth this on top of the usual offseason lump.
COLLEGE_DEVELOPMENT_BONUS = 12
# Below this a rating is too low for a percentage to mean anything; leave it alone.
CONVERSION_FLOOR = 8


class OffseasonError(Exception):
    pass


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def age_of(character, season):
    dob = ch.codec_dob(character.get("game_dob") or (character.get("claimed_slot") or {}).get("dob"))
    if not dob:
        return growth.START_AGE if hasattr(growth, "START_AGE") else 14
    try:
        born = int(str(dob).split("/")[-1]) if "/" in str(dob) else int(str(dob)[:4])
    except ValueError:
        return 14
    return int(season) - born


# ---- 0. who is still here ---------------------------------------------------------------------
def _locate(L, name, dob):
    """Find a character's record, and say what its absence means.

    This is the distinction the whole retirement path turns on, because a career FBPB3 has
    ended and a record we simply cannot match look identical from the store's side, and
    guessing wrong either loses a career or strands a slot forever.

    `LeagueDat.find` raises the same error for nobody and for two people, and CONVENTIONS
    records that duplicate names exist in these saves, so the matches are counted here instead:

    * ``here``      - exactly one record at the birthday we hold. Nothing to see.
    * ``moved``     - the name is there under a different birthday. FBPB3 rewrites a DOB to
                      12/1/<year> at season rollover, so this is drift, not death.
    * ``ambiguous`` - more than one record answers to the name. He is present but unidentifiable;
                      that is a failure to report, never a reason to end a career.
    * ``gone``      - the name is nowhere in a save that parsed perfectly well. The game has
                      aged him out, and there is no row left to grow, promote or hand back.
    """
    exact = [p for p in L.players if p.name == name and (dob is None or p.dob == dob)]
    if len(exact) == 1:
        return exact[0], "here"
    by_name = [p for p in L.players if p.name == name]
    if not by_name:
        return None, "gone"
    if len(by_name) == 1:
        return by_name[0], "moved"
    return None, "ambiguous"


def _mean(values, fields=RATINGS):
    got = [values[f] for f in fields if f in values]
    return sum(got) / len(got) if got else 0.0


def declining(store, character, live_mean, season):
    """How far he has fallen from his best recorded sheet, or None if he has not.

    The peak has to come from an EARLIER season than the one being judged. Ratings wobble
    week to week and FBPB3 runs its own progression on top of ours, so a peak set inside the
    season we are deciding proves nothing; requiring a previous one makes this a career arc
    rather than a bad fortnight.
    """
    if not hasattr(store, "snapshots"):
        return None
    try:
        rows = store.snapshots(character_id=character["id"])
    except Exception:
        return None                       # no history is not evidence of decline
    best = None
    for row in rows or []:
        mean = _mean(row.get("ratings") or {})
        if mean > 0 and (best is None or mean > best[0]):
            best = (mean, int(row.get("season") or 0))
    if best is None or best[1] >= int(season):
        return None
    if live_mean > best[0] * (1 - DECLINE_DROP):
        return None
    return {"peak": round(best[0], 1), "peak_season": best[1], "now": round(live_mean, 1)}


def retirement_for(character, pl, season, store):
    """Why this career ends now, in one line, or None if it does not end."""
    if character.get("league") != "pro":
        return None
    age = age_of(character, season)
    if age >= PRO_RETIREMENT_AGE:
        return f"aged out at {age}"
    if age < PRO_DECLINE_AGE:
        return None
    fall = declining(store, character, _mean(pl.values), season)
    if fall is None:
        return None
    return (f'declining at {age}: his sheet averages {fall["now"]}, '
            f'down from {fall["peak"]} in {fall["peak_season"]}')


def retire(character, season, reason, store, log=print, dry_run=False, slot_exists=True):
    """End one career, and give the level its reserve slot back.

    The refill is why this is not a one-line status update. A slot that is never handed back
    looks taken forever and the ceiling on concurrent characters ratchets down one person at a
    time - the same bug a promotion without a refill causes, which has already bitten this
    project twice.

    `slot_exists` is False when the game deleted the record itself. There is nothing left to
    rename, so his claim stays on him: better a slot that is honestly unavailable than one
    offered to somebody who then cannot be stamped into a row that is not in the file.
    """
    name = f'{character["first_name"]} {character["last_name"]}'
    row = {"character": character, "league": character.get("league"), "season": season,
           "reason": reason, "slot_refilled": False}
    if dry_run:
        log(f"   would retire {name}: {reason}")
        return {**row, "dry_run": True}

    if slot_exists:
        row["slot_refilled"] = refill(character["league"], character, log=log)
    # Close the open level row before retiring him. Promotion closes the level a
    # character is leaving, retirement did not - so a retired career page read as
    # still in progress for ever: to_season null, how_it_ended null, on a man who
    # had aged out four seasons earlier.
    if hasattr(store, "record_level"):
        try:
            history = list(character.get("level_history") or [])
            for row in history:
                if row.get("to_season") is None:
                    row["to_season"] = season
                    row["how_it_ended"] = reason
            if history:
                store.set_character_field(character["id"], "level_history", history)
        except Exception as exc:
            log(f"   (could not close the career of {character['first_name']}: {exc})")
    store.retire_character(character["id"], season, reason, release_slot=row["slot_refilled"])
    log(f"   {name} retired: {reason}")
    if slot_exists and not row["slot_refilled"]:
        log(f"   ! {name}'s slot did not come back; run tools/protect_rosters.py")
    elif not slot_exists:
        log(f'   ! {name}\'s row is gone from the {character.get("league")} save, so that slot '
            "cannot be handed to anybody until the save is repaired")
    return row


def run_retirements(characters, store, season, log=print, dry_run=False):
    """End the careers that are over, and notice the ones the game ended for us.

    First, before anything is grown, moved or paid: a retiree must not be given a year's
    inches, an offseason lump sum or a promotion out of a league he has left, and a character
    the game has already deleted would otherwise reappear further down as an unexplained
    codec error with no decision attached to it.
    """
    retired, failed = [], []
    for key in ("prep", "college", "pro"):
        live = [c for c in characters
                if c.get("league") == key and c.get("status") == "active"]
        if not live:
            continue
        try:
            L = LeagueDat(ch.save_path(key))
        except Exception as exc:
            # A save that will not open is our problem, not a career-ending event. "The game
            # retired him" here would end every career in the league on one bad read.
            log(f"   ! cannot read the {key} save, so nobody in it is judged: {exc}")
            failed.append({"character": None, "stage": "retire", "league": key,
                           "error": str(exc)})
            continue
        for c in live:
            name = f'{c["first_name"]} {c["last_name"]}'
            try:
                dob = ch.codec_dob(c.get("game_dob") or (c.get("claimed_slot") or {}).get("dob"))
                pl, state = _locate(L, name, dob)
                if state == "ambiguous":
                    raise OffseasonError(f"more than one record in the {key} save answers to "
                                         f"{name!r}; leaving him alone")
                if state == "gone":
                    retired.append(retire(
                        c, season,
                        f"retired by the game: no record left in the "
                        f"{cfg.BY_KEY[key].save_name} save",
                        store, log=log, dry_run=dry_run, slot_exists=False))
                    continue
                if state == "moved":
                    log(f"   {name}: the game moved his birthday to {pl.dob}")
                    # Corrected in hand whatever the store can persist: `refill` looks him up
                    # by this birthday moments later and would not find him under the old one.
                    c["game_dob"] = pl.dob
                    if not dry_run and hasattr(store, "set_character_field"):
                        store.set_character_field(c["id"], "game_dob", pl.dob)
                reason = retirement_for(c, pl, season, store)
                if reason:
                    retired.append(retire(c, season, reason, store, log=log, dry_run=dry_run))
            except Exception as exc:
                log(f"   ! {name}: {exc}")
                failed.append({"character": c, "stage": "retire", "error": str(exc)})
    return retired, failed


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
            pl = L.find(name, ch.codec_dob(c.get("game_dob")))
        except Exception as exc:
            log(f"   ! {name}: {exc}")
            continue
        inches = growth.grew_this_offseason(c["id"], int(c["height_inches"]), int(genes), age)
        if inches <= 0:
            continue
        now = pl.values["Height"] + inches
        if not dry_run:
            L.set(pl, "Height", now)
        expect.append((name, ch.codec_dob(c.get("game_dob")), {"Height": now}))
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


def years_early(character, to_league, season):
    """How many years ahead of the normal path this move is. 0 when he is on schedule."""
    if to_league != "pro":
        return 0
    used = int(character.get("college_years") or 0) + 1
    return max(0, COLLEGE_MAX_YEARS - used)


def conversion_for(character, to_league, season):
    """The multiplier his ratings carry across at, and why."""
    base = LEVEL_CONVERSION.get(to_league, 1.0)
    early = years_early(character, to_league, season)
    penalty = min(EARLY_PENALTY_MAX, early * EARLY_PENALTY_PER_YEAR)
    return {"factor": round(base - penalty, 4), "base": base, "early_years": early,
            "early_penalty": round(penalty, 4)}


def convert(ratings, factor):
    """Apply the conversion. Never moves a rating that is already near the floor."""
    out = {}
    for field, value in ratings.items():
        out[field] = value if value <= CONVERSION_FLOOR else max(
            CONVERSION_FLOOR, int(round(value * factor)))
    return out


def promote(character, to_league, store, log=print, dry_run=False, how="promoted", season=None,
            team=None):
    """Move one character up a level, carrying his ratings, and hand back his old slot."""
    from_league = character["league"]
    src_path, dst_path = ch.save_path(from_league), ch.save_path(to_league)
    name = f'{character["first_name"]} {character["last_name"]}'

    src = LeagueDat(src_path)
    pl = src.find(name, ch.codec_dob(character.get("game_dob")))
    ratings, potentials = _ratings_of(pl)
    height = pl.values["Height"]

    conv = conversion_for(character, to_league, None)
    if conv["factor"] < 1.0:
        before = sum(ratings.values())
        ratings = convert(ratings, conv["factor"])
        lost = before - sum(ratings.values())
        note = f' ({conv["early_years"]} year(s) early)' if conv["early_years"] else ""
        log(f'   {name}: carries {int(conv["factor"] * 100)}% across{note}, -{lost} rating points')

    # A free slot at the new level. Held by whoever still claims it, whatever his status: a
    # retired character whose row the game deleted keeps his claim precisely so that dead slot
    # is never handed to anybody.
    holders = [c for c in store.characters(league=to_league) if c.get("claimed_slot")]
    taken = [{"name": c["claimed_slot"].get("name"),
              "dob": c["claimed_slot"].get("dob")} for c in holders]
    slots = ch.free_slots(_manifest(), to_league, taken)
    # `team` is the drafting team when this is a draft pick: a player drafted by STL should
    # join STL if STL has a free reserve slot, not simply the first vacancy in the league.
    slot = ch.pick_slot(slots, character.get("position"), team=team)
    if slot is None:
        raise OffseasonError(f"no reserve slot left in {to_league} for {name}")

    if dry_run:
        log(f"   would move {name}: {from_league} -> {to_league} ({slot.team})")
        return {"character": character, "to": to_league, "team": slot.team,
                "slot": slot.as_json(), "conversion": conv}

    dst = LeagueDat(dst_path)
    ch.stamp_character(dst, slot, {
        "first_name": character["first_name"], "last_name": character["last_name"],
        "dob": ch.codec_dob(character.get("game_dob") or slot.dob), "height_inches": height,
        "position": character.get("position"), "ratings": ratings, "potentials": potentials,
    })
    ch.commit(dst, [(name, ch.codec_dob(character.get("game_dob") or slot.dob), {"Height": height})])

    refill(from_league, character, log=log)
    store.activate_character(character["id"], to_league, slot.team, slot.as_json(),
                             ch.codec_dob(character.get("game_dob")))

    # Write the CONVERTED sheet back. The save now holds the reduced ratings - moving up costs
    # 3% to college and 6% to the pros - while characters.ratings still held what he had at the
    # old level until the next Sim Week happened to overwrite it from the save.
    #
    # That gap is not cosmetic. The website reads characters.ratings, so his page showed
    # ratings he no longer has; and the database prices an upgrade from that same column, so a
    # point bought in that window is priced off a number the save disagrees with. The offseason
    # is exactly when somebody looks at their player, which makes it the worst possible moment
    # for the two to disagree.
    if hasattr(store, "set_character_field"):
        try:
            store.set_character_field(character["id"], "ratings", ratings)
            store.set_character_field(character["id"], "potentials",
                                      ch.store_potentials({**potentials}))
        except Exception as exc:
            log(f"   (could not write {name}'s converted sheet back: {exc})")
    if hasattr(store, "record_level"):
        try:
            placed = LeagueDat(dst_path).find(name, ch.codec_dob(character.get("game_dob") or slot.dob))
            store.record_level(character["id"], {
                "level": to_league, "team_abbrev": slot.team, "player_id": placed.id,
                "from_season": season, "to_season": None,
                "how_it_started": how, "how_it_ended": None,
                "carried": conv["factor"], "years_early": conv["early_years"],
            })
        except Exception as exc:
            log(f"   (could not record the level for {name}: {exc})")
    log(f"   {name}: {from_league} -> {to_league}, {slot.team}")
    return {"character": character, "to": to_league, "team": slot.team,
            "slot": slot.as_json(), "conversion": conv}


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
        pl = L.find(name, ch.codec_dob(character.get("game_dob") or slot.get("dob")))
    except Exception as exc:
        log(f"   ! could not find {name} in {league_key} to refill his slot: {exc}")
        return False

    original = next((r for r in _manifest()["players"]
                     if r["league"] == league_key and r["name"] == slot.get("name")
                     and r["dob"] == slot.get("dob")), None)
    if original is None:
        log(f"   ! {slot.get('name')} is not in the manifest; slot left as is")
        return False

    ch.reset_reserve(L, pl, original)
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


def run_draft(declared, store, log=print, dry_run=False, season=None):
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
            try:
                how = f'drafted #{p["pick"]} by {p["team"]}'
                moved = promote(c, "pro", store, log=log, how=how, season=season,
                                team=p["team"])
                if moved["slot"].get("team") != p["team"]:
                    # Not fatal - a roster with no free reserve row cannot take him and
                    # anywhere in the league is better than nowhere - but it must be said,
                    # because the career page will read "drafted by X, plays for Y".
                    log(f'   ! {p["team"]} had no free slot; '
                        f'{c["first_name"]} goes to {moved["slot"].get("team")} instead')
                p["slot"] = moved["slot"]
                p["conversion"] = moved["conversion"]
                if hasattr(store, "set_character_field"):
                    store.set_character_field(c["id"], "draft_pick", p["pick"])
                    store.set_character_field(c["id"], "draft_round", p["round"])
                    store.set_character_field(c["id"], "draft_season", season)
            except Exception as exc:
                log(f'   ! pick #{p["pick"]} failed: {exc}')
                p["error"] = str(exc)
    return [p for p in picks if "error" not in p] + [p for p in picks if "error" in p]


def _promise(character):
    """A rough ranking for draft night: what he is now, weighted by where he can still get to."""
    ratings = character.get("ratings") or {}
    if not ratings:
        return 0
    now = sum(ratings.get(f, 0) for f in RATINGS) / max(1, len(RATINGS))
    # A stored potential is keyed by its RATING, not by the codec's PotInside name - reading it
    # with the codec's names scored every character's ceiling as zero, which on draft night is
    # the difference between a lottery pick and going undrafted.
    pots = ch.codec_potentials(character.get("potentials"))
    ceiling = sum(pots.get(f, 0) for f in POTENTIALS)
    return now * 2 + ceiling / max(1, len(POTENTIALS))


# ---- the whole thing ------------------------------------------------------------------------
def run_offseason(store, season=None, log=print, dry_run=False, force=False):
    """Grow everyone, move whoever has outgrown his level, run the draft, free the slots.

    Refuses to run the same season twice. Everything in here is cumulative - inches, points,
    college years, promotions - so a second run silently pays everybody again and grows them
    again. It is one button, and a double click is not a reason to ruin a season.
    """
    # The offseason drives the same three saves a sim does, and both write league.dat. Share the
    # sim's lock rather than inventing a second one: two locks that do not know about each other
    # are the same as no lock at all.
    from .simweek import _SIM_LOCK, SimBusy
    if not _SIM_LOCK.acquire(blocking=False):
        raise SimBusy("a sim or another offseason is already using the saves")
    try:
        return _run_offseason(store, season=season, log=log, dry_run=dry_run, force=force)
    finally:
        _SIM_LOCK.release()


def _season_bonuses(characters, settings, log):
    """{character id: [(reason, points)]} for the season just played.

    Computed BEFORE promotions run, from the league each character actually played in. After
    them his league field says where he is going, so a kid promoted out of prep would be
    measured against college's leaders, in a season he never played there - he would rank last
    in everything and the bonus would quietly become a punishment for being good enough to move
    up.

    Never raises. Six HTML pages per league are parsed here, and a bonus that fails takes down
    an offseason that has already aged, grown, promoted and drafted everybody.
    """
    out, caches = {}, {}
    for c in characters:
        if c.get("status") != "active":
            continue
        key = c.get("league")
        if not key:
            continue
        html = ch.save_path(key).parent / "html"
        if not html.exists():
            continue
        try:
            rows = seasonbonus.for_character(
                f'{c["first_name"]} {c["last_name"]}', html, settings,
                caches.setdefault(key, {}))
        except Exception as exc:
            log(f'no season bonus for {c["first_name"]} {c["last_name"]}: {exc}')
            continue
        if rows:
            out[c["id"]] = rows
    return out


def _run_offseason(store, season=None, log=print, dry_run=False, force=False):
    settings = store.get_settings()
    season = int(season or settings.get("current_season", cfg.START_YEAR))
    done = settings.get("last_offseason")
    if done is not None and int(done) >= season and not dry_run and not force:
        raise OffseasonError(
            f"the {season} offseason has already been run (last completed: {done}). "
            "Pass force=True only if you know the first run did not finish.")
    result = {"season": season, "grown": 0, "promoted": [], "drafted": [], "retired": [],
              "failed": [], "dry_run": dry_run}

    log("who is still here")
    retired, failed = run_retirements(store.characters(), store, season, log=log, dry_run=dry_run)
    result["retired"] += retired
    result["failed"] += failed
    # Re-read rather than filter: retiring rewrote status and claimed_slot, and everything
    # below decides what to do from those two fields.
    characters = store.characters()

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

    # Before movers(), so everyone is still measured against the league he played in.
    season_bonus = _season_bonuses(characters, settings, log)
    for c in characters:
        rows = season_bonus.get(c["id"])
        if rows:
            log(f'{c["first_name"]} {c["last_name"]}: +{sum(p for _, p in rows)} season bonus ('
                + ", ".join(f"{r.split(':', 1)[-1].strip()} {p:+d}" for r, p in rows) + ")")

    moving = movers(characters, season)
    log(f"moving up: {len(moving['college'])} to college, {len(moving['draft'])} into the draft")
    # One character the codec cannot find must not abort an offseason that has already moved
    # other people - a half-run offseason is far worse than a reported failure, because the
    # save and the store disagree from then on.
    for c in moving["college"]:
        try:
            result["promoted"].append(promote(
                c, "college", store, log=log, dry_run=dry_run,
                how="aged out of prep", season=season))
        except Exception as exc:
            name = f'{c.get("first_name")} {c.get("last_name")}'
            log(f"   ! {name} did not move: {exc}")
            result["failed"].append({"character": c, "stage": "promote", "error": str(exc)})
    result["drafted"] = run_draft(moving["draft"], store, log=log, dry_run=dry_run, season=season)

    # Bank the college year BEFORE anything reads it again. This was a real bug: three places
    # read `college_years` and nothing wrote it, so every college player was permanently a
    # freshman - always three years early, so always the 73% conversion, and the four-year
    # eligibility cap could never fire.
    if not dry_run:
        for c in moving["stay"]:
            if c.get("league") == "college":
                store.set_character_field(c["id"], "college_years",
                                          int(c.get("college_years") or 0) + 1)

    # The offseason lump sum: every active character is a year older and gets paid for it,
    # and a college season that was seen through pays a development bonus on top.
    lump = int(settings.get("offseason_points", 15))
    bonus = int(settings.get("college_development_bonus", COLLEGE_DEVELOPMENT_BONUS))
    if not dry_run:
        paid = developed = earned = 0
        stayed = {c["id"] for c in moving["stay"]}
        for c in store.characters():
            if c.get("status") != "active":
                continue
            if lump:
                store.grant_points(c["id"], lump, "offseason")
                paid += 1
            if bonus and c.get("league") == "college" and c["id"] in stayed:
                store.grant_points(c["id"], bonus, "college development")
                developed += 1
            # One ledger row per component, so a friend's history says WHY he was paid rather
            # than showing one unexplained lump. Wrapped per character: a bonus that fails must
            # not cost somebody the offseason lump he has already earned.
            for why, amount in season_bonus.get(c["id"], []):
                try:
                    store.grant_points(c["id"], amount, why)
                    earned += amount
                except Exception as exc:
                    log(f'could not pay "{why}" to {c["first_name"]} {c["last_name"]}: {exc}')
        if paid:
            log(f"paid {lump} offseason point(s) to {paid} character(s)")
        if developed:
            log(f"paid {bonus} development point(s) to {developed} who stayed in college")
        if earned:
            log(f"paid {earned} season-bonus point(s) across "
                f"{len(season_bonus)} character(s)")
        result["paid"] = paid
        result["developed"] = developed
        result["season_bonus"] = earned

    if not dry_run:
        store.set_setting("last_offseason", season)
        store.set_setting("current_season", season + 1)
        store.set_setting("current_week", 0)
    log(f"offseason complete: {result['grown']} grew, {len(result['promoted'])} promoted, "
        f"{len(result['drafted'])} drafted, {len(result['retired'])} retired")
    return result
