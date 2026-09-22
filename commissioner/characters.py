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

from . import growth
from .codec.league_dat import POTENTIALS, RATING_MAX, RATINGS, CodecError, LeagueDat
from .codec import league_dat as lg
from .universe import config as cfg

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
BACKUPS = Path(__file__).resolve().parents[1] / "backups"

POSITION_CODES = {"C": 1, "PF": 2, "SF": 3, "SG": 4, "PG": 5}
# Fouling is a tendency, not a skill, and we have not confirmed which direction the engine
# treats as good, so nobody is allowed to spend on it.
LOCKED = {"Fouling"}
# What the game's own CSV import writes for a one-year deal (universe/generate.py:88). Imported
# rather than re-typed would be circular - generate imports this module - so it is stated here
# and the two are pinned together by tests/test_contract_payout.py.
IMPORT_CONTRACT = 1_000_000

# HOW LONG A DEAL WRITTEN FOR A CHARACTER RUNS, by the level he is landing in.
#
# It is four everywhere, and the three are listed separately because they are four for three
# different reasons: prep holds him from fourteen to PREP_LAST_AGE, college caps at
# COLLEGE_MAX_YEARS, and pro is ROOKIE_YEARS. Tuning one of those must not silently tune the
# other two, which is what a single shared constant would do.
#
# WHY ANY OF THEM IS NOT ONE. A one-year deal expires at the next rollover's FREE AGENCY stage,
# where the game throws every expiring contract open at once and the AI re-signs whom it likes.
# So a character given one year is a free agent in the same offseason he arrived - and only the
# DRAFT ever stated a term, which left the prep->college promotion, the website signup and the
# rehearsal all writing one-year deals onto real people. All seven characters are in college on
# one right now.
#
# Re-typed rather than imported because offseason imports this module; tests/test_contract.py
# pins them together so the copies cannot drift.
LEVEL_CONTRACT_YEARS = {"prep": 4, "college": 4, "pro": 4}


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
    # The dormant filler's own body, read off the row by stamp_character just before it is
    # overwritten, so that handing the slot back can put it there. The manifest never recorded
    # height or weight, and without these the recycled filler keeps the departed character's -
    # a 7'2" fourteen-year-old who is really the 5'10" kid the slot was generated as.
    height: int = None
    weight: int = None

    @classmethod
    def from_manifest(cls, row):
        return cls(row["league"], row["team"], row["name"], row["dob"], row["position"],
                   row["uniform"], row.get("height"), row.get("weight"))

    def as_json(self):
        out = {"league": self.league, "team": self.team, "name": self.name,
               "dob": self.dob, "position": self.position, "uniform": self.uniform}
        # Only when known: a slot claimed before this existed has no body recorded, and writing
        # nulls into claimed_slot would say "it was zero" rather than "nobody looked".
        if self.height:
            out["height"] = int(self.height)
        if self.weight:
            out["weight"] = int(self.weight)
        return out


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

    if busy:
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
    else:
        ordered = list(slots)

    # THE FALLBACK IS SPREAD TOO. `team` still wins outright, but when that team has no free
    # reserve row the second pool used to be the raw list in league order - so every pick that
    # overflowed landed in the same place. One promotion never noticed; a draft promotes sixty
    # in a row, and the whole overflow would stack on one roster. That is the exact bug `busy`
    # was written for, and it was reachable again through the one branch that skipped it.
    pools = ([s for s in slots if where(s) == team], ordered) if team else (ordered,)

    for pool in pools:
        if position:
            for s in pool:
                if s.position == position:
                    return s
        if pool:
            return pool[0]
    return None


def weight_for(character):
    """The pounds he should weigh on the day he is stamped: his choice, or the derived one.

    One function because two callers need the SAME answer - stamp_character writes it into
    league.dat, and the Sim Week commit check reads it back out to prove it landed.

    The body is in growth.weight_for_save, which clamps it into the range the int16 and the
    column both accept; this stays as the name the commissioner calls, because a weight that
    reached the file unclamped would be verified against itself and look correct.
    """
    return growth.weight_for_save(character)


def stamp_character(L, slot, character):
    """Turn a dormant reserve slot into a real character, in place.

    `character` needs: first_name, last_name, ratings {name: value}, potentials {name: value},
    dob "M/D/YYYY", height_inches, position. `weight_lbs` and `build` are optional - between
    them they decide what he weighs, and without either he gets the weight his height suggests.
    Returns the player record.
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
    # What this row was before he took it. `refill` writes it back when he leaves; see Slot.
    slot.height = pl.values["Height"]
    slot.weight = pl.values["Weight"]
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
    # WEIGHT WAS NEVER WRITTEN AT ALL until the builder grew a slider, so the game gave every
    # character whatever the reserve slot he claimed happened to weigh. Nobody noticed because
    # the site derived its own number from height and build and only ever showed that one.
    # Now that somebody can CHOOSE it, the two have to be the same number: a review card that
    # says 185 and a game that plays him at 160 is a lie with no error attached.
    #
    # Null is not "no weight", it is "never asked" - everybody made before the slider - and
    # those keep the number the site has always shown them, derived the same way it derives it.
    #
    # This is only the weight he ARRIVES with. From his next offseason on, growth.weight_step()
    # moves it with his height and his age - see offseason.apply_growth, which writes both
    # together and verifies them together.
    L.set(pl, "Weight", weight_for(character))
    # EXPERIENCE BELONGS TO THE ROW, NOT TO HIM, AND IT HAS TO BE RESET.
    #
    # A character does not get a new player; he is stamped onto a reserve row that already
    # exists, and everything on that row which nothing overwrites stays his. Exp was one of
    # those: after the 2029 promotions the college page for Chris Zimmer, seventeen years old
    # and one day into the league, read "Experience: 3 years". All seven arrived carrying 2 to 4
    # years of somebody else's career.
    #
    # Zero is the honest number and it is what the rest of the code already says: generate.py
    # builds a body with `max(0, age - age_range[0])`, which is 0 for anybody entering at the
    # bottom of a band, and ageout's intake sets 0 outright on a recycled body. A promotion is
    # the same event from the character's side - his first year at this level - whatever the row
    # did before he took it. The engine reads Exp for development and evaluation, so a freshman
    # billed as a fourth-year is a different player to it.
    L.set(pl, "Exp", 0)
    # AND A CONTRACT, FOR THE SAME REASON EXP IS RESET: it belongs to the row, not to him.
    #
    # Eleven of pro's sixty reserve seats carry no contract at all, and a seat is exactly where a
    # drafted character is stamped. Under Full Finances the game RELEASES a player with no
    # contract the moment the league loads - so a character could be drafted, placed, announced in
    # Discord, and be a free agent by the next Sim Week with nothing anywhere saying why.
    #
    # This does not wait for Finances to be switched on: a seat that is bare today is a landmine
    # today.
    #
    # LENGTH MATTERS AS MUCH AS EXISTENCE, and it is not obvious why. A one-year deal expires at
    # the very next rollover, and with Finances on that rollover runs FREE AGENCY - measured on a
    # clone 2026-09-22: every one-year contract in the league expired together and all 511 players
    # became free agents before the AI re-signed them. A character drafted in season S and given
    # one year would therefore be a free agent minutes later, in the same offseason he arrived,
    # and the AI would sign him wherever it liked. "Drafted #1 by LCH" would be a sentence about
    # a team he never played for.
    #
    # WHICH IS WHY A STATED TERM OVERWRITES AN EXISTING DEAL, and the first version of this did
    # not. It only filled a BARE row - and the seats are not bare: 49 of pro's 60 reserve rows
    # already carry a deal, every one of them a single year (college 30 of 41, prep 23 of 48, all
    # one year). So the draft set four years, found the dormant filler's one-year contract in the
    # way, and silently kept it on 49 of the 60 places a pick can land. The fix fired on 11.
    #
    # A term is only stated by a caller that knows the deal is the CHARACTER'S - the draft, with
    # its rookie years. Everyone else passes nothing and keeps the old behaviour of filling a
    # bare row and leaving an existing one alone, because a signup inheriting the seat's deal is
    # harmless and re-pricing him is not this function's business.
    wanted = character.get("contract_years")
    if wanted is not None:
        try:
            years = max(1, min(int(wanted), lg.CONTRACT_YEARS))
        except (TypeError, ValueError):
            years = 1                      # a term we cannot read is still a term he needs
    else:
        years = 0                          # nothing stated: fill a bare row only
    try:
        if years:
            L.set_contract(pl, [IMPORT_CONTRACT] * years)
        elif not any(L.contract_of(pl)):
            L.set_contract(pl, [IMPORT_CONTRACT])
    except AttributeError:
        # An older codec with no contract support. Narrow on purpose: this used to wrap the
        # clamp and the reads too, so a real AttributeError from inside the codec was swallowed
        # as "old codec" and the character was stamped with NO contract at all - the exact
        # landmine this block exists to defuse, reachable through its own error handler.
        pass
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

    # His body goes back too, when we know what it was. Ratings were always scrubbed here and
    # height was not, so a recycled slot walked around at the departed character's height - and
    # now his weight as well, since weight follows growth. Nothing to do for a slot claimed
    # before Slot started recording it: the number is gone and a guess would be worse.
    for field in ("height", "weight"):
        if original.get(field):
            L.set(pl, field.capitalize(), int(original[field]))

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
