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


def pick_slot(slots, position=None):
    """Prefer a slot whose listed position matches; fall back to any free one.

    Position is a preference, not a promise - FBPB3's coach assigns minutes from the depth chart
    and will happily play someone away from their listed spot.
    """
    if position:
        for s in slots:
            if s.position == position:
                return s
    return slots[0] if slots else None


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

    pl = L.find(slot.name, slot.dob)
    L.rename(pl, character["first_name"], character["last_name"])
    pl = L.find(f'{character["first_name"]} {character["last_name"]}', slot.dob)

    month, day, year = (int(v) for v in character["dob"].split("/"))
    L.set(pl, "BirthMonth", month)
    L.set(pl, "BirthDay", day)
    L.set(pl, "BirthYear", year)
    L.set(pl, "Height", int(character["height_inches"]))
    if character.get("position"):
        L.set(pl, "Position", POSITION_CODES[character["position"]])

    for field, value in (character.get("ratings") or {}).items():
        if field in RATINGS:
            L.set(pl, field, max(0, min(RATING_MAX, int(value))))
    for field, value in (character.get("potentials") or {}).items():
        if field in POTENTIALS:
            L.set(pl, field, max(0, min(RATING_MAX, int(value))))
    return pl


def apply_deltas(L, name, dob, deltas):
    """Add `deltas` ({field: +n}) to what the save currently holds. Returns {field: (was, now)}.

    A rating is never pushed past its own potential - that is the game's rule, not ours, and a
    rating above its ceiling simply decays back.
    """
    pl = L.find(name, dob)
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


_POT_BY_RATING = {
    "InsideScoring": "PotInside", "JumpShot": "PotJumpShot", "FtShot": "PotFtShot",
    "3pShot": "Pot3pShot", "Handling": "PotHandling", "Passing": "PotPassing",
    "OReb": "PotOReb", "DReb": "PotDReb", "PostDefense": "PotPostDefense",
    "PerimeterDefense": "PotPerimeterDefense", "Stealing": "PotStealing", "Blocking": "PotBlocking",
}


def _potential_for(rating):
    return _POT_BY_RATING.get(rating)


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
            pl = check.find(name, dob)
        except CodecError as exc:
            wrong.append(f"{name} ({dob}): {exc}")
            continue
        for field, value in fields.items():
            if pl.values.get(field) != value:
                wrong.append(f"{name} {field}: file says {pl.values.get(field)}, wanted {value}")
    if wrong:
        raise ApplyError("write verification failed: " + "; ".join(wrong[:6]))
    return check
