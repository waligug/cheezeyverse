"""Move the AI population on when it outgrows its league, and bring a new class in behind it.

WHY THIS EXISTS. Nothing ever aged an AI player out. `PREP_LAST_AGE` in `offseason.py` reads
like it does, but it is applied in `movers()`, which walks the STORE - our characters and nobody
else. Filler players were never considered anywhere, and the only retirement in the codebase is
`PRO_RETIREMENT_AGE`, which is also characters-only.

So the generated population simply got a year older every season, forever. Measured on
2026-09-20, two seasons into the universe:

  * PREP was created with fillers aged 14-17 and its rosters ran 15-19, with 61 AI players
    already past 17.
  * COLLEGE was created 18-21 and ran 19-23, with 61 past 21.
  * Prep's population is FIXED at 425 players - 240 rostered, 185 in a free-agent pool nobody
    signs from. No new class arrives at a rollover. It is the same 425 people growing up.

Left alone, prep is a high-school league of 22-year-olds in three seasons and 25-year-olds in
six, and the only teenagers in it are ours. It was not hypothetical either: the 2027 prep scoring
title went to an 18-year-old, and seven of the top fifteen scorers were over the cap.

WHAT THIS DOES, once per league per rollover:

1. RETIRE anyone at or past the cap who is not ours. He is released from his roster and crushed
   to the rating floor, so no GM signs him back. He is not deleted - the codec can release,
   sign, rename and set, but it cannot remove a record from league.dat or create one, and
   inventing a delete for this would mean rewriting the file's record table. Released and
   defanged, he never plays again, which is the whole point.
2. REFILL the roster back to full by recycling the deadest wood in the free-agent pool into a
   new intake: a fresh name, a fresh birthday at the league's youngest age, and ratings rolled
   by the same generator that made the original population. That pool is 185 players in prep
   who will otherwise never play again, so the intake costs nothing and drains a pile that would
   only grow.

WHAT IT WILL NOT TOUCH. Our characters, ever - by store identity and by manifest reserve row,
both. A reserve slot is how a new signup gets placed; losing one silently breaks the next person
to join, which is a far worse bug than an old filler.

THE ORDER MATTERS, and it is the one thing to be careful about when editing this. `rename`,
`release` and `sign` each splice the file and RE-PARSE it, so every Player object held across one
of them is stale - that is how an earlier tool renamed the wrong rows. `set` only pokes bytes and
is safe to batch. So each player is re-found by identity immediately before every splicing call.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .codec.league_dat import POTENTIALS, RATINGS, CodecError, LeagueDat
from .universe import config as cfg
from .universe import generate as gen

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")

# The first age at which a player has OUTGROWN the league, measured in the season about to be
# PLAYED (see playing_season). Pro is absent on purpose: a pro career ends by retirement, which
# offseason.py already handles, and 34 is the oldest filler the universe makes.
#
# NOT the generated bands. The universe was built prep 14-17 and college 18-21, and the first
# version of this capped exactly there - which meant a kid was thrown out of prep the season he
# turned 18. Nate's call on 2026-09-20 was to let him play that year out and move up at 19, so
# the ladder is now:
#
#     prep    14-18   (five years)      cap 19, intake 14
#     college 19-22   (four years)      cap 23, intake 19
#     pro     23+     retires at 38
#
# One knock-on worth knowing: the pro filler population is generated from 22, so for a season or
# two there are 22-year-old pros who never went to college. Harmless - they are AI bodies - but
# it is why pro's youngest and college's oldest no longer meet exactly.
AGE_CAPS = {"prep": 19, "college": 23}
# ...and the age a recycled newcomer arrives at: the bottom of the league's band.
INTAKE_AGE = {"prep": 14, "college": 19}

# league.dat stores Position as 1-5. Confirmed against the manifest on 2026-09-20 by counting
# every player whose name the manifest knows: 1=C, 2=PF, 3=SF, 4=SG, 5=PG.
POSITION_NAME = {1: "C", 2: "PF", 3: "SF", 4: "SG", 5: "PG"}
POSITION_CODE = {v: k for k, v in POSITION_NAME.items()}

# Same floors protect_rosters uses, and for the same reason: low enough that no GM prefers him,
# not zero, because a rating of 0 reads as "unrated" in places and the league leaderboards look
# broken when a player is a literal zero.
FLOOR_RATING = 2
FLOOR_POTENTIAL = 5


class AgeOutError(Exception):
    pass


def age_of(player, season):
    """Season minus birth year, the same convention offseason.age_of uses for characters."""
    return int(season) - int(player.values["BirthYear"])


def playing_season(finished_season):
    """The season this rollover is SETTING UP, which is the one the cap has to be measured in.

    THE OFF-BY-ONE THAT MAKES THIS WHOLE MODULE POINTLESS IF YOU GET IT WRONG. A rollover runs
    after season N ends and prepares season N+1. Testing the cap against N keeps everybody who
    was 17 in N - and every one of them turns 18 in N+1 and plays the entire season at 18. The
    league still fields eighteen-year-olds; they are simply a different eighteen-year-olds.

    That is not hypothetical. It is what the 2027 prep season looked like: Caron Gilstrap won
    the scoring title at 18, and he had survived a rollover to do it.

    Measured in N+1, the born-2010 cohort goes at the 2027 rollover instead of after another
    full season, and prep opens 2028 as 14/15/16/17 - the band it was generated with.
    """
    return int(finished_season) + 1


def protected_names(key, store=None):
    """Everyone this must never touch: our characters, and the reserve rows they get placed into.

    Both, not either. The store knows a character by the name he plays under; the manifest knows
    the reserve ROW he was stamped onto, which still carries its generated name until somebody
    claims it. An empty reserve slot is not a spare body - it is where the next person to sign up
    goes, and recycling one would break his placement for a reason that has nothing to do with him.
    """
    names = set()
    manifest = json.loads((ROOT / "universe" / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["players"]:
        if row["league"] == key and row["role"] == "reserve":
            names.add(row["name"])
    for c in (store.characters(league=key) if store is not None else []):
        names.add(f'{c["first_name"]} {c["last_name"]}')
    return names


def _needed_position(roster_positions, spec):
    """Which position this team is shortest of, against the shape the universe generates.

    Without this a recycled intake keeps whatever position the free agent happened to have, and
    a few seasons of that leaves teams with six centres and no point guard.
    """
    want = {}
    for p in gen.FILLER_POSITIONS:
        want[p] = want.get(p, 0) + 1
    scale = spec.filler_per_team / len(gen.FILLER_POSITIONS)
    have = {}
    for code in roster_positions:
        name = POSITION_NAME.get(code)
        if name:
            have[name] = have.get(name, 0) + 1
    return min(want, key=lambda p: have.get(p, 0) - want[p] * scale)


def _codec_safe(part):
    """What LeagueDat.rename will actually accept: printable ASCII, 1-40 characters.

    The name pools are wider than that - they carry Orcun, Bernabe and friends with their real
    accents, and the universe got away with it because creation goes in through FBPB3's player
    FILE import, which is latin-1. Renaming a record in place is stricter, so an accented pick
    would abort a rollover halfway through the intake. Filtered once, up front, rather than
    caught per name: a pool that silently loses a few spellings is better than a rollover that
    stops on the eleventh team.
    """
    return bool(part) and len(part) <= 40 and all(32 <= ord(c) < 127 for c in part)


def _fresh_name(rng, first_pool, last_pool, taken):
    for _ in range(200):
        first, last = rng.choice(first_pool), rng.choice(last_pool)
        if f"{first} {last}" not in taken:
            return first, last
    raise AgeOutError("could not find an unused name after 200 tries")


def plan(key, season, store=None, save_path=None):
    """Who would retire and how many newcomers each team needs. Reads only; writes nothing."""
    if key not in AGE_CAPS:
        return {"league": key, "capped": False, "retiring": [], "intake": 0, "teams": {}}
    spec = cfg.BY_KEY[key]
    path = Path(save_path) if save_path else DOCS / "leaguedata" / spec.save_name / "league.dat"
    L = LeagueDat(path)
    keep = protected_names(key, store)
    cap = AGE_CAPS[key]
    season = playing_season(season)      # the season being set up, not the one just played

    teams = L.teams()
    on_team = {}
    for t, info in teams.items():
        on_team[t] = [p for p in L.players if p.id in set(info["ids"])]

    retiring, short = [], {}
    for t, roster in on_team.items():
        going = [p for p in roster if p.name not in keep and age_of(p, season) >= cap]
        # Never strip a team to nothing, whatever the ages say. release() refuses it anyway; this
        # keeps the plan honest rather than letting apply() discover it halfway through.
        if len(going) >= len(roster):
            going = sorted(going, key=lambda p: -age_of(p, season))[:len(roster) - 1]
        for p in going:
            retiring.append({"name": p.name, "dob": p.dob, "age": age_of(p, season), "team": t})
        short[t] = spec.roster_size - (len(roster) - len(going))

    pool = [p for p in L.players
            if p.values["Team"] < 1 and p.name not in keep]
    return {"league": key, "capped": True, "cap": cap, "season": int(season),
            "retiring": retiring, "intake": sum(max(0, n) for n in short.values()),
            "teams": short, "pool": len(pool), "rostered": sum(len(r) for r in on_team.values())}


def apply(key, season, store=None, save_path=None, dry_run=False, log=print, seed=None):
    """Retire the over-age, refill with a new intake, and write the save.

    Returns the same shape `plan` does, plus what actually happened. `dry_run` does every lookup
    and every decision and then throws the edits away without saving.
    """
    if key not in AGE_CAPS:
        log(f"   {key}: no age cap (a pro career ends by retirement, not by graduating)")
        return {"league": key, "capped": False, "retired": [], "arrived": []}

    spec = cfg.BY_KEY[key]
    path = Path(save_path) if save_path else DOCS / "leaguedata" / spec.save_name / "league.dat"
    L = LeagueDat(path)
    keep = protected_names(key, store)
    cap, intake_age = AGE_CAPS[key], INTAKE_AGE[key]
    # Everything below is measured in the season this rollover is setting up. See
    # playing_season: measured in the season just finished, every 17-year-old survives the
    # rollover and plays the next one at 18, which is the exact thing this module exists to stop.
    season = playing_season(season)
    rng = random.Random(seed if seed is not None else (hash((key, int(season))) & 0xFFFFFFFF))
    first_pool, last_pool = gen.name_pools()
    first_pool = [n for n in first_pool if _codec_safe(n)]
    last_pool = [n for n in last_pool if _codec_safe(n)]
    if not first_pool or not last_pool:
        raise AgeOutError("no codec-safe names left in the name pools")
    towns = gen.hometowns()

    # ---- 1. retire the over-age ------------------------------------------------------------
    # Identity first, edits second: release() splices and re-parses, so the list is gathered as
    # (name, dob) pairs and each one re-found immediately before it is touched.
    doomed = []
    for t, info in L.teams().items():
        roster = [p for p in L.players if p.id in set(info["ids"])]
        going = [p for p in roster if p.name not in keep and age_of(p, season) >= cap]
        if len(going) >= len(roster):
            going = sorted(going, key=lambda p: -age_of(p, season))[:len(roster) - 1]
        doomed.extend((p.name, p.dob, age_of(p, season), t) for p in going)

    retired = []
    for name, dob, age, team in doomed:
        try:
            pl = L.find(name, dob)
        except CodecError as exc:
            log(f"   ! {name} ({dob}) could not be re-found: {exc}")
            continue
        # set() does not splice, so these are safe to batch and `pl` stays valid through them.
        for field_name in RATINGS:
            L.set(pl, field_name, FLOOR_RATING)
        for field_name in POTENTIALS:
            L.set(pl, field_name, FLOOR_POTENTIAL)
        try:
            L.release(pl)                       # splices; pl is stale from here
        except CodecError as exc:
            log(f"   ! {name} stayed on team {team}: {exc}")
            continue
        retired.append({"name": name, "dob": dob, "age": age, "team": team})

    # ---- 2. refill with a new intake --------------------------------------------------------
    arrived = []
    for t in sorted(L.teams()):
        while True:
            info = L.teams()[t]
            if len(info["ids"]) >= spec.roster_size:
                break
            roster = [p for p in L.players if p.id in set(info["ids"])]
            position = _needed_position([p.values["Position"] for p in roster], spec)
            taken = {p.name for p in L.players}
            # The deadest wood first: the oldest unrostered player nobody will ever sign. That is
            # also the pile this is meant to drain.
            pool = sorted((p for p in L.players
                           if p.values["Team"] < 1 and p.name not in keep),
                          key=lambda p: -age_of(p, season))
            if not pool:
                log(f"   ! {key} team {t} is short and the free-agent pool is empty")
                break
            source = pool[0]
            old_name, old_dob = source.name, source.dob
            first, last = _fresh_name(rng, first_pool, last_pool, taken)
            row = gen.make_player(rng, spec, spec.teams[0], position, intake_age,
                                  first_pool, last_pool, towns)

            pl = L.find(old_name, old_dob)
            # Numbers first, all of them, while `pl` is still valid: none of these splice.
            L.set(pl, "BirthYear", int(season) - intake_age)
            L.set(pl, "BirthMonth", rng.randint(1, 12))
            L.set(pl, "BirthDay", rng.randint(1, 28))
            L.set(pl, "Position", POSITION_CODE[position])
            L.set(pl, "Height", int(row["Height"]))
            L.set(pl, "Weight", int(row["Weight"]))
            L.set(pl, "Exp", 0)
            L.set(pl, "Inactive", 0)
            for field_name in RATINGS:
                if field_name in row:
                    L.set(pl, field_name, int(row[field_name]))
            for field_name in POTENTIALS:
                if field_name in row:
                    L.set(pl, field_name, int(row[field_name]))
            new_dob = pl.dob
            L.rename(pl, first, last)           # splices
            pl = L.find(f"{first} {last}", new_dob)
            try:
                L.sign(pl, t)                   # splices
            except CodecError as exc:
                log(f"   ! {first} {last} could not join team {t}: {exc}")
                break
            arrived.append({"name": f"{first} {last}", "dob": new_dob, "position": position,
                            "team": t, "recycled_from": old_name})

    log(f"   {key}: {len(retired)} aged out at {cap}+, {len(arrived)} joined at {intake_age}")
    if not dry_run and (retired or arrived):
        L.save(backup_dir=BACKUPS)
    elif dry_run:
        log(f"   {key}: DRY RUN - nothing was written")
    return {"league": key, "capped": True, "cap": cap, "intake_age": intake_age,
            "season": int(season), "retired": retired, "arrived": arrived, "dry_run": dry_run}
