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

NOT RELEASED IS NOT THE SAME AS NOT AGED, and for a long time this module confused the two. An
unclaimed reserve seat must survive every rollover, but nothing ever reset its age, so the seats
aged with the universe until nineteen of prep's forty-eight were 19 in a league that ends at 18 -
on rosters, playing. A new character also inherits the BIRTHDAY of the seat he claims, so the next
kid to sign up would have started already too old for his own league. `_rebase_reserves` now gives
an unclaimed seat its youth back when it reaches the cap. It is still never released.

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
MANIFEST = ROOT / "universe" / "manifest.json"
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
# Mirrors tools/protect_rosters.LEAVE_ALONE: these three are never floored, so they
# cannot be used to tell a defanged body from a real one.
_LEAVE_ALONE = {"3pUsage", "Fouling", "Stamina"}

# A DEFANGED BODY DOES NOT STAY AT THE FLOOR. The game's own progression nudges him upward every
# offseason, so asking for exactly FLOOR_RATING finds him only until the first rollover. Pro is
# the league that rolls over with progression AND free agency, and it came back with its floored
# bodies smeared across 3, 4, 5, 6 and 7 while the detector still demanded 2 - so 145 of 300 pro
# STARTERS were floored and invisible to their own repair. Unevenly, too: 4 of 15 on one team and
# 11 of 15 on another, which is exactly why the records were lopsided.
#
# Two gates, measured on the live saves, and both must agree:
#
#   * NOTHING UNDER 8 WAS EVER GENERATED. The filler bands are prep 8-38, college 18-52, pro
#     28-62, so a body whose best core skill is 7 or less did not come out of `gen.make_player`;
#     he was floored and has drifted. 7 is the largest value that is below every band's floor.
#   * A FLOORED BODY IS FLAT. All 378 pro bodies at or under 7 have a max-min spread of 6 or
#     less, while a real player from 13 up has a median spread of 21 and a maximum of 79. The
#     spread gate is what stops a genuinely poor player being regenerated out from under himself.
FLOOR_DRIFT = 7
FLOOR_SPREAD = 6
# A FLOORED BODY DOES NOT DRIFT EVENLY, which the first version of this assumed. Rudy Schreck sat
# on a pro roster with thirteen of his fifteen core ratings at 2-4 and Jumping alone at 17: the
# game's progression moves individual ratings at different rates, so a max-and-spread test let him
# through while he was plainly filler. Nate found him by looking at a roster.
#
# Two tests now, and either is enough:
#
#   * BELOW WHAT THE GENERATOR CAN MAKE. `generated_floor` samples `gen.make_player` for the
#     league and takes the lowest mean it produces, less a little margin. A body under that was
#     not generated for this league - it was floored. Measured against the live saves this splits
#     cleanly: in pro the worst body it keeps is 30.3 and the best it catches 13.5; in college
#     29.7 against 13.0. Nothing sits in between.
#   * MOSTLY AT THE FLOOR LINE. A body with 60% or more of its core ratings still at or under
#     FLOOR_DRIFT is filler whatever its mean - the Rudy Schreck case, and the safety net if a
#     band is ever widened far enough for the first test to go slack.
FLOOR_MOSTLY = 0.6


def _RESTORABLE_TEAMS(pl):
    """Rosters and free agency, never the draft pool (-2) and never a retired/unused row."""
    t = pl.values["Team"]
    return {t} if (t >= 1 or t == -1) else set()


def generated_floor(spec, samples=300, margin=0.92, seed=11):
    """The lowest mean overall `gen.make_player` produces for this league, less a little margin.

    Sampled rather than written down: the bands live in config, and a number typed in here would
    silently stop matching the day one of them moved.
    """
    from .universe import generate as gen
    rng = random.Random(seed)
    first_pool, last_pool = gen.name_pools()
    towns = gen.hometowns()
    skill = [f for f in RATINGS if f not in _LEAVE_ALONE]
    age = (spec.age_range[0] + spec.age_range[1]) // 2
    lowest = None
    for _ in range(samples):
        row = gen.make_player(rng, spec, spec.teams[0], "C", age, first_pool, last_pool, towns)
        vals = [int(row[f]) for f in skill if f in row]
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        lowest = mean if lowest is None else min(lowest, mean)
    return (lowest or 0) * margin


def is_defanged(values, skill, floor_mean=None):
    """True when this body was floored by the roster guard, however far it has since drifted."""
    vals = [values[f] for f in skill]
    if sum(1 for v in vals if v <= FLOOR_DRIFT) / len(vals) >= FLOOR_MOSTLY:
        return True
    if floor_mean is not None:
        return sum(vals) / len(vals) < floor_mean
    # With no league to compare against, only the structural test can be applied.
    return max(vals) <= FLOOR_DRIFT


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
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
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

    # The unclaimed reserve seats this rollover would make young again, so the PREVIEW says so.
    # The real run re-ages seats and rewrites the git-tracked universe/manifest.json, and a dry
    # run that reported only "N would age out, M would arrive" left both of those unannounced.
    from .characters import codec_dob
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rebasing = []
    for row in manifest["players"]:
        if row["league"] != key or row["role"] != "reserve":
            continue
        try:
            pl = L.find(row["name"], codec_dob(row["dob"]))
        except Exception:      # noqa: BLE001 - a claimed seat is renamed, so it is not found
            continue
        if age_of(pl, season) >= cap:
            rebasing.append({"name": pl.name, "team": row["team"], "age": age_of(pl, season)})

    return {"league": key, "capped": True, "cap": cap, "season": int(season),
            "retiring": retiring, "intake": sum(max(0, n) for n in short.values()),
            "teams": short, "pool": len(pool), "rostered": sum(len(r) for r in on_team.values()),
            "rebasing": rebasing}


def audit(key, season, store=None, save_path=None):
    """Prove the post-rollover roster has the intended size and no ordinary over-age player."""
    spec = cfg.BY_KEY[key]
    path = Path(save_path) if save_path else DOCS / "leaguedata" / spec.save_name / "league.dat"
    league = LeagueDat(path)
    keep = protected_names(key, store)
    over = [p.name for p in league.players
            if p.values["Team"] >= 1 and age_of(p, int(season)) >= AGE_CAPS[key]
            and p.name not in keep]
    sizes = {team: len(info["ids"]) for team, info in league.teams().items()}
    return {"over_age": over, "sizes": sizes,
            "ok": not over and set(sizes.values()) == {spec.roster_size}}


def _rebase_reserves(L, key, season, cap, intake_age):
    """Give an UNCLAIMED reserve seat its youth back instead of letting it age out of the league.

    A reserve row is the seat a new signup is stamped into, and `protected_names` keeps it off
    every release list so that the next person to join still has one. Nothing ever reset its AGE,
    though, and the seats quietly aged with the universe: measured against the live prep save for
    season 2030, nineteen of prep's forty-eight were already 19 in a league that ends at 18. They
    sit on rosters, so they were playing there - the exact thing this module exists to stop,
    arriving through the one door it deliberately leaves open.

    Worse than the standings: `characters.stamp_character` gives a new character the BIRTHDAY OF
    THE SLOT HE CLAIMS. The next kid to sign up for prep would have started his career already
    too old for the league he was joining, and no part of the signup flow would have said so.

    ONLY UNCLAIMED ROWS ARE TOUCHED, and findability is the test: `stamp_character` renames the
    row to the character's name, so a row still answering to the manifest's own generated name
    and date is a seat nobody holds. That needs no store, works in a dry run, and cannot mistake
    a real person for a seat - a claimed row simply is not found.

    THE SEAT KEEPS ITS NAME, and that is deliberate. Changing the date alone does mean a seat
    that has been rostered for years is archived under two `statsarchive._identity` values -
    (name, dob) - so a long-lived one can show up twice on the all-time page under the one name.
    Renaming it the way the intake path renames a recycled body would tidy that up, and it was
    tried: it breaks the guarantee that matters more. `protected_names`, `characters.free_slots`
    and `tests/test_age_out.py` all identify a seat BY NAME, so renaming reads as the seat having
    been recycled away - and in the copy case, where the manifest is deliberately not rewritten,
    the seat really would be lost. A duplicate row on a leaderboard is worth less than the
    guarantee that the next person to sign up has somewhere to go.

    Only the YEAR moves; the day and month are kept so the row stays recognisably itself. The
    manifest is rewritten to match, because it is how the codec finds the slot again - a date
    that moved in the save but not in the manifest could never be claimed or refilled.

    Returns (rebased rows, the updated manifest). The caller writes the manifest, and only after
    `L.save()` has succeeded, so the two never disagree.
    """
    from .characters import codec_dob
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    year = int(season) - intake_age
    rebased = []
    for row in manifest["players"]:
        if row["league"] != key or row["role"] != "reserve":
            continue
        try:
            pl = L.find(row["name"], codec_dob(row["dob"]))
        except Exception:      # noqa: BLE001 - claimed, renamed, or absent; none of them ours
            continue
        was = age_of(pl, season)
        if was < cap:
            continue
        fresh = f'{int(pl.values["BirthMonth"])}/{int(pl.values["BirthDay"])}/{year}'
        # `set` only pokes bytes - no splice, so no handle goes stale here.
        L.set(pl, "BirthYear", year)
        # EXPERIENCE IS A FUNCTION OF AGE, and moving the birthday without it leaves the seat
        # carrying the seasons it played at its old age: generate.py builds a body with
        # `max(0, age - age_range[0])`, and the intake path already zeroes it for exactly this
        # case - a recycled body made young again. `stamp_character` never writes Exp at all, so
        # whatever is left here is what the NEXT SIGNUP INHERITS, and a fourteen-year-old with
        # five seasons behind him is a different player to the engine: development, AI evaluation
        # and contracts all read it.
        L.set(pl, "Exp", 0)
        rebased.append({"name": pl.name, "team": row["team"], "age_was": was,
                        "was": row["dob"], "dob": fresh})
        row["dob"] = fresh
    return rebased, manifest

def _write_manifest(manifest):
    """Temp file then replace. See the note at the manifest write in apply()."""
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    tmp.replace(MANIFEST)


def sync_manifest(L, key, keep, manifest):
    """Make this league's FILLER rows say who is actually on its rosters.

    WHY THIS EXISTS. The manifest is the list of bodies the universe considers its own, and
    `tools/protect_rosters.py` defangs and evicts everybody it does not name. Nothing ever added
    the intake to it: `apply()` recycles a free-agent body, renames it and signs it to a team,
    and the manifest never hears about it - so the very next roster guard saw a stranger on a
    roster and threw it off again. That is the loop that emptied the rosters, and it is why the
    guard still finds 72 strangers in prep and 93 in college on EVERY sim week, releasing and
    re-signing the same people and rewriting a 5-10 MB save each time for no change at all.

    Registering them closes it: the guard finds nothing to do and short-circuits.

    RESERVE ROWS ARE NEVER TOUCHED HERE. They are signup seats, not bodies - `free_slots` and
    `stamp_character` find them by the name and date recorded here, so rewriting one loses a
    seat. Neither are characters: they live on a reserve row under their own name, and `keep`
    holds both them and the seats.

    Rebuilt rather than patched, so it is self-healing. Three seasons of drift left 79 rows in
    prep and 93 in college pointing at players who no longer exist, and a rule that only appends
    would carry those forever.

    Returns (manifest, registered, dropped). The caller writes it, and only for the real save.
    """
    spec = cfg.BY_KEY[key]
    ids = sorted({p.values["Team"] for p in L.players if p.values["Team"] >= 1})
    abbrev_of = {tid: spec.teams[i].abbrev for i, tid in enumerate(ids) if i < len(spec.teams)}
    was = {(r["name"], r["dob"]): r for r in manifest["players"]
           if r["league"] == key and r["role"] == "filler"}

    rows = []
    for pl in L.players:
        if pl.values["Team"] < 1 or pl.name in keep:
            continue                       # in the pool, or a seat/character - not a filler row
        old = was.get((pl.name, pl.dob))
        rows.append({"league": key, "team": abbrev_of.get(pl.values["Team"], ""),
                     "role": "filler", "name": pl.name, "dob": pl.dob,
                     "position": POSITION_NAME.get(pl.values["Position"], ""),
                     "uniform": (old or {}).get("uniform", 0)})

    now = {(r["name"], r["dob"]) for r in rows}
    registered = [r["name"] for r in rows if (r["name"], r["dob"]) not in was]
    dropped = [r["name"] for k, r in was.items() if k not in now]
    manifest["players"] = ([r for r in manifest["players"]
                            if not (r["league"] == key and r["role"] == "filler")] + rows)
    return manifest, registered, dropped


def reconcile(key, save_path=None, store=None, log=print, restore_ratings=True, seed=None,
              reserves=False):
    """Register today's rosters, and give back the ratings the guard took off them.

    The repair for drift that has already happened. Two halves, and the second is why it writes
    the save as well as the manifest.

    A body the manifest does not name is DEFANGED to floor ratings every sim week, and the intake
    was never registered - so a third of prep and college ended up rated 2 across the board. 81
    and 93 players respectively, on rosters, playing games. A league of floor-rated bodies is not
    a league: the point of the AI population is that a character has somebody to face, and
    minutes are supposed to be earned against real opposition rather than handed over by default.

    So every newly-registered body that is sitting at the floor is regenerated into its league's
    own filler band - `spec.ratings` and `spec.potentials`, the same generator that made the
    original population - at its CURRENT age, not reset to an intake age. Bodies that already
    have real ratings are left exactly as they are.

    Never touches a character or a reserve seat: both are in `keep`, and `sync_manifest` excludes
    them from the filler rows this works from.
    """
    spec = cfg.BY_KEY[key]
    path = Path(save_path) if save_path else DOCS / "leaguedata" / spec.save_name / "league.dat"
    L = LeagueDat(path)
    keep = protected_names(key, store)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest, registered, dropped = sync_manifest(L, key, keep, manifest)

    restored = []
    if restore_ratings:
        # A DORMANT RESERVE SEAT IS STILL A BODY ON A ROSTER. The seats are deliberately floored
        # so a recycled one cannot come back as a departed character's fully developed player and
        # take rotation minutes off a real person - that is the bug `reset_reserve` exists to stop
        # and it stays stopped, because a claimed seat is overwritten by the character's own
        # ratings the moment he is stamped onto it.
        #
        # But floored is not the same as dormant, and they are not sitting quietly: measured on
        # the live saves, pro carried 58 seats on rosters with 16 of them ON A DEPTH CHART and 54
        # in the active lineup. Those are rating-3 placeholders taking real minutes, about three
        # per team, and they are what "half these teams have 3 overall players" actually looked
        # like once the drifted filler had been repaired.
        #
        # So a seat is regenerated into its league's own band like any other filler - decent, not
        # a star, the same `gen.make_player` draw everyone else gets. CHARACTERS ARE STILL NEVER
        # TOUCHED; only the reserve rows come out of the keep set.
        rng = random.Random(seed if seed is not None else (hash((key, "reconcile")) & 0xFFFFFFFF))
        first_pool, last_pool = gen.name_pools()
        towns = gen.hometowns()
        skill = [f for f in RATINGS if f not in _LEAVE_ALONE]
        floor_mean = generated_floor(spec)
        people = {f'{c["first_name"]} {c["last_name"]}'
                  for c in (store.characters(league=key) if store is not None else [])}
        ratings_keep = people if reserves else keep
        # Season only decides the age we regenerate him AT; read it off the save so a repair run
        # outside an offseason still gets it right.
        season = L.season_day()[1]
        for pl in L.players:
            # ANY rostered filler at the floor, not only the ones registered just now. Nine
            # prep bodies were already in the manifest and still rated 2 - defanged by some
            # earlier pass and never given back - and skipping them would leave rating-2 players
            # in the rotation for exactly the reason this function exists to fix.
            # Team >= 1 is a roster, Team == -1 is free agency. NOT `< 1`, which also catches
            # -2, the game's own DRAFT POOL - regenerating a draft record would rewrite the class
            # our characters are drafted against, and the pool is where college's outgoing
            # seniors are carried. Free agents are included because the next roster gap is filled
            # from them, so leaving them floored puts the problem straight back on a roster.
            if pl.name in ratings_keep or pl.values["Team"] not in _RESTORABLE_TEAMS(pl):
                continue
            if not is_defanged(pl.values, skill, floor_mean):
                continue                   # he kept his ratings; leave him alone
            row = gen.make_player(rng, spec, spec.teams[0],
                                  POSITION_NAME.get(pl.values["Position"], "C"),
                                  max(spec.age_range[0], min(spec.age_range[1],
                                                             age_of(pl, season))),
                                  first_pool, last_pool, towns)
            for field in RATINGS:
                if field in row:
                    L.set(pl, field, int(row[field]))
            for field in POTENTIALS:
                if field in row:
                    L.set(pl, field, int(row[field]))
            restored.append(pl.name)

    log(f"   {key}: {len(registered)} registered, {len(dropped)} stale row(s) dropped, "
        f"{len(restored)} defanged body/bodies given real ratings")
    if save_path is None:
        if restored:
            L.save(backup_dir=BACKUPS)
        if registered or dropped:
            _write_manifest(manifest)
    elif registered or dropped or restored:
        if restored:
            L.save(backup_dir=BACKUPS)     # the copy may be written; the one manifest may not
        log(f"   {key}: copy run - the manifest was left alone")
    return {"league": key, "registered": registered, "dropped": dropped, "restored": restored}

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
    # One splice per TEAM, not one per PLAYER. A splice re-parses all 425 player records, so the
    # old loop spent 346 seconds per league doing the same parse dozens of times.
    release_groups = {}
    for team in sorted({row[3] for row in doomed}):
        rows = [row for row in doomed if row[3] == team]
        players, accepted = [], []
        for name, dob, age, _team in rows:
            try:
                pl = L.find(name, dob)
            except CodecError as exc:
                log(f"   ! {name} ({dob}) could not be re-found: {exc}")
                continue
            for field_name in RATINGS:
                L.set(pl, field_name, FLOOR_RATING)
            for field_name in POTENTIALS:
                L.set(pl, field_name, FLOOR_POTENTIAL)
            players.append(pl)
            accepted.append({"name": name, "dob": dob, "age": age, "team": team})
        if players:
            release_groups[team] = players
            retired.extend(accepted)
    try:
        L.release_groups(release_groups)
    except CodecError as exc:
        raise AgeOutError(f"over-age players could not be released: {exc}") from exc

    # FBPB3 expands teams to a 17-20 man camp roster during its native offseason. The next sim
    # eventually cuts them, but the commissioner must publish and verify a real roster now.
    # Release the weakest ordinary fillers in one batch per team; protected character/reserve
    # rows are never candidates, and camp cuts keep their ratings because they have not aged out.
    camp_cuts, cut_groups = [], {}
    teams = L.teams()
    for team in sorted(teams):
        info = teams[team]
        excess = len(info["ids"]) - spec.roster_size
        if excess <= 0:
            continue
        roster = [p for p in L.players if p.id in info["ids"]]
        candidates = [p for p in roster if p.name not in keep]
        candidates.sort(key=lambda p: (sum(p.values.get(f, 0) for f in RATINGS), p.name))
        cut = candidates[:excess]
        if len(cut) != excess:
            raise AgeOutError(f"team {team} has {excess} camp extras but only {len(cut)} safe cuts")
        cut_groups[team] = cut
        camp_cuts.extend({"name": p.name, "dob": p.dob, "team": team} for p in cut)
    L.release_groups(cut_groups)

    # ---- 2. refill with a new intake --------------------------------------------------------
    arrived, pending = [], []
    teams = L.teams()
    # FREE AGENTS ONLY, WHICH IS TEAM -1. `Team < 1` also catches -2, and -2 is the game's DRAFT
    # POOL - 70 of college's 190 non-rostered records and 65 of prep's 185. Recycling one into
    # roster filler consumes a draft record permanently, draining a pool we do not own and which
    # the pro league now carries our outgoing college seniors into. `tools/protect_rosters.py`
    # already had this right, selecting `== -1` for the same job; the two disagreed and this was
    # the side that was wrong.
    pool = sorted((p for p in L.players if p.values["Team"] == -1 and p.name not in keep),
                  key=lambda p: -age_of(p, season))
    taken = {p.name for p in L.players}
    for t in sorted(teams):
        roster = [p for p in L.players if p.id in teams[t]["ids"]]
        for _ in range(max(0, spec.roster_size - len(roster))):
            if not pool:
                raise AgeOutError(f"{key} team {t} is short and the free-agent pool is empty")
            source = pool.pop(0)
            old_name = source.name
            position = _needed_position([p.values["Position"] for p in roster], spec)
            first, last = _fresh_name(rng, first_pool, last_pool, taken)
            taken.add(f"{first} {last}")
            row = gen.make_player(rng, spec, spec.teams[0], position, intake_age,
                                  first_pool, last_pool, towns)
            L.set(source, "BirthYear", int(season) - intake_age)
            L.set(source, "BirthMonth", rng.randint(1, 12))
            L.set(source, "BirthDay", rng.randint(1, 28))
            L.set(source, "Position", POSITION_CODE[position])
            L.set(source, "Height", int(row["Height"]))
            L.set(source, "Weight", int(row["Weight"]))
            L.set(source, "Exp", 0)
            L.set(source, "Inactive", 0)
            for field_name in RATINGS:
                if field_name in row:
                    L.set(source, field_name, int(row[field_name]))
            for field_name in POTENTIALS:
                if field_name in row:
                    L.set(source, field_name, int(row[field_name]))
            pending.append((source, first, last, source.dob, position, t, old_name))
            roster.append(source)  # position balance for the next arrival on this team

    # Names have variable byte lengths; descending edits make every rename one parse. Roster
    # storage is fixed-width, so all signings likewise need only one final parse.
    L.rename_many((pl, first, last) for pl, first, last, *_ in pending)
    signings = []
    for _source, first, last, dob, position, t, old_name in pending:
        pl = L.find(f"{first} {last}", dob)
        signings.append((pl, t))
        arrived.append({"name": pl.name, "dob": dob, "position": position,
                        "team": t, "recycled_from": old_name})
    L.sign_many(signings)

    # ---- 3. the unclaimed reserve seats get their youth back --------------------------------
    rebased, manifest = _rebase_reserves(L, key, season, cap, intake_age)

    # ---- 4. tell the manifest who is on the rosters now -------------------------------------
    # Without this the intake signed above is a stranger to tools/protect_rosters.py, which
    # evicts it on the next sim week - the loop that emptied the rosters.
    manifest, registered, dropped = sync_manifest(L, key, keep, manifest)

    log(f"   {key}: {len(retired)} aged out at {cap}+, {len(arrived)} joined at {intake_age}")
    if rebased:
        log(f"   {key}: {len(rebased)} unclaimed reserve seat(s) reset to {intake_age}")
    if registered or dropped:
        log(f"   {key}: manifest now names {len(registered)} new filler(s), "
            f"{len(dropped)} stale row(s) dropped")
    if not dry_run and (retired or arrived or rebased):
        L.save(backup_dir=BACKUPS)
        # AFTER the save, never before: a manifest pointing at a date the save does not have is a
        # slot nobody can claim or refill.
        #
        # AND ONLY FOR THE REAL SAVE. `save_path` means somebody handed us a copy - a rehearsal,
        # or tests/test_age_out.py, which runs the real thing against a duplicate on purpose.
        # There is ONE manifest, and rewriting it from a copy would move the live universe's
        # slot dates to match dates only the copy has, breaking every future claim and refill.
        if (rebased or registered or dropped) and save_path is None:
            # TEMP FILE THEN REPLACE, like league_dat.save, statsarchive.save and
            # gamesarchive.save. A truncating write is the wrong shape for this file:
            # protected_names, characters.free_slots, offseason.refill and the signup placement
            # all do a bare json.loads on it, so an interrupted write is not a stale manifest,
            # it is no manifest - no signup can be placed and the next age-out cannot run.
            _write_manifest(manifest)
        elif rebased or registered or dropped:
            log(f"   {key}: copy run - the manifest was left alone, so its reserve dates "
                f"no longer match this file")
    elif dry_run:
        log(f"   {key}: DRY RUN - nothing was written")
    return {"league": key, "capped": True, "cap": cap, "intake_age": intake_age,
            "season": int(season), "retired": retired, "arrived": arrived,
            "camp_cuts": camp_cuts, "rebased_reserves": rebased,
            "registered": registered, "dropped": dropped, "dry_run": dry_run}
