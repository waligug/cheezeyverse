"""Put characters into the save, and apply what people spend their points on.

Two operations, both pure codec work against one league's `league.dat`:

**Claiming a slot.** A character does not get inserted into the binary - the file layout never
changes. At universe creation every team was given dormant **reserve slots** (see
`universe/manifest.json`): ordinary-looking players rated 3-12 so the AI gives them no minutes.
Creating a character claims one and stamps the character onto it - new name, new ratings, new
birthday, new height, new position. `reserve_per_team * teams` is the hard ceiling on concurrent
characters per league.

**Applying upgrades.** Every spend is written as a **delta on whatever the game currently says**,
never as an absolute the website computed. FBPB3 does its own progression - teenagers improve,
veterans decline, coaching matters - and applying deltas means the two stack instead of fighting.
Read the live value, add, write, then re-read the file and confirm every write landed; if any did
not, the caller restores the backup rather than publishing a half-applied week.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .codec.league_dat import POTENTIALS, RATING_MAX, RATINGS, CodecError, LeagueDat
from .universe import config as cfg

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
BACKUPS = Path(__file__).resolve().parents[1] / "backups"

POSITION_CODES = {"C": 1, "PF": 2, "SF": 3, "SG": 4, "PG": 5}
# Fouling is a tendency, not a skill, and we have not confirmed which direction the engine
# treats as good, so nobody is allowed to spend on it.
LOCKED = {"Fouling"}


class ApplyError(Exception):
    pass


@dataclass
class Slot:
    """A reserve slot as the manifest recorded it, which is how the codec finds it again."""
    league: str
    team: str
    name: str
    dob: str
    position: str
    uniform: int

    @classmethod
    def from_manifest(cls, row):
        return cls(row["league"], row["team"], row["name"], row["dob"], row["position"], row["uniform"])

    def as_json(self):
        return {"league": self.league, "team": self.team, "name": self.name,
                "dob": self.dob, "position": self.position, "uniform": self.uniform}


def codec_dob(value):
    """Any birthday the store hands back, in the form the save file actually uses.

    LeagueDat.find() compares the date as a STRING, and the codec builds it unpadded as
    `M/D/YYYY` straight out of BirthMonth/BirthDay/BirthYear. `game_dob` is a Postgres `date`
    column, so however it went in, Supabase returns it as `YYYY-MM-DD` - which matches no
    player in any save, and a character who cannot be found in his own save cannot be
    upgraded, promoted or retired ever again. The local JSON store keeps whatever string it
    was given, so the two backends disagree unless everything normalises on the way in.

    Accepts `M/D/YYYY`, `YYYY-MM-DD`, and anything with .year/.month/.day (date, datetime).
    """
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return f"{value.month}/{value.day}/{value.year}"
    text = str(value).strip()
    if not text:
        return None
    if "-" in text:                                   # ISO, from a date column
        parts = text.split("T")[0].split("-")
        if len(parts) == 3:
            y, m, d = (int(x) for x in parts)
            return f"{m}/{d}/{y}"
    if "/" in text:                                   # already ours, but maybe zero padded
        parts = text.split("/")
        if len(parts) == 3:
            m, d, y = (int(x) for x in parts)
            return f"{m}/{d}/{y}"
    raise ApplyError(f"cannot read {value!r} as a birthday")


def save_path(league_key):
    return DOCS / "leaguedata" / cfg.BY_KEY[league_key].save_name / "league.dat"


def open_league(league_key):
    return LeagueDat(save_path(league_key))


def free_slots(manifest, league_key, claimed):
    """Reserve slots in this league that no character holds yet.

    `claimed` is the set of (name, dob) pairs already taken - the commissioner reads it from the
    characters table, because the save itself cannot tell a claimed slot from an unclaimed one
    once the name has been rewritten.
    """
    taken = {(c["name"], c["dob"]) for c in claimed}
    return [Slot.from_manifest(r) for r in manifest["players"]
            if r["league"] == league_key and r["role"] == "reserve"
            and (r["name"], r["dob"]) not in taken]


def pick_slot(slots, position=None, team=None, busy=None, divisions=None, team_of=None):
    """Pick a free reserve slot, spreading people across the league rather than stacking them.

    Taking the first free slot in team order put the first five friends on two teams. So when
    `busy` is given - {team abbrev: how many characters that team already has} - slots are
    ordered by that count first, then by how many characters are already in the same division,
    then by name for determinism. The effect is that each new person lands somewhere nobody is,
    which is what a league of friends should look like.

    `team` still wins outright, for the draft: a player taken first overall by STL should join
    STL. Without it he went wherever a vacancy happened to be and his page claimed a team he had
    never played for.

    `team_of` resolves which team a slot is REALLY on. The manifest records where a reserve row
    started, and rows genuinely move between teams - a CPU trade does it, and so does moving a
    character deliberately - so the manifest's team is a starting position, not a fact. Reading
    it as one would place somebody on the team a slot used to belong to.

    Position remains a preference and nothing more: FBPB3's coach assigns minutes off the depth
    chart and plays people away from their listed spot all the time.
    """
    where = team_of or (lambda slot: slot.team)

    if team:
        pools = ([s for s in slots if where(s) == team], slots)
    elif busy:
        in_division = {}
        for abbrev, n in busy.items():
            d = (divisions or {}).get(abbrev)
            if d is not None:
                in_division[d] = in_division.get(d, 0) + n
        ordered = sorted(
            slots,
            key=lambda s: (busy.get(where(s), 0),
                           in_division.get((divisions or {}).get(where(s)), 0),
                           where(s) or ""))
        pools = (ordered,)
    else:
        pools = (slots,)

    for pool in pools:
        if position:
            for s in pool:
                if s.position == position:
                    return s
        if pool:
            return pool[0]
    return None


def stamp_character(L, slot, character):
    """Turn a dormant reserve slot into a real character, in place.

    `character` needs: first_name, last_name, ratings {name: value}, potentials {name: value},
    dob "M/D/YYYY", height_inches, position. Returns the player record.
    """
    # A character with no ratings would be stamped over the reserve row and silently keep ITS
    # floor ratings - 3 to 12 across the board, an unplayable player nobody would notice until
    # he had been on a roster for a season. Refuse instead.
    given = {f: v for f, v in (character.get("ratings") or {}).items() if f in RATINGS}
    if not given:
        raise ApplyError(
            f'{character.get("first_name")} {character.get("last_name")} has no ratings; '
            "stamping him would leave the reserve row's floor ratings in place")
    if not character.get("position"):
        raise ApplyError(f'{character.get("first_name")} {character.get("last_name")} has no position')

    pl = L.find(slot.name, codec_dob(slot.dob))
    L.rename(pl, character["first_name"], character["last_name"])
    pl = L.find(f'{character["first_name"]} {character["last_name"]}', codec_dob(slot.dob))

    dob = codec_dob(character.get("dob"))
    if not dob:
        raise ApplyError(
            f'{character.get("first_name")} {character.get("last_name")} has no dob. The website '
            "never sends one - a character takes the birthday of the reserve slot he claims, "
            "which is also the birthday offseason.refill puts back when he leaves.")
    month, day, year = (int(v) for v in dob.split("/"))
    L.set(pl, "BirthMonth", month)
    L.set(pl, "BirthDay", day)
    L.set(pl, "BirthYear", year)
    L.set(pl, "Height", int(character["height_inches"]))
    if character.get("position"):
        L.set(pl, "Position", POSITION_CODES[character["position"]])

    for field, value in (character.get("ratings") or {}).items():
        if field in RATINGS:
            L.set(pl, field, max(0, min(RATING_MAX, int(value))))

    wanted_pots = codec_potentials(character.get("potentials"))
    if (character.get("potentials") or {}) and not wanted_pots:
        raise ApplyError(
            f'{character.get("first_name")} {character.get("last_name")} has potentials keyed '
            f'{sorted(character["potentials"])[:3]}..., none of which name a rating this game '
            "has a potential for. Stamping him would leave the reserve slot's floor potentials "
            "in place and the game would pull his ratings down to them.")
    for field, value in wanted_pots.items():
        L.set(pl, field, max(0, min(RATING_MAX, int(value))))

    # The slot he just took over was built to be unused. Put him in the lineup and give him a
    # share of the depth chart, or he is a name on a roster who never plays a minute.
    L.dress(pl)
    return pl


# A dormant reserve is deliberately terrible so the AI coach gives it no minutes. These are the
# bands commissioner/universe/generate.py used to create them (RESERVE_RATINGS,
# RESERVE_POTENTIALS); they are repeated rather than imported because generate.py builds CSVs
# for the New Game wizard and importing it here would drag the whole universe builder into
# every codec call.
RESERVE_RATINGS = (3, 12)
RESERVE_POTENTIALS = (10, 25)

# Tendencies and stamina are habits, not ability. Floor-rating them makes a filler behave
# strangely rather than merely badly, so generate.py left them alone and so does this.
NOT_ABILITY = ("3pUsage", "Fouling", "Stamina")


def reset_reserve(L, pl, original):
    """Put a vacated reserve slot back to dormant: its name, its birthday, and its floor ratings.

    Handing the slot its identity back is only half the job. A character who leaves - promoted,
    retired, or deleted - leaves his RATINGS behind on the row, so without this the recycled
    filler is a fully developed copy of him. He then takes rotation minutes from real characters,
    and every departure adds another one. Over a few seasons every vacated slot in all three
    leagues is a ghost of whoever used to hold it.

    Two places hand a slot back - offseason.refill and tools/protect_rosters - and only one of
    them scrubbed. Same scrub, called from both, seeded off the slot's own identity so the result
    is deterministic and a slot recycled twice looks the same both times.
    """
    import random

    first, _, last = original["name"].partition(" ")
    L.rename(pl, first, last)
    pl = L.find(original["name"])

    month, day, year = (int(v) for v in codec_dob(original["dob"]).split("/"))
    L.set(pl, "BirthMonth", month)
    L.set(pl, "BirthDay", day)
    L.set(pl, "BirthYear", year)

    rng = random.Random(f'{original["name"]}|{original["dob"]}')
    for field in RATINGS:
        if field in NOT_ABILITY:
            continue
        L.set(pl, field, rng.randint(*RESERVE_RATINGS))
    for field in POTENTIALS:
        L.set(pl, field, rng.randint(*RESERVE_POTENTIALS))
    return pl


def apply_deltas(L, name, dob, deltas):
    """Add `deltas` ({field: +n}) to what the save currently holds. Returns {field: (was, now)}.

    A rating is never pushed past its own potential - that is the game's rule, not ours, and a
    rating above its ceiling simply decays back.
    """
    pl = L.find(name, codec_dob(dob))
    moved = {}
    for field, delta in deltas.items():
        if field in LOCKED:
            raise ApplyError(f"{field} cannot be spent on")
        if field not in RATINGS and field not in POTENTIALS:
            raise ApplyError(f"unknown field {field}")
        was = pl.values[field]
        now = was + int(delta)
        if field in RATINGS:
            pot = _potential_for(field)
            if pot and pot in pl.values:
                now = min(now, pl.values[pot])
        now = max(0, min(RATING_MAX, now))
        if now != was:
            L.set(pl, field, now)
        moved[field] = (was, now)
    return moved


POT_BY_RATING = {
    "InsideScoring": "PotInside", "JumpShot": "PotJumpShot", "FtShot": "PotFtShot",
    "3pShot": "Pot3pShot", "Handling": "PotHandling", "Passing": "PotPassing",
    "OReb": "PotOReb", "DReb": "PotDReb", "PostDefense": "PotPostDefense",
    "PerimeterDefense": "PotPerimeterDefense", "Stealing": "PotStealing", "Blocking": "PotBlocking",
}


def _potential_for(rating):
    return POT_BY_RATING.get(rating)


RATING_BY_POT = {v: k for k, v in POT_BY_RATING.items()}


def codec_potentials(given):
    """Potentials keyed however the caller had them, keyed the way the SAVE FILE needs them.

    Everything outside the codec keys a potential by the rating it belongs to - the website
    does (`startingSheet` fills `potentials[rating]`), and so does the database, whose
    cv_potentials() returns rating names. The codec alone calls them PotInside, PotJumpShot
    and so on, because that is what the bytes are.

    Nothing translated between the two. `stamp_character` filtered the incoming potentials
    with `if field in POTENTIALS`, which is false for every rating name, so it wrote NONE of
    them and left the reserve slot's floor potentials in place. FBPB3 then did exactly what it
    is supposed to do and pulled each rating down to its potential - so every character came
    out of his first sim with his ratings crushed to a dormant filler's ceiling, and nothing
    anywhere said so. Accept both keyings; return the codec's.
    """
    out = {}
    for field, value in (given or {}).items():
        if field in RATING_BY_POT:          # already PotInside / PotJumpShot / ...
            out[field] = value
        elif field in POT_BY_RATING:        # InsideScoring / JumpShot / ...
            out[POT_BY_RATING[field]] = value
    return out


def store_potentials(values):
    """The other direction: a codec player's values, keyed by rating name for the store."""
    return {rating: values[pot] for rating, pot in POT_BY_RATING.items() if pot in values}


def commit(L, expect):
    """Save, re-read, and confirm every intended value is really in the file.

    `expect` is [(name, dob, {field: value})]. Raises before the caller publishes anything, so a
    failed write is a restored backup rather than a wrong week on the website.
    """
    L.save(backup_dir=BACKUPS)
    check = LeagueDat(L.path)
    wrong = []
    for name, dob, fields in expect:
        try:
            pl = check.find(name, codec_dob(dob))
        except CodecError as exc:
            wrong.append(f"{name} ({dob}): {exc}")
            continue
        for field, value in fields.items():
            if pl.values.get(field) != value:
                wrong.append(f"{name} {field}: file says {pl.values.get(field)}, wanted {value}")
    if wrong:
        raise ApplyError("write verification failed: " + "; ".join(wrong[:6]))
    return check
