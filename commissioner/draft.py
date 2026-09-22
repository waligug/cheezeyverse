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


def reason_for(team, profile_key, detail, runner_up=None):
    """One sentence, generated from the term that actually decided the pick.

    Everything here is derived from `detail`, which came out of `evaluate` - so tuning a weight
    changes the sentence too, and the reason cannot drift away from the arithmetic that made the
    choice. That is the whole contract of this module.
    """
    profile = PROFILES.get(profile_key) or PROFILES["best_available"]
    terms = detail["terms"]
    won = max(terms, key=terms.get)
    if won == "fit" and detail["fits"]:
        why = f"They were thinnest at {detail['need']} and he plays it."
    elif won == "ceiling":
        why = f"Highest ceiling left on the board ({detail['ceiling']:.0f})."
    else:
        why = f"Most ready to play right now ({detail['now']:.0f})."
    parts = []
    if PROFILE_TERM.get(profile_key) == won:
        parts.append(profile["line"].format(team=team))
    parts.append(why)
    if runner_up:
        parts.append(f"{runner_up} was the other name in the room.")
    return " ".join(parts)


def build_board(declared, order, needs=None, profiles=None):
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
    remaining = list(declared)
    picks = []
    n = max(1, len(order))
    slot = 0
    while remaining:
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
        picks.append({
            "round": slot // n + 1,
            "pick": slot + 1,
            "team": team,
            "character": best,
            "score": round(score, 2),
            "profile": profile_key,
            "need": need,
            "reason": reason_for(team, profile_key, detail, runner_up),
            "runner_up": runner_up,
        })
        remaining.remove(best)
        slot += 1
    return picks


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
