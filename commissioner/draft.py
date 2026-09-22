"""Draft night: who each team wants, and why.

WHAT THIS REPLACES. `run_draft` used to sort everybody by one global `_promise` score and hand
pick *i* to `order[i % len(order)]`. Every team wanted exactly the same player, in the same
order, so "the draft" was a ranked list with team names stapled to it. Nothing about a team
entered into it: not its roster, not its record, not what it already had four of.

THE REASON PRINTED IS THE REASON USED. Every pick here comes out of `evaluate`, which returns the
score AND the term that decided it, so the line that reaches Discord is generated from the same
arithmetic that made the choice. Nothing is written twice, which is the only way a stated reason
stays true after somebody tunes the weights.

Pure functions, no I/O. The whole board can be built and tested without Discord, without the game,
and without touching a save - which matters, because draft night has never once run on the live
universe and the first time it does it will be moving real people between leagues.
"""
from __future__ import annotations

import hashlib

from . import ageout
from . import characters as ch
from .codec.league_dat import POTENTIALS, RATINGS
from .universe import config as cfg

# What a front office is FOR, in the only terms the sheet actually has. Each profile weights the
# three things that can be known about a prospect; the weights sum to roughly 1 so scores stay
# comparable across teams, and the leftover is what makes them disagree.
#
# These are not flavour text. `evaluate` multiplies by them, and the reason names whichever term
# won - so a "wins now" team saying it took the ready-made player is a statement about the sum.
PROFILES = {
    "upside": {
        "label": "bets on upside",
        "weights": {"ceiling": 0.62, "now": 0.24, "fit": 0.14},
        "line": "{team} always draft the ceiling.",
    },
    "win_now": {
        "label": "wants it now",
        "weights": {"ceiling": 0.20, "now": 0.66, "fit": 0.14},
        "line": "{team} draft for right now, not for later.",
    },
    "need_first": {
        "label": "drafts the hole",
        "weights": {"ceiling": 0.28, "now": 0.32, "fit": 0.40},
        "line": "{team} fill the hole first and argue about it later.",
    },
    "best_available": {
        "label": "takes the best player",
        "weights": {"ceiling": 0.42, "now": 0.53, "fit": 0.05},
        "line": "{team} do not draft for need.",
    },
}
ORDERED_PROFILES = sorted(PROFILES)


def team_profile(abbrev):
    """Which kind of front office this team is. Stable for the life of the universe.

    Derived from a hash of the abbreviation rather than stored anywhere, so a team has the same
    temperament every draft without a migration, a settings key, or a file somebody has to keep
    in step with the team list. md5 rather than hash() because Python salts hash() per process -
    the Wheels would have been a different team every time the panel restarted.
    """
    digest = hashlib.md5(str(abbrev).encode("utf-8")).digest()
    return ORDERED_PROFILES[digest[0] % len(ORDERED_PROFILES)]


def team_need(roster_positions, spec):
    """The position this team is shortest of.

    Straight through to `ageout._needed_position`, which already answers exactly this against the
    shape `generate.FILLER_POSITIONS` builds a roster in. Wrapped rather than reimplemented so the
    draft and the age-out intake can never drift into two different opinions about what a team is
    missing - which would show up as a team drafting a guard to fill a hole the intake had just
    filled with a centre.
    """
    return ageout._needed_position(roster_positions, spec)


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def sheet(character):
    """(now, ceiling) for a prospect, each on the ratings scale.

    `ceiling` reads the potentials through `ch.codec_potentials`. That translation is the whole
    reason this is a function: a stored potential is keyed by its RATING name, not the codec's
    `PotInside` form, and reading it with the codec's names scores every ceiling as zero - which
    on draft night is the difference between a lottery pick and going undrafted. The same trap is
    already commented in `offseason._promise`.
    """
    ratings = character.get("ratings") or {}
    now = _mean(ratings.get(f, 0) for f in RATINGS)
    pots = ch.codec_potentials(character.get("potentials"))
    ceiling = _mean(pots.get(f, 0) for f in POTENTIALS)
    # A ceiling below what he already is means we could not read one. Fall back to his present
    # self rather than pretending he has no future, which would sink him down the board.
    return now, max(ceiling, now)


def evaluate(character, need, profile_key):
    """(score, terms) for one prospect in front of one team.

    `terms` carries each weighted contribution, so the caller can name the one that decided it
    without recomputing anything.
    """
    profile = PROFILES.get(profile_key) or PROFILES["best_available"]
    weights = profile["weights"]
    now, ceiling = sheet(character)
    fits = bool(need) and str(character.get("position") or "") == str(need)
    # Fit is scored on the same scale as the other two so one weight set can balance them: a
    # perfect positional match is worth as much as being a 100-rated player, and no match is
    # worth nothing. Anything else and `fit` either never matters or always wins.
    fit = 100.0 if fits else 0.0
    terms = {
        "now": now * weights["now"],
        "ceiling": ceiling * weights["ceiling"],
        "fit": fit * weights["fit"],
    }
    return sum(terms.values()), {"terms": terms, "now": now, "ceiling": ceiling, "fits": fits}


def _height(character):
    inches = character.get("height_inches")
    try:
        feet, rest = divmod(int(inches), 12)
    except (TypeError, ValueError):
        return ""
    return f"{feet}'{rest}\""


def describe(character):
    """"Dodger Manson  C, 7'2"" - what a pick looks like on the board."""
    name = f'{character.get("first_name", "")} {character.get("last_name", "")}'.strip()
    bits = [b for b in (character.get("position"), _height(character)) if b]
    return f"{name}  {', '.join(bits)}" if bits else name


# What each profile is KNOWN for. Used to decide whether its catchphrase is honest on a given
# pick: a "fill the hole first" team that just took the highest ceiling on the board because it
# had no hole must not say it filled a hole. The catchphrase appears only when the term it
# advertises is the term that actually won.
PROFILE_TERM = {"upside": "ceiling", "win_now": "now", "need_first": "fit",
                "best_available": None}

# How close a runner-up has to be, as a share of the winning score, before he is worth mentioning.
# WHY A MARGIN AT ALL: the first version named the second name on every single pick, so a man who
# won by a landslide and a man who won by a rounding error read exactly the same. "The other name
# in the room" is a claim about how close it was, and printing it unconditionally makes it noise.
COIN_FLIP = 0.015
IN_THE_ROOM = 0.07


def _catchphrase_is_honest(profile_key, won):
    """Whether this profile's line can be said out loud on a pick decided by `won`.

    Three of the four lines are POSITIVE claims - "we draft the ceiling" - and are honest only
    when the term they advertise is the term that won. `best_available`'s is a NEGATIVE one, "we
    do not draft for need", which is true on every pick that fit did NOT decide. That is why it
    maps to None in PROFILE_TERM and needs its own branch rather than never speaking at all.
    """
    if profile_key == "best_available":
        return won != "fit"
    return PROFILE_TERM.get(profile_key) == won


def _stance(team, profile_key, won, detail, ctx):
    """What the team came in wanting - said honestly, including when they did not get it.

    A `need_first` team taking the highest ceiling on the board used to say NOTHING about its own
    temperament, because the catchphrase was suppressed and nothing replaced it. The pick that is
    most worth explaining is exactly the one where a team went against type, so the suppressed
    case gets a sentence of its own instead of silence.
    """
    profile = PROFILES.get(profile_key) or PROFILES["best_available"]
    if _catchphrase_is_honest(profile_key, won):
        return profile["line"].format(team=team)
    if profile_key != "need_first":
        return ""
    need = detail.get("need")
    if not need:
        return f"{team} fill the hole first when they have one. Tonight they had none."
    # They wanted a position and did not take it. WHICH of the two reasons it was has to come out
    # of the board, not out of a guess: either nobody left plays it, or somebody did and lost.
    if ctx.get("best_fit"):
        return f"{team} wanted a {need}, and passed on {ctx['best_fit']} to take him."
    return f"{team} wanted a {need}. Nobody left on the board plays one."


def reason_for(team, profile_key, detail, runner_up=None, ctx=None):
    """The team's thinking on one pick, generated from the term that actually decided it.

    Everything here is derived from `detail` and `ctx`, both of which came out of the same
    `evaluate` pass that made the choice - so tuning a weight changes the sentence too, and the
    reason cannot drift away from the arithmetic behind it. That is the whole contract of this
    module, and it is why nothing below is allowed to assert anything the board does not show.
    """
    ctx = ctx or {}
    terms = detail["terms"]
    won = max(terms, key=terms.get)
    left = ctx.get("left")
    if left == 1:
        # "Highest ceiling LEFT on the board" is technically true of a board with one man on it
        # and tells the room nothing. Say what actually happened.
        why = ("The only name in this draft." if ctx.get("pick") == 1
               else "The last name on the board.")
    elif won == "fit" and detail["fits"]:
        why = f"They were thinnest at {detail['need']} and he plays it."
    elif won == "ceiling":
        why = f"Highest ceiling left on the board ({detail['ceiling']:.0f})."
    else:
        why = f"Most ready to play right now ({detail['now']:.0f})."

    parts = [p for p in (_stance(team, profile_key, won, detail, ctx), why) if p]

    # The runner-up, and only when he was close enough for the word to mean something.
    margin, score = ctx.get("margin"), ctx.get("score")
    if runner_up and margin is not None and score:
        share = margin / score if score > 0 else 1.0
        if share < COIN_FLIP:
            parts.append(f"{runner_up} was a coin-flip away.")
        elif share < IN_THE_ROOM:
            parts.append(f"{runner_up} was the other name in the room.")
    elif runner_up and margin is None:
        parts.append(f"{runner_up} was the other name in the room.")
    return " ".join(parts)


# A man nobody takes is the other half of draft night, and it is the funnier half. These fire only
# once somebody has genuinely sat through SNUB_AFTER picks, so the joke is always carrying a true
# number - which is the same rule the reasons follow. A roast built on a made-up premise is just a
# bug that reads as a joke.
SNUB_AFTER = 3
SNUBS = (
    "{name} was the {ord} best player in this draft and it is pick {n}.",
    "Somebody is going to have to take {name} eventually. {n} picks, and it has not been any "
    "of them.",
    "{n} picks in, and {name} remains available. Extremely available.",
    "Still on the board: {name}, who most people had {n_back} picks ago.",
    "{name} has now watched {n_back} men go who were not supposed to go before him.",
    "The room has had {n} chances at {name} and taken none of them.",
)


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def talent_rank(board):
    """{id(prospect): where the flat sheet said he should go}. One ranking, not a team's.

    Deliberately NOT any front office's score. Every team sees the board through its own weights,
    so "he slid" only means something against a view that does not move - otherwise the sentence
    is just a restatement of whoever happened to be on the clock.
    """
    ordered = sorted(board, key=lambda c: (-sum(sheet(c)), describe(c)))
    return {id(c): i + 1 for i, c in enumerate(ordered)}


def snub_for(rest, expected, pick):
    """A line about the man sliding furthest, or None while it is not yet funny.

    WHY NOT "how many picks has he sat through". That was the first version, and on a full board
    it is the same number for everybody still available - so it always landed on whoever happened
    to be rated second, who was usually taken moments later. Sliding is the difference between
    where the sheet put a man and where the room actually is, which is the thing worth laughing at.
    """
    worst, slid = None, 0
    for _score, _detail, c in rest or ():
        gap = pick - expected.get(id(c), pick)
        if gap > slid:
            worst, slid = c, gap
    if worst is None or slid < SNUB_AFTER:
        return None
    name = describe(worst).split("  ")[0]
    digest = hashlib.md5(f"{name}|{pick}".encode("utf-8")).digest()
    return SNUBS[digest[0] % len(SNUBS)].format(
        name=name, n=pick, n_back=slid, ord=_ordinal(expected.get(id(worst), pick)))


# A man going one or two picks later than a flat average of his ratings predicted is not a story,
# it is rounding. Only report a real fall.
MIN_SLIDE = 3


def slides(picks):
    """Who went far later than his own sheet said he should. [(pick, name, slipped_by), …]

    Each pick carries the rank it was given against the WHOLE board, undrafted men included, so
    this must not recompute one from the drafted alone - that ranking closes the gaps left by
    everybody who went unpicked and quietly erases the biggest slides in the draft.
    """
    out = []
    for p in picks:
        want = p.get("expected")
        if not want:
            continue
        slipped = p["pick"] - want
        if slipped >= MIN_SLIDE:
            out.append((p["pick"], describe(p.get("character") or {}).split("  ")[0], slipped))
    out.sort(key=lambda r: -r[2])
    return out


# A real draft is two rounds and then the phone stops ringing. Used only as a FLOOR: the board
# always runs long enough to take every one of our characters, however far one of them slides,
# because a character who goes undrafted is never promoted, never placed and never paid - he just
# quietly stops existing. The field is what absorbs the cut instead.
DRAFT_ROUNDS = 2


def build_board(declared, order, needs=None, profiles=None, field=None, rounds=DRAFT_ROUNDS):
    """The whole draft, in pick order, each with the reason it happened.

    `needs` is {team abbrev: position} and `profiles` is {team abbrev: profile key}; both are
    optional so the board can be built from nothing but a list of characters - which is what makes
    this testable and what lets `tools/draft_preview.py` run with no save loaded.

    Returns dicts shaped like the ones `run_draft` already builds - round, pick, team, character -
    plus score, reason and runner_up, so the existing promote/`set_character_field` path underneath
    it does not change at all.
    """
    needs = needs or {}
    profiles = profiles or {}
    ours = {id(c) for c in declared}
    remaining = list(declared) + [c for c in (field or []) if id(c) not in ours]
    picks = []
    n = max(1, len(order))
    slot = 0
    # Fixed for the whole night, over the whole board: where a flat reading of the sheet said
    # each man should go. Recomputing it per pick would make "he slid" drift as the board emptied.
    expected = talent_rank(remaining)
    while remaining:
        # Stop once the rounds are used up AND every one of ours is off the board. Either
        # condition alone is wrong: cutting at the rounds can strand a character, and ignoring
        # them makes an eighty-man field into an eighty-pick draft.
        if slot >= rounds * n and not any(id(c) in ours for c in remaining):
            break
        team = order[slot % n]
        profile_key = profiles.get(team) or team_profile(team)
        need = needs.get(team)
        scored = []
        for c in remaining:
            score, detail = evaluate(c, need, profile_key)
            detail["need"] = need
            scored.append((score, detail, c))
        # Sort by score, then by name, so a tie is broken the same way every run. A draft that
        # reorders itself between a preview and the real thing is worse than no preview.
        scored.sort(key=lambda row: (-row[0], describe(row[2])))
        score, detail, best = scored[0]
        runner_up = describe(scored[1][2]).split("  ")[0] if len(scored) > 1 else None
        # The best man left who plays the position they said they needed - named only when he is
        # NOT the pick, because that is the one case worth a sentence.
        best_fit = next((describe(c).split("  ")[0] for _s, d, c in scored
                         if d["fits"] and c is not best), None)
        ctx = {
            "left": len(scored),
            "pick": slot + 1,
            "score": score,
            "margin": score - scored[1][0] if len(scored) > 1 else None,
            "best_fit": best_fit,
        }
        picks.append({
            "round": slot // n + 1,
            "pick": slot + 1,
            "team": team,
            "character": best,
            "score": round(score, 2),
            "profile": profile_key,
            "need": need,
            "reason": reason_for(team, profile_key, detail, runner_up, ctx),
            "runner_up": runner_up,
            "snub": snub_for(scored[1:], expected, slot + 1),
            "expected": expected.get(id(best)),
            # The one flag the write path turns on. A field prospect is announced and then
            # forgotten; only OUR people are promoted, stamped onto a roster and paid.
            "is_character": id(best) in ours,
        })
        # By identity, not by value: two prospects off the same blank pool can compare equal and
        # list.remove() would drop whichever one it found first, leaving the drafted man on the
        # board to be taken again.
        remaining = [c for c in remaining if c is not best]
        slot += 1
    return picks


def undrafted_from(declared, field, picks):
    """Everyone who was on the board when it ran out of picks. Ours are never in it."""
    taken = {id(p.get("character")) for p in picks}
    return [c for c in list(declared or ()) + list(field or ()) if id(c) not in taken]


# FBPB3's own draft pool sits at team -2 with Exp 0 - 80 records in CV_Pro, born across six
# years. It is a STANDING pool of blank records for most of the year: the game fills in a class
# during its own offseason, and until it does every one of them reads ~9 overall with every
# potential pinned at 5. Drafting that field would put eighty identical nobodies on the board and
# rank our characters against noise, so a pool that has not been generated is treated as no pool.
POOL_TEAM = -2
POOL_MIN_CEILING = 20.0


def field_from_save(league_dat, limit=None):
    """The game's own prospects, in character shape, so draft night is not a seven-man event.

    These are NOT ours and must never be promoted, stamped or paid - `build_board` marks every
    pick with `is_character` for exactly that reason. They are here to give our characters a real
    field to be measured against, somebody to slide behind, and somebody to leave undrafted.

    Returns [] when the class has not been generated yet, which is most of the calendar.
    """
    from .codec.league_dat import POSITIONS
    out = []
    for pl in league_dat.players:
        if pl.values.get("Team") != POOL_TEAM:
            continue
        first, _, last = str(pl.name).partition(" ")
        out.append({
            "id": f"pool-{pl.id or len(out)}",
            "first_name": first, "last_name": last,
            "role": "field", "position": POSITIONS.get(pl.values.get("Position"), ""),
            "height_inches": pl.values.get("Height"),
            "ratings": {f: pl.values.get(f, 0) for f in RATINGS},
            "potentials": {f: pl.values.get(f, 0) for f in POTENTIALS},
        })
    # READ THE RAW POTENTIALS, not sheet()'s ceiling. sheet() deliberately falls back to what a
    # man already is when it cannot read a ceiling, so a pool carrying ordinary ratings and
    # pinned potentials scores as a perfectly good draft class through that fallback. The pinned
    # potential IS the signal that the game has not filled the class in.
    if not out or max(_mean((c["potentials"] or {}).values()) for c in out) < POOL_MIN_CEILING:
        return []
    # Best first, so a `limit` keeps the prospects worth announcing rather than an arbitrary slice.
    out.sort(key=lambda c: (-sum(sheet(c)), describe(c)))
    return out[:limit] if limit else out


def needs_from_save(key, league_dat):
    """{team abbrev: the position that team is shortest of}, read off a loaded save.

    Separate from `build_board` on purpose: everything above is pure, and this is the one part
    that needs a save open. A caller with no save - a test, a preview on another machine - simply
    passes no needs and every team drafts on talent.
    """
    spec = cfg.BY_KEY[key]
    teams = league_dat.teams()
    ids = sorted(teams)
    abbrev_of = {tid: spec.teams[i].abbrev for i, tid in enumerate(ids) if i < len(spec.teams)}
    out = {}
    for tid, info in teams.items():
        abbrev = abbrev_of.get(tid)
        if not abbrev:
            continue
        positions = [p.values["Position"] for p in league_dat.players if p.id in set(info["ids"])]
        try:
            out[abbrev] = team_need(positions, spec)
        except Exception:      # noqa: BLE001 - a team we cannot read simply drafts on talent
            continue
    return out


# ---- the class the college league feeds -------------------------------------------------------
# FBPB3 generates its own draft class, but it does it DURING ITS OWN ROLLOVER - which runs AFTER
# our draft, not before (`_run_offseason` calls run_draft; `run_offseason` calls rollover_saves
# afterwards). So `field_from_save` has always asked a pool the game has not filled in yet and
# always got nothing back, and every draft night since the first one has read "the game's draft
# class is not generated yet; our people draft alone". That is not a timing accident that will
# come good on its own - the order guarantees it, every season, for ever.
#
# Writing the college league's outgoing seniors into that pool just before the draft is what
# makes the field real. It also makes it real with names the server has watched for four years
# instead of strangers the game invented, which is the whole point of running a ladder.
#
# NOTHING HERE IS PROMOTED, PAID OR STAMPED. These are pool records at team -2 in the PRO save;
# `build_board` marks every one of them `is_character: False` and the write path skips them.
# They exist to be drafted around, slid behind, and left undrafted.
#
# The game overwrites these slots when it generates its own class during the rollover that
# follows. That is fine and expected: they have done their job by then.

# The board is DRAFT_ROUNDS x teams picks deep, so forty fills every slot our draft can announce
# and leaves the game's untouched blanks below the cut, where a ~9-overall record belongs.
CARRY_LIMIT = 40


def _ascii_name(text):
    """Fold a name to what the codec's `rename` accepts - it refuses anything outside 32..126.

    Six of college's generated names carry accents (`Guc Dufault`, `Leopold Segard`,
    `Vitor Romeiro`), and `rename` raises CodecError on every one of them. Folding keeps the man
    recognisable where stripping would not: Sepulveda rather than Seplveda.
    """
    import unicodedata
    folded = unicodedata.normalize("NFKD", str(text or ""))
    kept = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join("".join(c for c in kept if 32 <= ord(c) < 127).split())


def outgoing_seniors(college, season, store=None, limit=CARRY_LIMIT):
    """The best college players ageing out at this rollover, in character shape, best first.

    EXACTLY THE SET `ageout.plan` RETIRES, and deliberately so: rostered, past the cap measured
    in the season being SET UP rather than the one just played, and never one of ours nor a
    manifest reserve row. Carrying a name the age-out is not taking would put a man in the draft
    who is still playing college next season, which is worse than an invented name.

    Sorted by the same key `field_from_save` uses, so "best" means one thing on draft night.
    """
    from .codec.league_dat import POSITIONS
    keep = ageout.protected_names("college", store)
    cap = ageout.AGE_CAPS["college"]
    playing = ageout.playing_season(season)
    out = []
    for pl in college.players:
        if pl.values.get("Team", 0) < 1 or pl.name in keep:
            continue
        try:
            if ageout.age_of(pl, playing) < cap:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        first, _, last = _ascii_name(pl.name).partition(" ")
        if not first or not last:
            continue
        out.append({
            "id": f"senior-{pl.id}", "role": "field",
            "first_name": first, "last_name": last,
            "position": POSITIONS.get(pl.values.get("Position"), ""),
            "height_inches": pl.values.get("Height"),
            # Field to field, in the codec's own keys, because these are copied straight back
            # into another save rather than read through the store's rating-keyed shape.
            "ratings": {f: pl.values.get(f, 0) for f in RATINGS},
            "potentials": {f: pl.values.get(f, 0) for f in POTENTIALS},
        })
    out.sort(key=lambda c: (-sum(sheet(c)), describe(c)))
    return out[:limit] if limit else out


def carry_into_pool(pro, seniors):
    """Write `seniors` over the pro draft pool. Returns the names that landed.

    ORDER MATTERS AND IT IS THE ONE THING TO BE CAREFUL ABOUT HERE. Every numeric field is fixed
    width and can be set in place, but `rename` re-encodes three strings and changes the record's
    LENGTH - which splices the file and re-parses it, invalidating every player reference held
    across the call. So all the numbers go in first, against references that are still good, and
    every name goes last in ONE `rename_many`, which applies its edits back to front for exactly
    this reason.
    """
    slots = [p for p in pro.players if p.values.get("Team") == POOL_TEAM]
    pairs = list(zip(slots, seniors))
    for pl, c in pairs:
        for field, value in (c.get("ratings") or {}).items():
            if field in RATINGS:
                pro.set(pl, field, int(value))
        for field, value in (c.get("potentials") or {}).items():
            if field in POTENTIALS:
                pro.set(pl, field, int(value))
        if c.get("height_inches"):
            pro.set(pl, "Height", int(c["height_inches"]))
        code = ch.POSITION_CODES.get(c.get("position"))
        if code:
            pro.set(pl, "Position", code)
    pro.rename_many([(pl, c["first_name"], c["last_name"]) for pl, c in pairs])
    return [f'{c["first_name"]} {c["last_name"]}' for _pl, c in pairs]
