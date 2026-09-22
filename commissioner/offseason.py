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
failed verification restores the backup - the same rule Sim Week runs under. All three saves are
copied BEFORE the first write of the whole offseason rather than per phase, because retirements
write before growth does; if anything raises, `restore_saves` puts every one of them back.

That restore is not a nicety. Growth is cumulative - it reads the height out of the save and adds
this year's inches to it - and `last_offseason` is only written at the very end. So a run that
stopped halfway used to leave the saves half-grown AND leave the obvious recovery, running it
again, free to grow those same characters a second time with nothing anywhere to detect it.
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import datetime
from pathlib import Path

from . import ageout
from . import draft
from . import draftcast
from . import points
from . import characters as ch
from . import notify
from . import seasonbonus
from . import simstatus
from . import takeaways
from .simstatus import SimStatus
from . import growth
from .codec.league_dat import POTENTIALS, RATINGS, LeagueDat
from .universe import config as cfg

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
MANIFEST = ROOT / "universe" / "manifest.json"

NEXT_LEVEL = {"prep": "college", "college": "pro"}
# College eligibility runs four years. A character promoted after his age-17 season plays them
# at 18-21, which is a year below the AI band of 19-22 that ageout generates - deliberately. He
# arrives as the youngest man in the league and has to earn his place in it, which is the same
# rule the rest of the universe runs on. It is not the age mismatch the age-out removes: that
# one was about bodies too OLD for their league still taking roster places.
#
# THE ARGUMENT THAT USED TO SIT HERE - that this must agree with ageout.AGE_CAPS "or the
# universe has two different ladders in it" - is what raised this to 18 and silently cost every
# character a season. It is deliberately not repeated: two ladders is the correct answer,
# because the rules are answering different questions. AGE_CAPS releases an AI body that has
# outgrown its league; this promotes a character who has finished his time in one. A character
# arriving in college young is the intended shape of a career, not the age mismatch the age-out
# exists to remove.
# The last age a character plays in prep. He is promoted after the season in which he reaches
# it, so 17 means four prep seasons: 14, 15, 16, 17.
#
# This was 17 by design, raised to 18 in a0e0b0d84 as a side effect of giving the AI population
# an age ladder, which silently pushed every character's promotion back a full season. It is
# NOT the same rule as the filler age-out: ageout.AGE_CAPS["prep"] = 19 releases AI bodies and
# is deliberately independent, so this can move without touching the population maths.
PREP_LAST_AGE = 17
# How long a rookie deal runs before free agency has to decide anything. Four, so a drafted
# character reaches his first real negotiation at about the age a college senior would have.
ROOKIE_YEARS = 4
# WHAT A DRAFTED CHARACTER IS PAID IN THE GAME, as distinct from the skill points he earns.
#
# The first version handed every pick the same $1,000,000 IMPORT_CONTRACT - the token the league
# was built with. Four years of it keeps him on the team that drafted him, which is the point of
# draft night, but at a tenth of what the league pays its better players that is a cage rather
# than a rookie deal: free agency is the only event that ever prices a man properly, and a token
# contract makes him skip it four years running.
#
# The numbers are the LEAGUE'S OWN, not invented. Measured on the live pro save and on the
# post-free-agency clone: cap $63,482,168, mid-level exception $5,468,453, the lowest salary the
# AI actually paid anybody $482,464, median $1,460,090. So pick 1 lands just under the mid-level
# exception and the scale tapers to about the league minimum by the end of a round - the shape a
# real rookie scale has, and well short of a cap nobody is near.
# HOW LONG THE GAME CONTRACT RUNS, which is NOT how long the points deal runs. ROOKIE_YEARS
# above is the skill-point rookie scale and stays at four. This is the deal FBPB3 sees, and
# Nate's call is that everybody starts on a short one: "It's fine if they all start on small 1
# years." One year means a drafted character expires with the rest of the league at the first
# free agency and is priced by it, instead of being held on a commissioner-chosen number for
# four years. The cost is real and worth saying out loud - he reaches free agency in the same
# offseason he was drafted, so the team that picked him is not guaranteed to keep him.
ROOKIE_GAME_YEARS = 1
ROOKIE_SALARY_TOP = 5_000_000
ROOKIE_SALARY_MIN = 500_000
ROOKIE_SALARY_PICKS = 20


def rookie_salary(pick):
    """The game salary for a draft slot, tapering from the top pick to the league minimum."""
    try:
        n = int(pick)
    except (TypeError, ValueError):
        return ROOKIE_SALARY_MIN
    if n < 1 or n >= ROOKIE_SALARY_PICKS:
        return ROOKIE_SALARY_MIN
    # Geometric rather than linear: the gap between picks 1 and 2 should be worth more than the
    # gap between 18 and 19, which is how every real scale behaves.
    span = (n - 1) / (ROOKIE_SALARY_PICKS - 1)
    return int(round(ROOKIE_SALARY_TOP * (ROOKIE_SALARY_MIN / ROOKIE_SALARY_TOP) ** span, -3))
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
# MOVING UP NO LONGER COSTS RATINGS AT COLLEGE. The 3% haircut took 22-27 rating points off each
# of the first seven, which cost about 41 points to buy back - two thirds of a whole college
# season's income spent returning to the player he already was, at exactly the moment the price
# bands start to bite. seasonbonus.promotion_grant pays him for the prep season instead, so the
# move is rewarded rather than taxed. The pro step keeps its cost: that one is a real jump, and
# a character arriving there has a college career's worth of points behind him.
LEVEL_CONVERSION = {"college": 1.00, "pro": 0.94}
EARLY_PENALTY_PER_YEAR = 0.07     # each year skipped takes another 7% off
EARLY_PENALTY_MAX = 0.24          # never worse than a 24% haircut
# Staying pays in points, leaving pays in time. Without this the maths made declaring after one
# year strictly best by about 34 points over a career, which is not a choice, it is an answer.
# A completed college season is worth this on top of the usual offseason lump.
COLLEGE_DEVELOPMENT_BONUS = 20
# Below this a rating is too low for a percentage to mean anything; leave it alone.
CONVERSION_FLOOR = 8


class OffseasonError(Exception):
    pass


class OffseasonRecoveryError(OffseasonError):
    """A partial binary write could not be restored; stop the entire transition."""


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
            for level in history:
                if level.get("to_season") is None:
                    level["to_season"] = season
                    level["how_it_ended"] = reason
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
    """Write this year's inches - and this year's weight - into the save, character by character.

    WEIGHT IS NOT CONDITIONAL ON INCHES. A sixteen-year-old who gains no height still fills out,
    because the frame curve carries a maturation term to eighteen, so the weight is recomputed
    for everybody who is still here rather than only for whoever grew. Writing it only when the
    height moved would leave a character stuck at his fourteen-year-old weight for any year he
    happened not to grow, which is most of the late ones.

    Both writes go into the SAME `expect` entry: ch.commit re-reads the file and refuses the
    whole offseason unless every value in it landed. A write outside that dict is a write
    nobody checks.
    """
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
        now = pl.values["Height"] + max(0, inches)
        # Off the height the SAVE holds, not off the model's own curve: the game is allowed to
        # have moved him, and his weight should describe the body that is actually in there.
        # weight_step, not weight_at: this year's growth lands in full, but an old wrong weight
        # is walked toward the truth rather than snapped to it. See growth.WEIGHT_CATCHUP_PER_YEAR.
        pounds = growth.weight_step(c, pl.values["Weight"], pl.values["Height"], now, age)
        wants = {}
        if inches > 0:
            wants["Height"] = now
        if pounds != pl.values["Weight"]:
            wants["Weight"] = pounds
        if not wants:
            continue
        if not dry_run:
            for field, value in wants.items():
                L.set(pl, field, value)
        expect.append((name, ch.codec_dob(c.get("game_dob")), wants))
        # `grown` stays what it has always been: the people who gained INCHES. The offseason
        # report counts it and names them, and "3 grew" meaning "3 got heavier" would be a
        # quietly wrong sentence in a message people read.
        if inches > 0:
            grown.append({"character": c, "inches": inches, "height": now, "weight": pounds})
            log(f'   {name} grew {inches}" to {now // 12}\'{now % 12}" at {age}'
                + (f", {pounds} lbs" if "Weight" in wants else ""))
        else:
            log(f"   {name} filled out to {pounds} lbs at {age}")
    if expect and not dry_run:
        ch.commit(L, expect)
    return grown, expect


# ---- 2. who moves ---------------------------------------------------------------------------
def movers(characters, season):
    """Characters who have outgrown their level, split by where they are going."""
    out = {"college": [], "draft": [], "stay": []}
    for c in characters:
        # "declared" IS A LIVE STATUS, and this was the only place in the codebase that read it
        # as though it were not. `declare_for_draft` sets status='declared' and leaves the
        # `declared` flag alone, so Dodger Manson - declared, on a college roster, wearing the
        # badge on the site - fell straight through this loop into no bucket at all: not the
        # draft, not staying, nothing. An offseason would have moved everybody except him, for
        # ever, and the only symptom is a man who never comes up again.
        #
        # Everywhere else already pairs them: publish, seasonflow (four times), simweek and
        # promote all test `in ("active", "declared")`. This now matches them.
        if c.get("status") not in ("active", "declared"):
            continue
        age = age_of(c, season)
        if c.get("league") == "prep":
            (out["college"] if age >= PREP_LAST_AGE else out["stay"]).append(c)
        elif c.get("league") == "college":
            years = int(c.get("college_years") or 0) + 1
            # The status is the declaration the site actually makes; the flag is the older way
            # of saying it. Either counts, so neither route can strand somebody.
            if c.get("declared") or c.get("status") == "declared" or years >= COLLEGE_MAX_YEARS:
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
            contract=None, contract_years=None,
            team=None, busy=None):
    """Move one character up a level, carrying his ratings, and hand back his old slot.

    `busy` is {team abbrev: characters already there}, and it is what stops a whole class landing
    on one team - see the comment at the pick_slot call. A caller promoting several people should
    keep one dict across them all and count each placement into it; passing None reads the store
    instead, which is correct for a real run and blind on a dry one.
    """
    from_league = character["league"]
    src_path, dst_path = ch.save_path(from_league), ch.save_path(to_league)
    name = f'{character["first_name"]} {character["last_name"]}'

    src = LeagueDat(src_path)
    pl = src.find(name, ch.codec_dob(character.get("game_dob")))
    ratings, potentials = _ratings_of(pl)
    # The BODY he has at the old level, both numbers, read out of the save he is leaving. Height
    # was already carried; weight was not, and stamp_character would then have fallen back to
    # build_weight() - the fourteen-year-old curve - at his adult height. A 6'4" nineteen-year-old
    # arrived at college 27 lbs lighter than he left prep, and the catch-up then spent two
    # offseasons walking it back. He keeps who he is across a level; only his ratings are
    # converted, and that is deliberate and priced.
    height = pl.values["Height"]
    weight = pl.values["Weight"]

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
    dst = LeagueDat(dst_path)
    slots = ch.free_slots(_manifest(), to_league, taken)
    available = {(p.name, p.dob): p for p in dst.players}
    team_ids = sorted(dst.teams())
    team_names = {tid: t.abbrev for tid, t in zip(team_ids, cfg.BY_KEY[to_league].teams)}
    slots = [slot for slot in slots if (slot.name, ch.codec_dob(slot.dob)) in available
             and available[(slot.name, ch.codec_dob(slot.dob))].values["Team"] in team_names]
    for slot in slots:
        slot.team = team_names[available[(slot.name, ch.codec_dob(slot.dob))].values["Team"]]
    # `team` is the drafting team when this is a draft pick: a player drafted by STL should
    # join STL if STL has a free reserve slot, not simply the first vacancy in the league.
    # SPREAD A COHORT. pick_slot has known how to do this since the first five friends landed on
    # two teams - simweek's signup path passes `busy` - but promote never did, so it took the
    # first free slot in league order. On 2026-09-21 all seven were promoted together and every
    # one of them went to MJW; they had to be swapped apart by hand afterwards.
    #
    # `slot.team` above has already been rewritten to where the row REALLY is in the destination
    # save, so the default team_of is right and no resolver is needed here.
    #
    # A caller that promotes several people should pass one `busy` and count each placement into
    # it. Falling back to the store is correct for a real run, because activate_character has
    # written the previous man's team before the next is promoted - but a DRY RUN writes nothing,
    # so without a caller-held tally a preview reports everybody going to the same place.
    spec_to = cfg.BY_KEY[to_league]
    if busy is None:
        busy = {t.abbrev: 0 for t in spec_to.teams}
        for other in store.characters(league=to_league):
            if other.get("status") in ("active", "declared") and other.get("team_abbrev"):
                busy[other["team_abbrev"]] = busy.get(other["team_abbrev"], 0) + 1
    divisions = {t.abbrev: t.division for t in spec_to.teams}
    slot = ch.pick_slot(slots, character.get("position"), team=team,
                        busy=busy, divisions=divisions)
    if slot is None:
        raise OffseasonError(f"no reserve slot left in {to_league} for {name}")

    if dry_run:
        log(f"   would move {name}: {from_league} -> {to_league} ({slot.team})")
        return {"character": character, "to": to_league, "team": slot.team,
                "slot": slot.as_json(), "conversion": conv}

    original_src, original_dst = bytes(src.data), bytes(dst.data)
    ch.stamp_character(dst, slot, {
        "first_name": character["first_name"], "last_name": character["last_name"],
        "dob": ch.codec_dob(character.get("game_dob") or slot.dob), "height_inches": height,
        # `weight_lbs` here is what he weighs NOW, not at fourteen - the same way `height_inches`
        # carries his current height into the new save rather than his starting one.
        "weight_lbs": weight,
        "position": character.get("position"), "ratings": ratings, "potentials": potentials,
        # THE DEAL HAS TO OUTLAST THE ROLLOVER HE ARRIVES IN. run_draft happens inside the
        # offseason, and the game's own rollover runs straight after it - with Finances on that
        # includes FREE AGENCY, where every expiring contract in the league is thrown open at
        # once. A one-year rookie deal would expire in the same offseason it was signed and the
        # AI would re-sign him wherever it pleased, so the man drafted #1 by LCH would open the
        # season somewhere else. ROOKIE_YEARS is the same term the points deal uses, so the
        # game and the ledger agree about how long he is a rookie.
        # Taken from the deal when there is one (the draft's rookie years), and otherwise from
        # the level he is joining. A promotion is not a rookie contract and has no points rate,
        # so it states a TERM without fabricating a deal in his career history - which is why
        # this is a separate argument rather than a `contract` with no rate in it.
        # `game_years` wins when the caller states one, because the points deal and the game
        # deal are different lengths on purpose: four years of skill-point rookie scale, one
        # year of contract so free agency prices him.
        "contract_years": ((contract or {}).get("game_years") or contract_years
                           or ch.LEVEL_CONTRACT_YEARS.get(to_league)),
        # AND WHAT HE IS PAID. A drafted character carries a rookie-scale salary; everyone else
        # passes nothing and takes the league's own token, because only the draft knows a slot
        # and only a slot prices a rookie.
        "contract_salary": (contract or {}).get("salary"),
    })
    # Prepare BOTH saves before committing either. A missing source claim must not leave a
    # second copy of the player in college/pro while his store record still points at prep.
    if not refill(from_league, character, log=log, prepared=src):
        raise OffseasonError(f"cannot release {name}'s original reserve slot")
    try:
        ch.commit(dst, [(name, ch.codec_dob(character.get("game_dob") or slot.dob),
                         {"Height": height, "Weight": weight})])
        original_slot = character["claimed_slot"]
        ch.commit(src, [(original_slot["name"], ch.codec_dob(original_slot["dob"]), {})])
    except Exception:
        # No database call has happened yet. The enclosing journal protects a crash here.
        try:
            src_path.write_bytes(original_src)
            dst_path.write_bytes(original_dst)
        except Exception as exc:
            raise OffseasonRecoveryError("promotion rollback failed; reconcile the saves") from exc
        raise

    store.activate_character(character["id"], to_league, slot.team, slot.as_json(),
                             ch.codec_dob(character.get("game_dob")))

    # Write the sheet back. Moving up to the pros costs 6% and the save now holds the reduced
    # ratings; college costs nothing, so there this simply re-states what he already had. Either
    # way the store must not be left behind - characters.ratings still held what he had at the
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
            entry = {
                "level": to_league, "team_abbrev": slot.team, "player_id": placed.id,
                "from_season": season, "to_season": None,
                "how_it_started": how, "how_it_ended": None,
                "carried": conv["factor"], "years_early": conv["early_years"],
            }
            # THE DEAL RIDES ON THE LEVEL, and it has to, because there is nowhere else to put
            # it: `contract` is not a column and not in SETTABLE_FIELDS, so writing it directly
            # would raise and the rookie deal would silently never exist. `level_history` is
            # jsonb, is already written here, and IS read back - and a contract genuinely belongs
            # to the level he signed at, so it closes itself when the level does.
            if contract:
                entry["contract"] = dict(contract, team=slot.team)
            store.record_level(character["id"], entry)
        except Exception as exc:
            log(f"   (could not record the level for {name}: {exc})")
    log(f"   {name}: {from_league} -> {to_league}, {slot.team}")
    return {"character": character, "to": to_league, "team": slot.team,
            "slot": slot.as_json(), "conversion": conv}


# ---- 4. give the slot back ------------------------------------------------------------------
def refill(league_key, character, log=print, prepared=None):
    """Hand a departing character's reserve slot back its original filler identity.

    Without this the level he left keeps a slot that looks taken forever, and the ceiling on
    concurrent characters ratchets down one person at a time.
    """
    slot = character.get("claimed_slot") or {}
    if not slot:
        return False
    path = ch.save_path(league_key)
    L = prepared if prepared is not None else LeagueDat(path)
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

    # The manifest knows the slot's name, birthday and position; only `claimed_slot` knows the
    # body it had before this character took it over, because stamp_character recorded it there.
    ch.reset_reserve(L, pl, {**original,
                             "height": slot.get("height"), "weight": slot.get("weight")})
    if prepared is None:
        L.save(backup_dir=BACKUPS)
    log(f'   slot {original["name"]} is {"prepared" if prepared is not None else "free again"} in {league_key}')
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


def _draft_needs(log=print):
    """What each pro team is shortest of, read off the pro save. Empty when it cannot be read.

    Never fatal: a draft with no needs is the old behaviour - everybody drafts on talent - and
    that is a far better outcome than a draft that refuses to run because a save was busy.
    """
    try:
        from .codec.league_dat import LeagueDat
        return draft.needs_from_save("pro", LeagueDat(ch.save_path("pro")))
    except Exception as exc:                                            # noqa: BLE001
        log(f"   (could not read pro rosters for team needs: {exc}; drafting on talent alone)")
        return {}


def _draft_field(log=print, limit=None):
    """FBPB3's own prospect class, so our people are drafted against a field rather than alone.

    Empty is the normal answer for most of the year - the pool records exist all season but the
    game only fills them in during its own offseason - and empty simply means the board is our
    characters, exactly as it was before. Nothing here is ever promoted or paid.
    """
    try:
        field = draft.field_from_save(LeagueDat(ch.save_path("pro")), limit=limit)
        if field:
            log(f"   the field: {len(field)} prospects from the game's own pool")
        else:
            log("   the game's draft class is not generated yet; our people draft alone")
        return field
    except Exception as exc:                                            # noqa: BLE001
        log(f"   (could not read the draft pool: {exc}; our people draft alone)")
        return []


def run_draft(declared, store, log=print, dry_run=False, season=None, cast=None):
    """Assign declared players to pro teams, worst record first. Returns the picks.

    `cast` is an optional `draftcast.DraftCast`. It is announced to after each pick has actually
    landed, never before - a pick posted to Discord that then fails to stamp would be the one
    thing worse than no broadcast at all.
    """
    if not declared:
        return []
    # A DRY RUN ANNOUNCES NOTHING. The offseason only builds a cast when it is doing the real
    # thing, so this is belt and braces - but a preview that posts draft picks to the server is
    # the one bug in here that cannot be taken back, and the guard costs a line.
    if dry_run:
        cast = None
    order = draft_order()
    needs = _draft_needs(log)
    # TRIMMED TO THE SIZE OF AN ACTUAL DRAFT. The pool is eighty deep; left whole, a character
    # who slides drags the board out to eighty announced picks to reach him - eighty Discord
    # messages for one signing. Taking the best of the field keeps the night two rounds long and
    # still gives our people a real field to be measured against.
    room = max(0, draft.DRAFT_ROUNDS * len(order) - len(declared))
    field = _draft_field(log, limit=room) if room else []
    # Each team evaluates the remaining board through its own profile and its own hole, so the
    # order is no longer one global ranking with team names stapled on. `_promise` survives as
    # the tiebreak inside `draft.sheet` for anybody with no ratings at all.
    picks = draft.build_board(declared, order, needs=needs, field=field)
    undrafted = draft.undrafted_from(declared, field, picks)
    log(f"   draft order starts {', '.join(order[:4])}")
    # ONE TALLY ACROSS THE WHOLE DRAFT, per promote()'s own contract. Left to itself promote
    # re-reads the store on every call: correct, because activate_character has written the
    # previous man's team before the next pick asks - but that is sixty round trips in the
    # middle of a draft, and a pick that dies on a timeout is a pick that has to be unpicked by
    # hand. Counting placements in here is what the prep->college intake already does.
    busy = None
    if store is not None:
        busy = {t.abbrev: 0 for t in cfg.BY_KEY["pro"].teams}
        try:
            for other in store.characters(league="pro"):
                if other.get("status") in ("active", "declared") and other.get("team_abbrev"):
                    busy[other["team_abbrev"]] = busy.get(other["team_abbrev"], 0) + 1
        except Exception as exc:                                        # noqa: BLE001
            log(f"   (could not tally pro rosters: {exc}; each pick will read the store)")
            busy = None
    if cast is not None:
        cast.open(len(declared), order, board=len(picks))
    for p in picks:
        c = p["character"]
        mark = "" if p.get("is_character", True) else "  (field)"
        log(f'   #{p["pick"]:2} {p["team"]}  {c["first_name"]} {c["last_name"]}{mark}')
        log(f'        {p["reason"]}')
        if p.get("snub"):
            log(f'        {p["snub"]}')
        if cast is not None:
            cast.on_the_clock(p)
        # A FIELD PROSPECT IS ANNOUNCED AND NOTHING ELSE. He belongs to FBPB3, which runs its own
        # draft over the same pool; promoting him here would invent a transaction the game never
        # made. `is_character` defaults to True so a board built by anything that predates the
        # field still writes every pick, which is the safe way round.
        if not dry_run and p.get("is_character", True):
            try:
                how = f'drafted #{p["pick"]} by {p["team"]}'
                # Worked out before the move so the number announced is the number stored: the
                # team comes off the slot he actually lands in, which is not always the team
                # that picked him when a roster has no free reserve row.
                deal = {"rate": points.rookie_rate(p["pick"]), "years": ROOKIE_YEARS,
                        "season_from": season, "pick": p["pick"],
                        # `rate` is skill points a week; `salary` is what the GAME pays him.
                        # Different currencies answering different questions.
                        "salary": rookie_salary(p["pick"]),
                        "game_years": ROOKIE_GAME_YEARS}
                moved = promote(c, "pro", store, log=log, how=how, season=season,
                                team=p["team"], contract=deal, busy=busy)
                if moved["slot"].get("team") != p["team"]:
                    # Not fatal - a roster with no free reserve row cannot take him and
                    # anywhere in the league is better than nowhere - but it must be said,
                    # because the career page will read "drafted by X, plays for Y".
                    log(f'   ! {p["team"]} had no free slot; '
                        f'{c["first_name"]} goes to {moved["slot"].get("team")} instead')
                landed = moved["slot"].get("team")
                if busy is not None and landed:
                    busy[landed] = busy.get(landed, 0) + 1
                p["slot"] = moved["slot"]
                p["conversion"] = moved["conversion"]
                p["contract"] = dict(deal, team=moved["slot"].get("team") or p["team"])
                if hasattr(store, "set_character_field"):
                    store.set_character_field(c["id"], "draft_pick", p["pick"])
                    store.set_character_field(c["id"], "draft_round", p["round"])
                    store.set_character_field(c["id"], "draft_season", season)
            except Exception as exc:
                if isinstance(exc, OffseasonRecoveryError):
                    raise
                log(f'   ! pick #{p["pick"]} failed: {exc}')
                p["error"] = str(exc)
        if cast is not None and "error" not in p:
            cast.pick(p, contract=p.get("contract"))
    if cast is not None:
        cast.close([p for p in picks if "error" not in p], undrafted=undrafted)
    # Only OUR picks are returned. The field is scenery: it is scored, announced and roasted, but
    # the offseason report counts promotions, and a field prospect was never promoted.
    mine = [p for p in picks if p.get("is_character", True)]
    return [p for p in mine if "error" not in p] + [p for p in mine if "error" in p]
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
class _JournaledStore:
    """Record intent before network writes, including calls whose reply never arrives."""
    WRITES = {"activate_character", "grant_points", "record_level", "retire_character",
              "set_character_field", "set_setting", "add_snapshot"}

    def __init__(self, store, journal):
        self.store, self.journal = store, journal
        self.writes_started = False
        self.failed_write = None

    def __getattr__(self, name):
        method = getattr(self.store, name)
        if name not in self.WRITES:
            return method
        def write(*args, **kwargs):
            if self.failed_write:
                raise OffseasonError("an earlier database write failed: " + self.failed_write)
            try:
                self.journal._update_marker(lambda state: state.update(
                    phase="store:" + name, store_writes_started=True))
                self.writes_started = True
                return method(*args, **kwargs)
            except Exception:
                self.failed_write = name
                raise
        return write


def run_offseason(store, season=None, log=print, dry_run=False, force=False, rollover=False):
    """Share the save lock and recovery journal with Sim Week.

    Binary rollback is safe only before any database mutation was attempted. After that,
    preserve both sides and block retries, including force, until they are reconciled.
    """
    from . import simweek as journal
    if not journal._SIM_LOCK.acquire(blocking=False):
        raise journal.SimBusy("a sim, offseason or backup is already using the saves")
    taken, marked, completed, status = {}, False, False, None
    tracked = _JournaledStore(store, journal)
    try:
        # RESOLVE THE SEASON BEFORE ANYTHING READS IT. This used to happen only inside the
        # `if rollover:` branch below, while the takeaways and the season records further down
        # are read on EVERY path. A preview posts no season on purpose - app.py's `_season_arg`
        # documents None as "use the store's season" - so `season_records(None)` reached
        # `int(None)` and turned a supported call into an error page. `for_season` hid it: it
        # catches per league and logs, so only the records call actually died.
        season = int(season or store.get_settings().get("current_season", cfg.START_YEAR))
        if not dry_run:
            stale = journal.interrupted_run()
            if stale:
                raise OffseasonError(journal.describe_interruption(stale))
            if journal.FBPB3.is_running():
                raise OffseasonError("close FBPB3 before running the offseason")
            if rollover:
                from . import seasonflow
                ready = seasonflow.readiness(season, already_locked=True)
                if not ready["ready"]:
                    raise OffseasonError(" ".join(ready["reasons"]))
                if force:
                    raise OffseasonError("A complete season transition cannot be forced. Reconcile interrupted runs first.")
            journal._mark_running(["prep", "college", "pro"], 0, season, kind="offseason")
            marked = True
            taken = back_up_every_save(log=log)
            if set(taken) != {"prep", "college", "pro"}:
                raise OffseasonError("all three saves must be backed up before the offseason")
            journal._update_marker(lambda state: state.update(
                phase="offseason", backups={k: str(v) for k, v in taken.items()}))
        if rollover and not dry_run:
            seasonflow.archive_finished(store, season, taken, log)
        # ONE LIVE CARD, not a one-line "started" and then twenty minutes of silence. The
        # first real season transition ran for over twenty minutes with nothing said in
        # between, and the question it produced - "what is happening in the offseason now?" -
        # is exactly the one a progress card answers. Same card the sim uses; `kind` only
        # changes the words. Never fatal: a Discord outage must not stop a season turning over.
        if not dry_run:
            try:
                status = SimStatus(0, ["prep", "college", "pro"], log=log,
                                   kind="offseason", label=f"Season {season} -> {season + 1}")
                status.start()
            except Exception:                                           # noqa: BLE001
                status = None
        # Freeze the finished season before rollover replaces exports and promotions change leagues.
        season_takeaways = takeaways.for_season(season, store=store)
        season_records = takeaways.season_records(season, store)
        result = _run_offseason(store if dry_run else tracked, season=season, log=log,
                                status=status,
                                dry_run=dry_run, force=force, backups=taken,
                                advance_settings=not rollover)
        result["season_takeaways"] = season_takeaways
        result["season_records"] = season_records
        if not dry_run:
            if tracked.failed_write:
                raise OffseasonError("a database write failed: " + tracked.failed_write)
            if rollover:
                tracked.writes_started = True  # engine changes must never trigger a save-only rollback
                result["rollover"] = seasonflow.rollover_saves(tracked, season, journal, log)
                tracked.set_setting("last_offseason", season)
                tracked.set_setting("current_season", season + 1)
                tracked.set_setting("current_week", 0)
                result["next_season"] = season + 1
                for key in ("prep", "college", "pro"):
                    journal._snapshot_league(key, tracked, season + 1, 0, log)
            completed = True
            journal._clear_marker()
        if rollover and not dry_run:
            _say(status, 92, "publish", "Rebuilding the site for the new season")
            try:
                from .publish.publish import publish, git_push
                publish([s.key for s in cfg.LEAGUES])
                if store.get_settings().get("auto_publish", True):
                    git_push(f"Season {season + 1} opening")
                result["published"] = True
            except Exception as exc:
                result["publish_error"] = str(exc)
                log(f"New season is saved, but publishing needs a retry: {exc}")
        # Notification failures must never roll back a completed offseason.
        try:
            if not dry_run:
                _say(status, 98, "report", "Writing the season up")
                notify.post(_offseason_report(result, store=store), log=log)
        except Exception:
            pass
        if not dry_run:
            try:
                save_result(result)
            except OSError as exc:
                log(f"Offseason completed, but saving its report failed: {exc}")
        _finish(status, True, _one_line(result))
        return result
    except Exception as exc:
        if marked and not completed:
            if not tracked.writes_started and (not taken or restore_saves(taken, log=log)):
                journal._clear_marker()
            else:
                log("Offseason recovery required. Saves and database may both contain changes; "
                    "reconcile them before clearing the recovery journal. Do not retry.")
            try:
                notify.post(f"**Offseason stopped** - {exc}. Check the recovery journal before retrying.",
                            log=lambda m: None)
            except Exception:
                pass
        # Turn the card red rather than leaving it stuck at 99% looking like it is still going.
        _finish(status, False, str(exc)[:300])
        raise
    finally:
        journal._SIM_LOCK.release()


def _rebased_count(info):
    """How many unclaimed reserve seats were, or would be, re-aged to the intake age.

    `apply` reports them under `rebased_reserves` and `plan` is gaining its own preview of the
    same thing. Read whichever is present rather than pinning one name: a missing key here has
    to mean "none reported", not a KeyError raised inside the offseason's own never-fatal block.
    """
    for field in ("rebased_reserves", "rebasing", "rebased"):
        value = info.get(field)
        if value is not None:
            return len(value) if isinstance(value, (list, tuple, set)) else int(value)
    return 0


def back_up_every_save(log=print):
    """A copy of all three saves before ANYTHING is written, keyed by league.

    Before anything, not before growth: retirements run first and `refill` writes to a save, so
    a backup taken inside the growth loop was already too late for the league a retirement had
    touched.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    taken = {}
    for key in ("prep", "college", "pro"):
        path = ch.save_path(key)
        if not path.exists():
            continue
        dest = BACKUPS / f"{stamp}-offseason-{cfg.BY_KEY[key].save_name}"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / "league.dat")
        # THE MANIFEST TRAVELS WITH THE SAVES. It is the map from a reserve slot to a row in the
        # file, matched on name AND date of birth, and since the age-out began re-aging unclaimed
        # seats the two are only meaningful together. Restoring league.dat alone would leave the
        # save holding the old reserve birthdays and the manifest the new ones, and every
        # unclaimed seat would become unfindable: stamp_character's L.find raises CodecError and
        # no new signup can be placed until somebody repairs the manifest by hand.
        if ageout.MANIFEST.exists():
            shutil.copy2(ageout.MANIFEST, dest / "manifest.json")
        taken[key] = dest / "league.dat"
    if taken:
        log(f"backed up {len(taken)} save(s) to {BACKUPS}")
    return taken


def restore_saves(backups, log=print):
    """Copy the backups back over the saves. True only if every one of them went back.

    Only call this before any store write was attempted. Otherwise restoring binary saves
    alone would undo game changes while retaining retirements, points or completion flags.
    """
    if not backups:
        return False
    ok = True
    for key, backup in backups.items():
        try:
            shutil.copy2(backup, ch.save_path(key))
            log(f"   restored the {key} save from {Path(backup).parent.name}")
        except Exception as exc:
            ok = False
            log(f"   ! could not restore the {key} save from {backup}: {exc}")
    # ONCE, and after the saves. There is one manifest for the whole universe, and the copy
    # beside any of these backups was taken at the same moment as all three, so restoring it
    # from the first one that has it puts the pair back in step. See back_up_every_save.
    for backup in backups.values():
        saved = Path(backup).parent / "manifest.json"
        if not saved.exists():
            continue
        try:
            shutil.copy2(saved, ageout.MANIFEST)
            log(f"   restored universe/manifest.json from {saved.parent.name}")
        except Exception as exc:                                        # noqa: BLE001
            ok = False
            log(f"   ! could not restore the manifest from {saved}: {exc}")
        break
    return ok


def _say(status, percent, stage, detail, league=None):
    """Move the card on. Silent and harmless when there is no card, or when Discord is down."""
    if status is None:
        return
    try:
        status.update(percent=percent, stage=stage, detail=detail, league=league)
    except Exception:                                                   # noqa: BLE001
        pass


def _finish(status, ok, detail):
    if status is None:
        return
    try:
        status.finish(ok, detail)
        status.wait(timeout=simstatus.FINAL_BUDGET)
    except Exception:                                                   # noqa: BLE001
        pass


def _one_line(result):
    """The offseason in one sentence, for the card's final state."""
    aged = sum((v or {}).get("retired") or 0 for v in (result.get("aged_out") or {}).values())
    came = sum((v or {}).get("arrived") or 0 for v in (result.get("aged_out") or {}).values())
    bits = [f'{result.get("grown", 0)} grew']
    for label, key in (("promoted", "promoted"), ("drafted", "drafted"), ("retired", "retired")):
        n = len(result.get(key) or [])
        if n:
            bits.append(f"{n} {label}")
    if aged or came:
        bits.append(f"{aged} aged out, {came} arrived")
    if result.get("publish_error"):
        bits.append("the site needs a republish")
    return ", ".join(bits)


def _offseason_report(result, store=None):
    """The offseason, as the Discord server should hear it.

    Ordered by what people actually care about, which is not the order the code does it in: who
    got taller, who improved most, who moved up, who is gone. A count ("3 grew") is a log line;
    a name is news, so everything here names somebody.

    One named winner for the biggest improvement, deliberately. "Everyone improved" is a report;
    "Johnny improved most" is a competition, and this universe is seven friends competing.
    """
    lines = [f'**Offseason {result.get("season")}**']

    grew = sorted(result.get("grew") or [], key=lambda g: -g["inches"])
    if grew:
        lines.append("")
        lines.append("**Grew over the summer**")
        for g in grew[:8]:
            feet, inches = divmod(int(g["height"]), 12)
            # built with chr() rather than escapes: a feet-and-inches string needs both an
            # apostrophe and a double quote, and writing those through a scripted edit is how
            # this file got a syntax error and another one shipped a literal 0x08.
            tall = str(feet) + chr(39) + str(inches) + chr(34)
            lines.append("- " + g["name"] + " +" + str(g["inches"]) + chr(34) + " to " + tall)
        if len(grew) > 8:
            lines.append(f"- ...and {len(grew) - 8} more")

    movers = result.get("movers") or []
    if movers:
        best = movers[0]
        lines.append("")
        lines.append(f'**Most improved: {best["name"]}** '
                     f'+{best["gain"]} across his sheet ({best["from"]} to {best["to"]}, '
                     'stamina aside)')
        for m in movers[1:4]:
            lines.append(f'- {m["name"]} +{m["gain"]}')
        stalled = [m for m in movers if m["gain"] <= 0]
        if stalled:
            # Said out loud rather than quietly omitted. Somebody who went nowhere all season is
            # exactly who needs to know, and he will not find it by reading a list of winners.
            lines.append(f'- no movement at all: {", ".join(m["name"] for m in stalled[:4])}')

    # THE DRAFT GETS ITS OWN BOARD, in pick order, with what each man signed for. `drafted` is a
    # list of pick DICTS, so the generic str() below would have dumped raw Python into Discord -
    # and the draft is the one part of an offseason people want to read line by line anyway.
    drafted = result.get("drafted") or []
    if drafted:
        lines.append("")
        lines.append("**Draft**")
        for p in drafted[:12]:
            if not isinstance(p, dict):
                lines.append(f"- {p}")
                continue
            c = p.get("character") or {}
            name = f'{c.get("first_name", "")} {c.get("last_name", "")}'.strip() or "?"
            deal = p.get("contract") or {}
            rate = f' - {deal["rate"]}/wk' if deal.get("rate") is not None else ""
            lines.append(f'- #{p.get("pick", "?")} {p.get("team", "?")}  {name}{rate}')
            if p.get("reason"):
                lines.append(f'  {p["reason"]}')
        if len(drafted) > 12:
            lines.append(f"- ...and {len(drafted) - 12} more")

    for label, key in (("Moved up", "promoted"), ("Retired", "retired")):
        rows = result.get(key) or []
        if rows:
            lines.append("")
            lines.append(f"**{label}**")
            lines.append("- " + ", ".join(str(r) for r in rows)[:400])

    # WHAT ACTUALLY HAPPENED ON A COURT. Growth and most-improved are about sheets; this is
    # about basketball, and it is the part a group chat argues over. Built from the stats.json
    # and games.json every publish already writes, so it costs no parsing. See takeaways.py.
    try:
        colour = (result["season_takeaways"] if "season_takeaways" in result else
                  takeaways.for_season(result.get("season"), store=store))
    except Exception as exc:                                            # noqa: BLE001
        colour = []
        print(f"  no takeaways in the report ({exc}); the rest of it stands")
    if colour:
        lines.append("")
        lines.append("**The season itself**")
        lines.extend(colour)

    # THE REST OF THE LEAGUE, which is the biggest single change an offseason makes and used to
    # go out with nobody told. 51 prep players leaving and 51 fourteen-year-olds arriving is
    # league news by any measure - and if it silently stops working, a silent report is exactly
    # how nobody would notice for a season.
    aged = result.get("aged_out") or {}
    moved = {k: v for k, v in aged.items() if v.get("retired") or v.get("arrived")}
    if moved:
        lines.append("")
        lines.append("**The rest of the league moved on too**")
        for key in sorted(moved):
            row, arrive = moved[key], ageout.INTAKE_AGE.get(key)
            name = cfg.BY_KEY[key].name if key in cfg.BY_KEY else key
            lines.append(f'- {name}: {row["retired"]} aged out, {row["arrived"]} new '
                         + (f"{arrive}-year-olds arrived" if arrive else "arrived"))
    broken = sorted(k for k, v in aged.items() if v.get("error"))
    if broken:
        lines.append("")
        lines.append(f'- the age-out did not run for {", ".join(broken)}; check the log')

    paid = []
    if result.get("paid"):
        paid.append(f'{result["paid"]} paid the offseason lump')
    if result.get("season_bonus"):
        paid.append(f'{result["season_bonus"]} season-bonus point(s) on top')
    # The promotion grant is the biggest single payout an offseason makes. It was reaching the
    # log and the result and stopping there, which is the one audience that does not read them.
    grants = result.get("promotion_grants") or {}
    if grants:
        total = sum(points for rows in grants.values() for _, points in rows)
        paid.append(f'{total} promotion-grant point(s) to {len(grants)} moving up')
    if paid:
        lines.append("")
        lines.append("**Points** - " + ", ".join(paid))

    if result.get("failed"):
        lines.append("")
        lines.append(f'{len(result["failed"])} could not be processed; check the log')
    return "\n".join(lines)


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
    out, grants, caches = {}, {}, {}
    for c in characters:
        if c.get("status") != "active":
            continue
        key = c.get("league")
        if not key:
            continue
        html = ch.save_path(key).parent / "html"
        if not html.exists():
            continue
        if key not in caches:
            when = seasonbonus.export_date(html)
            log(f"{key}: season bonus read from the export of {when or 'an unknown date'}")
        try:
            rows = seasonbonus.for_character(
                f'{c["first_name"]} {c["last_name"]}', html, settings,
                caches.setdefault(key, {}),
                rounds=cfg.BY_KEY[key].playoff_rounds if key in cfg.BY_KEY else None,
                league=key)
        except Exception as exc:
            # NOT `continue`. The promotion grant is worked out below off the same cache, and
            # skipping to the next character forfeited 20-44 points with nothing in the log to
            # say so - the season bonus is a handful of points, the grant is a career step.
            log(f'no season bonus for {c["first_name"]} {c["last_name"]}: {exc}')
            rows = []
        if rows:
            out[c["id"]] = rows
        # THE PROMOTION GRANT IS WORKED OUT HERE TOO, for everybody, off the cache that has just
        # been built - not later for the movers only. `movers` runs after this, so asking then
        # would mean re-parsing six pages per league at the one moment the character's own
        # `league` field has already been changed to where he is GOING. Computed for all and
        # paid only to those who actually moved.
        try:
            grant = seasonbonus.promotion_grant(
                f'{c["first_name"]} {c["last_name"]}', html, settings, caches[key],
                rounds=cfg.BY_KEY[key].playoff_rounds if key in cfg.BY_KEY else None)
        except Exception as exc:                                        # noqa: BLE001
            log(f'no promotion grant for {c["first_name"]} {c["last_name"]}: {exc}')
            grant = []
        if grant:
            grants[c["id"]] = grant
    return out, grants


# Stamina is excluded from the improvement figure, and this is not a fudge for one season.
# On 2026-09-19 every character's Stamina was set administratively to a flat 70 - the quiz had
# been producing 19-34, the bottom two percent of the league - and that one write is worth +2.0
# to +2.8 of sheet average, against 0.0 to 1.4 of movement actually EARNED across the whole
# season. Left in, "most improved" would have ranked by who started with the worst conditioning,
# Tim first at 12 -> 70, and it would have stopped naming the one player who genuinely went
# nowhere. A flat number that is identical for everybody carries no information about who
# improved, this season or any season, so it does not belong in a measure of who improved.
MOVER_FIELDS = [r for r in RATINGS if r != "Stamina"]


def season_movers(characters, store, season, log=print):
    """[{name, from, to, gain}] - how far each character's sheet moved across the season.

    Read from `rating_snapshots`, which every Sim Week has been writing all year and which
    nothing has ever read back. The first and last snapshot of the season being closed are the
    two ends of it; the middle is the growth chart on his career page.

    The mean of all eighteen ratings, not FBPB3's own Overall. CONVENTIONS records that field as
    unverified and mostly zero, and the site already colours players by this mean, so this is the
    number people have been looking at all season.

    Never raises. A report that cannot be built must not take the offseason down with it.
    """
    out = []
    if not hasattr(store, "snapshots"):
        return out
    for c in characters:
        if c.get("status") != "active":
            continue
        try:
            rows = [s for s in (store.snapshots(character_id=c["id"]) or [])
                    if int(s.get("season", -1)) == int(season)]
        except Exception as exc:
            log(f'   no history for {c["first_name"]} {c["last_name"]}: {exc}')
            continue
        if len(rows) < 2:
            continue
        start = _mean(rows[0].get("ratings") or {}, MOVER_FIELDS)
        end = _mean(rows[-1].get("ratings") or {}, MOVER_FIELDS)
        out.append({"name": f'{c["first_name"]} {c["last_name"]}',
                    "from": round(start, 1), "to": round(end, 1),
                    "gain": round(end - start, 1)})
    out.sort(key=lambda m: -m["gain"])
    return out


def _run_offseason(store, season=None, log=print, dry_run=False, force=False, backups=None,
                   status=None,
                   advance_settings=True):
    settings = store.get_settings()
    season = int(season or settings.get("current_season", cfg.START_YEAR))
    done = settings.get("last_offseason")
    if done is not None and int(done) >= season and not dry_run and not force:
        raise OffseasonError(
            f"the {season} offseason has already been run (last completed: {done}). "
            "Pass force=True only if you know the first run did not finish.")
    result = {"season": season, "grown": 0, "grew": [], "movers": [],
              "promoted": [], "drafted": [], "retired": [], "failed": [], "dry_run": dry_run}

    _say(status, 5, "prepare", "Checking every character is still in his save")
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
        # The backup for this league was taken by run_offseason before the first write of the
        # whole offseason - retirements go first and refill writes - so there is none to take
        # here. A direct call to _run_offseason with no backups is a caller who has said, by
        # passing nothing, that it is looking after its own copies.
        if not dry_run and backups is not None and key not in backups:
            log(f"   ! no backup was taken for {key}; refusing to write to it")
            continue
        _say(status, 15, "grow", f"{cfg.BY_KEY[key].name}: growing everybody a year", key)
        log(f"{key}: growth")
        grown, changed = apply_growth(key, characters, season, log=log, dry_run=dry_run)
        if not dry_run:
            by_name = {f'{c["first_name"]} {c["last_name"]}': c for c in characters}
            for name, _dob, values in changed:
                c = by_name.get(name)
                if not c:
                    continue
                if "Height" in values:
                    store.set_character_field(c["id"], "height_inches", values["Height"])
                if "Weight" in values:
                    store.set_character_field(c["id"], "weight_lbs", values["Weight"])
        result["grown"] += len(grown)
        # The COUNT is all the panel ever wanted; the report wants to name people. An inch over
        # a summer is the most "kid growing up" thing that happens in this universe and it has
        # been a silent number change all year.
        result["grew"] += [{"name": f'{g["character"]["first_name"]} {g["character"]["last_name"]}',
                            "inches": g["inches"], "height": g["height"]} for g in grown]

    # Before movers(), so everyone is still measured against the league he played in.
    try:
        result["movers"] = season_movers(characters, store, season, log=log)
    except Exception as exc:
        log(f"could not work out who improved most ({exc}); the offseason is unaffected")
    _say(status, 30, "bonus", "Reading the season's honours and leaderboards")
    season_bonus, promotion_grants = _season_bonuses(characters, settings, log)
    for c in characters:
        rows = season_bonus.get(c["id"])
        if rows:
            log(f'{c["first_name"]} {c["last_name"]}: +{sum(p for _, p in rows)} season bonus ('
                + ", ".join(f"{r.split(':', 1)[-1].strip()} {p:+d}" for r, p in rows) + ")")

    _say(status, 40, "move", "Working out who moves up and who enters the draft")
    moving = movers(characters, season)
    log(f"moving up: {len(moving['college'])} to college, {len(moving['draft'])} into the draft")
    # One character the codec cannot find must not abort an offseason that has already moved
    # other people - a half-run offseason is far worse than a reported failure, because the
    # save and the store disagree from then on.
    # ONE TALLY ACROSS THE WHOLE CLASS. promote falls back to reading the store, which is right
    # for a real run but blind on a dry one - nothing is written, so every preview would report
    # the entire cohort going to the same team. Counting each placement here keeps the preview
    # honest and matches what the real run does.
    spec_college = cfg.BY_KEY["college"]
    college_busy = {t.abbrev: 0 for t in spec_college.teams}
    for other in store.characters(league="college"):
        if other.get("status") in ("active", "declared") and other.get("team_abbrev"):
            college_busy[other["team_abbrev"]] = college_busy.get(other["team_abbrev"], 0) + 1
    for c in moving["college"]:
        try:
            # THE TERM IS STATED. Without it the stamp wrote a one-year deal, which expires at
            # the very next rollover's free agency - so every character promoted out of prep
            # would be thrown open to the AI in the same offseason he arrived at college.
            moved = promote(c, "college", store, log=log, dry_run=dry_run,
                            how="aged out of prep", season=season, busy=college_busy,
                            contract_years=ch.LEVEL_CONTRACT_YEARS["college"])
            result["promoted"].append(moved)
            landed = (moved.get("slot") or {}).get("team") or moved.get("team")
            if landed:
                college_busy[landed] = college_busy.get(landed, 0) + 1
        except Exception as exc:
            if isinstance(exc, OffseasonRecoveryError):
                raise
            name = f'{c.get("first_name")} {c.get("last_name")}'
            log(f"   ! {name} did not move: {exc}")
            result["failed"].append({"character": c, "stage": "promote", "error": str(exc)})
    # DRAFT NIGHT GOES OUT LIVE, one pick at a time, and only when there is a draft to watch.
    # A dry run builds the same board and announces none of it - the preview must be able to show
    # the room exactly what will happen without telling the room it happened.
    cast = None
    if moving["draft"] and not dry_run:
        try:
            cast = draftcast.DraftCast(season, log=log)
        except Exception as exc:                                        # noqa: BLE001
            log(f"   (no draft broadcast: {exc}; the draft itself is unaffected)")
    try:
        result["drafted"] = run_draft(moving["draft"], store, log=log, dry_run=dry_run,
                                      season=season, cast=cast)
    finally:
        if cast is not None:
            cast.finish()

    # Bank the college year BEFORE anything reads it again. This was a real bug: three places
    # read `college_years` and nothing wrote it, so every college player was permanently a
    # freshman - always three years early, so always the 73% conversion, and the four-year
    # eligibility cap could never fire.
    if not dry_run:
        for c in moving["stay"]:
            if c.get("league") == "college":
                store.set_character_field(c["id"], "college_years",
                                          int(c.get("college_years") or 0) + 1)

    # ---- the AI population moves on as well ------------------------------------------------
    # Until now only OUR characters were ever aged out of a league. The generated population
    # simply got a year older every season: by the 2027 rollover prep was running 15-19 against
    # the 14-17 band it was built with, 61 AI players were past the cap, and the prep scoring
    # title went to an eighteen-year-old. See commissioner/ageout.py.
    #
    # LAST of the steps that write to a save, deliberately. Every character has finished moving
    # by here and every reserve slot a departure left behind has been refilled, so anything
    # still over the cap is genuinely nobody's - and the intake cannot land in a slot that was
    # about to be handed to a person.
    result["aged_out"] = {}
    for key in ("prep", "college"):
        _say(status, 55 if key == "prep" else 75, "ageout",
             f"{cfg.BY_KEY[key].name}: retiring the over-age and bringing a new class in", key)
        if not ch.save_path(key).exists():
            continue
        if not dry_run and backups is not None and key not in backups:
            log(f"   ! no backup was taken for {key}; not aging its AI population out")
            continue
        try:
            if dry_run:
                # PLAN, NOT APPLY. `apply(dry_run=True)` does every release, rename and sign and
                # then throws the result away - and each of those splices the file and re-parses
                # all 425 records, so it is about seven minutes a league. That turned the Dry run
                # button, which is documented as answering in half a second and exists precisely
                # so somebody can look before committing, into a fourteen-minute silent stall
                # holding the save lock. `plan` answers the same question by reading.
                p = ageout.plan(key, season, store=store)
                log(f'   {key}: {len(p["retiring"])} would age out at {p["cap"]}+, '
                    f'{p["intake"]} would arrive at {ageout.INTAKE_AGE[key]}')
                result["aged_out"][key] = {"retired": len(p["retiring"]),
                                           "arrived": p["intake"], "dry_run": True,
                                           "rebased": _rebased_count(p)}
                continue
            out = ageout.apply(key, season, store=store, dry_run=False, log=log)
            result["aged_out"][key] = {"retired": len(out["retired"]),
                                       "arrived": len(out["arrived"]),
                                       "rebased": _rebased_count(out)}
        except Exception as exc:                                        # noqa: BLE001
            # Never fatal, and never a raise. By this point characters have moved leagues and
            # the store has been written; an intake that did not happen is a cosmetic problem
            # next season, while a half-run offseason leaves the saves and the store disagreeing
            # forever. Say so loudly and carry on.
            # NOT "did not run". By the time this can raise, the age-out may already have
            # written the save AND the manifest, and with the reserve re-base those two have to
            # agree - so the difference between "nothing happened" and "something half happened"
            # is the difference between ignoring this line and checking the file.
            log(f"   ! {key}: the age-out failed partway ({exc}); it may already have written "
                f"the save and the manifest, so check both before running it again. The rest of "
                f"the offseason stands")
            result["aged_out"][key] = {"error": str(exc)}

    # WHAT THE GRANT WOULD PAY, on a dry run as well as a real one. This used to be set only
    # inside the `if not dry_run` block below, so a preview - the thing somebody reads BEFORE an
    # irreversible rollover - showed the lump and the development bonus and stayed silent about
    # what is now the largest payment of the offseason. The real run narrows this to what was
    # actually paid once the writes have landed.
    result["promotion_grants"] = {
        r["character"]["id"]: promotion_grants[r["character"]["id"]]
        for r in result.get("promoted", [])
        if isinstance(r, dict) and r.get("character")
        and promotion_grants.get(r["character"].get("id"))}

    # The offseason lump sum: every active character is a year older and gets paid for it,
    # and a college season that was seen through pays a development bonus on top.
    lump = int(settings.get("offseason_points", 15))
    bonus = int(settings.get("college_development_bonus", COLLEGE_DEVELOPMENT_BONUS))
    # WHAT THE GAME THINKS A PRO IS WORTH. Read once, from the pro save, before anybody is paid:
    # the band a man falls into is his place among his OWN league's salaries, so it cannot be
    # computed one character at a time. Never fatal - a league whose contracts cannot be read
    # falls through to `None`, and annual_payout then pays the floor to everybody, which is the
    # honest answer rather than a guess.
    pro_salaries, pro_bounds = {}, None
    try:
        from .codec.league_dat import LeagueDat
        pro = LeagueDat(ch.save_path("pro"))
        rostered = [pl for pl in pro.players if pl.values.get("Team", 0) >= 1]
        for pl in rostered:
            pro_salaries[(pl.name, pl.dob)] = (pro.contract_of(pl) or [0])[0]
        pro_bounds = points.salary_distribution(pro_salaries.values())
        log(f"pro salary bands: {pro_bounds}" if pro_bounds else
            "pro has no salary scale yet (Finances off); the payout pays its floor")
    except Exception as exc:                                        # noqa: BLE001
        log(f"could not read pro contracts ({exc}); the payout pays its floor")

    def _annual(c):
        """(points, reason) for one character's yearly payment.

        Pro is paid for his contract INSTEAD of the flat lump - that is the whole change. Prep
        and college are untouched and keep the lump, because neither has real contracts and prep
        cannot be given them: 164 of its 240 rostered players have none, and Finances would
        release every one of them on load.
        """
        if c.get("league") != "pro":
            return lump, "offseason"
        salary = pro_salaries.get((f'{c["first_name"]} {c["last_name"]}',
                                   ch.codec_dob(c.get("game_dob"))), 0)
        amount = points.annual_payout(salary, pro_bounds)
        return amount, points.payout_reason(salary, amount, pro_bounds)

    if not dry_run:
        paid = developed = earned = granted = 0
        stayed = {c["id"] for c in moving["stay"]}
        promoted_ids = {r["character"]["id"] for r in result.get("promoted", [])
                        if isinstance(r, dict) and r.get("character")}
        # Who was actually PAID, not who moved. promotion_grant returns nothing for a character
        # with no line in the export, and counting him as paid would overstate the report.
        granted_to = set()
        for c in store.characters():
            if c.get("status") != "active":
                continue
            amount, why = _annual(c)
            if amount:
                store.grant_points(c["id"], amount, why)
                paid += 1
                if c.get("league") == "pro":
                    result.setdefault("contract_payouts", {})[c["id"]] = [(why, amount)]
            if bonus and c.get("league") == "college" and c["id"] in stayed:
                store.grant_points(c["id"], bonus, "college development")
                developed += 1
            # One ledger row per component, so a friend's history says WHY he was paid rather
            # than showing one unexplained lump. Wrapped per character: a bonus that fails must
            # not cost somebody the offseason lump he has already earned.
            # Only the people who actually moved, and only what was computed BEFORE they did.
            if c["id"] in promoted_ids and promotion_grants.get(c["id"]):
                for why, amount in promotion_grants.get(c["id"], []):
                    try:
                        store.grant_points(c["id"], amount, why)
                        granted += amount
                        # Credited only once the write has landed. Marking him before the call
                        # meant a Supabase hiccup left the result and the saved report claiming
                        # points he never got, which is the one record anybody would check.
                        granted_to.add(c["id"])
                    except Exception as exc:                            # noqa: BLE001
                        log(f'could not pay "{why}" to '
                            f'{c["first_name"]} {c["last_name"]}: {exc}')
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
        if granted:
            log(f"paid {granted} promotion-grant point(s) to {len(granted_to)} who moved up")
        # The grant is about to be the biggest single payout of an offseason, so it belongs in
        # the report beside the lump and the development bonus rather than only in the log.
        result["promotion_grants"] = {cid: rows for cid, rows in promotion_grants.items()
                                      if cid in granted_to}
        result["paid"] = paid
        result["developed"] = developed
        result["season_bonus"] = earned

    if not dry_run and advance_settings:
        store.set_setting("last_offseason", season)
        store.set_setting("current_season", season + 1)
        store.set_setting("current_week", 0)
    log(f"offseason complete: {result['grown']} grew, {len(result['promoted'])} promoted, "
        f"{len(result['drafted'])} drafted, {len(result['retired'])} retired")
    return result


RESULT_PATH = Path(__file__).resolve().parents[1] / "universe" / "offseason_last_result.json"


def save_result(result):
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = RESULT_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(RESULT_PATH)


def saved_result():
    try:
        return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
