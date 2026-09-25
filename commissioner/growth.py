"""Seeded rolls and height growth for Cheezeyverse characters. The Python half of a
two-language model.

MIRRORED EXACTLY IN site/js/rules.js. This copy is the one the commissioner runs: each
offseason it asks for a character's height at his new age and writes the inches straight
into league.dat through the codec, which stores Height as an int16. The JS copy exists so
the create page can show the range he is heading for and the player page can show the
growth so far.

tests/test_rules.py runs the same ids and the same seeds through both implementations and
asserts identical results, so if you change one, change the other and re-run it.

WHAT IS SEEDED, AND OFF WHAT
----------------------------
Two different things, seeded two different ways on purpose:

* ``identity_seed(first, last, hometown, jersey)`` seeds the STAT ROLL. The numbers have to
  exist before the database row does, so the seed comes from the identity a person typed
  rather than from the row's id. Two people who answer the quiz identically still get
  different players, and re-submitting the same form is never a reroll.
* ``height_seed(character_id)`` seeds the HEIGHT CURVE, because the commissioner replays
  that curve every offseason, long after creation, and it must never rewrite history.

The derivation of the ratings themselves (the quiz, the traits, the weights) lives only in
site/js/rules.js - Python does not need it and duplicating it would be a second thing to
keep in step. What Python mirrors is the *seeding and the rolling*: identical seeds,
identical streams, identical swings.

THE HEIGHT MODEL
----------------
* An expected adult height comes from the starting height at fourteen plus the
  `height_genes` trait. That number is PURE - no randomness - which is why it can be stored
  on the character at creation as `expected_adult_height`.
* Each offseason he closes about a QUARTER of whatever gap is left, times a 0.75-1.25
  jitter. A quarter of a shrinking gap is the diminishing-returns curve, and it is
  deliberately gradual: he is still visibly growing at nineteen.
* On top of that there is a CREEP that decays every year but never reaches zero. That is
  what makes the absence of a ceiling literal rather than rhetorical - there is no age and
  no height at which this model says "that is it".
* And a freak roll, which can add a real spurt, with its chance HALVED for every full inch
  he is already past his expectation. A seven footer is possible out of the right answers
  and is never, ever guaranteed.

All arithmetic is in integer HUNDREDTHS OF AN INCH. There is not one float in the growth
path, which is what lets the two languages agree bit for bit rather than nearly.
"""
from __future__ import annotations

MASK32 = 0xFFFFFFFF

START_AGE = 14

# --- the tuning table. Identical to HEIGHT_GROWTH in site/js/rules.js -------------------

#: Offseasons the model runs: 14->15, 15->16, ... 22->23.
OFFSEASONS = 9
#: Hundredths of an inch a kid with height_genes = 0 still has coming.
GAIN_BASE = 100
#: Plus this many hundredths per ten points of genes: genes 100 -> +8.00 inches.
GAIN_PER_TEN_GENES = 70
#: Percent of the remaining gap closed each offseason, before the jitter.
CLOSE_PCT = 26
#: The jitter on the close and on the creep: x0.75 to x1.25.
JITTER_LO = 75
JITTER_SPAN = 51
#: The creep, in hundredths: this much in the first offseason...
CREEP_BASE = 42
#: ...this much less each year after...
CREEP_DECAY = 5
#: ...and never, ever less than this. A twentieth of an inch a year, forever.
CREEP_FLOOR = 5
#: How live the freak roll is: 100% at fourteen, falling, never quite nothing.
AGE_PCT_BASE = 100
AGE_PCT_DECAY = 12
AGE_PCT_FLOOR = 5
#: Chance of a spurt, before the age damping and the halving-per-inch-over.
FREAK_CHANCE = 25
#: And its size in hundredths: 0.05 to 0.60 of an inch, before the age damping.
FREAK_LO = 5
FREAK_SPAN = 56
#: How far his real target can sit from the expectation, in hundredths: 1.50 inches short
#: of it to 2.50 inches past it, drawn once off his id. This is where almost all of the
#: spread in adult height comes from, and why the create page shows a band, not a number.
TARGET_DOWN = 150
TARGET_UP = 250

#: The last age the model has anything to say about.
GROWTH_END_AGE = START_AGE + OFFSEASONS  # 23

#: Fixed sample ids so height_outlook() is deterministic and matches the JS copy.
OUTLOOK_SAMPLES = 64

#: The separator between identity fields. A control character, so no name can contain it.
SEED_SEPARATOR = "\x01"


# ---------------------------------------------------------------------------------------
# Seeds and the PRNG
# ---------------------------------------------------------------------------------------


def _imul(a: int, b: int) -> int:
    """JavaScript's Math.imul, as far as the low 32 bits are concerned."""
    return (a * b) & MASK32


def fnv1a32(text) -> int:
    """32-bit FNV-1a over the UTF-16 code units of a string.

    UTF-16 code units, not bytes and not codepoints, because the JS copy walks the string
    with charCodeAt(). In practice these are ASCII uuids and ASCII names, but doing it
    properly costs nothing and removes a whole class of "why do they disagree".
    """
    s = "" if text is None else str(text)
    raw = s.encode("utf-16-le")
    h = 2166136261
    for i in range(0, len(raw), 2):
        unit = raw[i] | (raw[i + 1] << 8)
        h = (h ^ unit) & MASK32
        h = _imul(h, 16777619)
    return h & MASK32


def height_seed(character_id) -> int:
    """The growth PRNG's seed: the character's own id."""
    return fnv1a32(character_id)


def identity_seed(first_name="", last_name="", hometown="", jersey="") -> int:
    """The stat roll's seed: who he is, not what you answered.

    Name, hometown and jersey number, trimmed, lowercased and joined. Mirrors
    identitySeed() in site/js/rules.js, including how it stringifies the jersey number.

    The one cross-language trap is that number: JavaScript's String(8.0) is "8" while
    Python's str(8.0) is "8.0", and a jersey read back out of PostgREST as JSON can arrive
    as a float. An integral float is therefore narrowed to an int first, which makes
    identity_seed(..., 8), identity_seed(..., 8.0) and identity_seed(..., "8") all agree
    with the browser.

    In practice the browser is the only thing that needs to produce a roll - the sheet is
    derived once, at creation, and stored - so this exists to make a server-side re-derive
    possible rather than because anything does one today.
    """
    def part(v):
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return ("" if v is None else str(v)).strip().lower()

    return fnv1a32(SEED_SEPARATOR.join(
        (part(first_name), part(last_name), part(hometown), part(jersey))))


def mulberry32(seed: int):
    """mulberry32. Returns a callable yielding uint32 per call, same stream as the JS."""
    state = seed & MASK32

    def nxt() -> int:
        nonlocal state
        state = (state + 0x6D2B79F5) & MASK32
        t = state
        t = _imul(t ^ (t >> 15), t | 1)
        t = (t ^ (t + _imul(t ^ (t >> 7), t | 61))) & MASK32
        return (t ^ (t >> 14)) & MASK32

    return nxt


def roll_swings(seed: int, bands) -> list:
    """One swing per band, each uniform over [-band, +band], drawn in order off one stream.

    The caller fixes the order (rules.js draws the 18 ratings in their canonical order and
    then the 12 potentials in theirs) and it must never change, or every existing character
    would reroll into somebody else.
    """
    rng = mulberry32(seed)
    out = []
    for band in bands or []:
        b = max(0, int(band or 0))
        out.append((rng() % (2 * b + 1)) - b)
    return out


# ---------------------------------------------------------------------------------------
# Height
# ---------------------------------------------------------------------------------------


def to_inches(hundredths: int) -> int:
    """Hundredths to whole inches, rounding half up. Matches Math.floor((h + 50) / 100)."""
    return (hundredths + 50) // 100


def _creep_at(i: int) -> int:
    return max(CREEP_FLOOR, CREEP_BASE - i * CREEP_DECAY)


def _age_pct_at(i: int) -> int:
    return max(AGE_PCT_FLOOR, AGE_PCT_BASE - i * AGE_PCT_DECAY)


def _js_round(x):
    """Round half UP, the way JavaScript's Math.round does.

    Python's round() is banker's rounding: round(70.5) is 70 but Math.round(70.5) is 71,
    which would put this mirror an inch away from rules.js for any half-inch input.
    """
    import math
    return int(math.floor(float(x) + 0.5))


def expected_adult_hundredths(start_inches, height_genes) -> int:
    """Where he is expected to finish, in hundredths. Pure - no seed, no rolls.

    It is an expectation, not a cap: the creep and the freak roll both push past it.
    """
    start = _js_round(start_inches or 0) * 100
    genes = max(0, min(100, _js_round(height_genes or 0)))
    return start + GAIN_BASE + (genes * GAIN_PER_TEN_GENES) // 10


def expected_adult_height(start_inches, height_genes) -> int:
    """The same number in whole inches - what goes in `expected_adult_height`."""
    return to_inches(expected_adult_hundredths(start_inches, height_genes))


def growth_curve(character_id, start_inches, height_genes):
    """One entry per age from 14 to 23 inclusive.

    Deterministic given (character_id, start_inches, height_genes) and nothing else.

    :returns: list of dicts {"age", "hundredths", "inches"}
    """
    rng = mulberry32(height_seed(character_id))
    expected = expected_adult_hundredths(start_inches, height_genes)
    cur = _js_round(start_inches or 0) * 100

    # One draw, up front: where he is really heading. See TARGET_DOWN / TARGET_UP.
    target = expected - TARGET_DOWN + (rng() % (TARGET_DOWN + TARGET_UP + 1))

    out = [{"age": START_AGE, "hundredths": cur, "inches": to_inches(cur)}]
    for i in range(OFFSEASONS):
        # Four draws every offseason, in this order, whether or not each one is used, so
        # the stream stays aligned with the JS copy.
        j1 = JITTER_LO + (rng() % JITTER_SPAN)
        j2 = JITTER_LO + (rng() % JITTER_SPAN)
        hit = rng() % 100
        roll = FREAK_LO + (rng() % FREAK_SPAN)

        gap = target - cur
        close = (gap * CLOSE_PCT * j1) // 10000 if gap > 0 else 0
        creep = (_creep_at(i) * j2) // 100

        age_pct = _age_pct_at(i)
        over = max(0, (cur + close + creep) - target)
        halvings = min(31, over // 100)
        chance = (FREAK_CHANCE * age_pct // 100) >> halvings
        spurt = (roll * age_pct) // 100 if hit < chance else 0

        cur += close + creep + spurt
        out.append({"age": START_AGE + i + 1, "hundredths": cur, "inches": to_inches(cur)})
    return out


def height_at_age(character_id, start_inches, height_genes, age) -> int:
    """His height in whole inches at any age.

    Before 14 is the starting height; at 23 and beyond he is done growing and this returns
    the final height rather than raising, because the commissioner will happily ask about a
    twenty-six year old.
    """
    curve = growth_curve(character_id, start_inches, height_genes)
    a = int(age)
    if a <= START_AGE:
        return curve[0]["inches"]
    if a >= GROWTH_END_AGE:
        return curve[-1]["inches"]
    return curve[a - START_AGE]["inches"]


def grew_this_offseason(character_id, start_inches, height_genes, new_age) -> int:
    """Inches to add when a character turns `new_age`. Zero once he is grown.

    This is the call the offseason job wants: read the player's current Height out of the
    save, add this, write it back. It is a difference of two curve points rather than a
    fresh roll, so applying it twice by accident cannot compound.
    """
    before = height_at_age(character_id, start_inches, height_genes, int(new_age) - 1)
    after = height_at_age(character_id, start_inches, height_genes, int(new_age))
    return after - before


def _sample_id(i: int) -> str:
    return f"cv-outlook-sample-{i}"


def height_outlook(start_inches, height_genes) -> dict:
    """The range he is heading for.

    Runs a fixed set of sample ids through the model so the create page can show an honest
    band before the character has an id of its own. The sample ids are fixed, so this is
    deterministic and the JS copy produces the same five numbers.
    """
    finals = sorted(growth_curve(_sample_id(i), start_inches, height_genes)[-1]["hundredths"]
                    for i in range(OUTLOOK_SAMPLES))

    def at(q):
        return finals[min(len(finals) - 1, (len(finals) * q) // 100)]

    return {
        "expected": expected_adult_height(start_inches, height_genes),
        "low": to_inches(at(20)),
        "high": to_inches(at(80)),
        "ceiling": to_inches(finals[-1]),
        "floor": to_inches(finals[0]),
    }


# ---- weight ---------------------------------------------------------------------------------
# MIRRORS BUILDS[].lbs and buildWeight() in site/js/rules.js. Duplicated rather than imported
# for the same reason everything else here is: this file is the Python half of a two-language
# model and there is no JS runtime on the commissioner machine. tests/test_rules.py pins the
# two copies together.
BUILD_LBS = {"wiry": -14, "lean": -7, "solid": 0, "strong": 9, "heavy": 20}


def build_weight(height_inches, build_id=None) -> int:
    """The weight a fourteen-year-old of this height and build is assumed to be.

    A FALLBACK, not the truth. A character created since the builder grew a weight slider
    carries `weight_lbs` and that is what he weighs; this answers for everybody made before it,
    and for anyone who never touched the slider, with exactly the number the site has always
    shown them. It is also where the slider starts.
    """
    # _js_round, not round(): Math.round is half-up and Python's round() is half-even, and the
    # JS copy is the one a person watches move on the slider. See the height model above.
    h = _js_round(height_inches) if height_inches else 70
    return _js_round((h - 60) * 4.6 + 96) + BUILD_LBS.get(str(build_id or "").lower(), 0)


# What a body of this height carries at this age, before anything personal about him.
#
# MIRRORS the deterministic core of weight_for() in commissioner/universe/generate.py, which is
# what every filler in all three leagues was built with - duplicated rather than imported for the
# reason characters.py already gives about that module: importing it would drag the whole universe
# builder into every codec call. The gauss noise and its max(120) floor are deliberately NOT here.
# That floor is a population-generation detail; applied to one character it would quietly make
# every small prep fourteen-year-old weigh the same.
#
# WHY THIS CURVE AND NOT buildWeight(). buildWeight has no age in it - it describes a fourteen
# year old, and that is all it was ever asked to do. Checked against 955 real players in the
# saves, it runs 15-25 lbs light at every adult height band, while this one lands within 3-6:
#
#   pro, 6'2"-6'5"   real 186   buildWeight 170   this 183
#   pro, 6'6"-6'9"   real 208   buildWeight 183   this 199
#
# A character who kept growing on buildWeight would be visibly the lightest man on his team for
# his whole career, next to fillers this curve produced.
WEIGHT_PER_INCH = 5.2
WEIGHT_AT_60IN = 100
MATURATION_LBS = 4          # a year of filling out, until he is done at 18
MATURATION_END_AGE = 18
WEIGHT_MIN, WEIGHT_MAX = 50, 400   # the int16 the game stores, kept plausible


def frame_weight(height_inches, age) -> int:
    """The weight the population model gives a body this tall at this age."""
    h = int(height_inches or 70)
    a = int(age if age is not None else START_AGE)
    grown_in = max(0, MATURATION_END_AGE - a) * MATURATION_LBS
    return _js_round(WEIGHT_AT_60IN + (h - 60) * WEIGHT_PER_INCH - grown_in)


def weight_offset(character) -> int:
    """How far from the usual frame HE is - the slider's choice, kept for life.

    Measured at fourteen, where both his stored numbers live: `weight_lbs` is what he chose and
    `height_inches` is how tall he was when he chose it. It absorbs his own decision and the
    small constant gap between buildWeight (which the slider is built around) and the frame
    curve, so neither drifts as he grows.
    """
    chosen = character.get("weight_lbs")
    if chosen in (None, ""):
        chosen = build_weight(character.get("height_inches"), character.get("build"))
    return int(chosen) - frame_weight(character.get("height_inches"), START_AGE)


def weight_at(character, height_now, age) -> int:
    """What he should weigh in the save now: this year's frame, plus the man he chose to be."""
    total = frame_weight(height_now, age) + weight_offset(character)
    return max(WEIGHT_MIN, min(WEIGHT_MAX, total))


# How much of a HISTORICAL error the offseason is allowed to correct in one summer. This year's
# real change - his inches, his year of filling out - is always applied in full; only the gap
# between what the file says and what the model says is rationed.
#
# It exists because the seven characters who predate the weight column are carrying the weight of
# the dormant filler whose slot they took, and two of those sat on the generator's 120 lb floor.
# Correcting that in one write moves a 6'0" fourteen-year-old 50 lbs in a single offseason, which
# FBPB3 then posts up and rebounds with. At 12 a year everybody is on the curve within one to four
# offseasons and no year looks like anything other than a teenager growing.
#
# Set it to None to correct everybody at once.
# ZERO SINCE 2026-09-24, AND IT HAD BECOME A PUMP. weight_offset() measures a man "at fourteen"
# off `weight_lbs` and `height_inches` - but the offseason rewrites both every year, so the
# offset is re-measured each summer and swallows the maturation already applied. The gap below
# then read as "he is under his frame" every single year: +4 a year for a teenager and the full
# 12 for every adult, for life. Tim Turner went 240 -> 293 at 6'9" and the 2034 offseason died
# when the database refused him. The historical correction this was for (characters carrying a
# filler's weight) finished years ago, so from now on only the real change moves a weight: his
# inches, and filling out until MATURATION_END_AGE.
WEIGHT_CATCHUP_PER_YEAR = 0


def weight_step(character, weight_in_save, height_before, height_now, age) -> int:
    """What to write into the save this offseason: this year's change, plus a step toward truth.

    Returns the model's own answer when the file has nothing usable, which is what a character
    stamped this season wants - he arrives already correct.
    """
    target = weight_at(character, height_now, age)
    if not weight_in_save:
        return target
    was = weight_at(character, height_before, max(START_AGE, age - 1))
    natural = target - was
    gap = target - (int(weight_in_save) + natural)
    if WEIGHT_CATCHUP_PER_YEAR is not None:
        gap = max(-WEIGHT_CATCHUP_PER_YEAR, min(WEIGHT_CATCHUP_PER_YEAR, gap))
    return max(WEIGHT_MIN, min(WEIGHT_MAX, int(weight_in_save) + natural + gap))


def weight_ceiling(height_inches) -> int:
    """The heaviest weight the database accepts at this height (characters_weight_lbs_sane).

    Mirrors supabase/weight_range.sql: the creation-time weight for this height plus 95. A save
    write the store will then refuse strands an offseason half-done, so growth never goes past it.
    """
    h = int(height_inches or 70)
    return _js_round((h - 60) * 4.6 + 96) + 95


def weight_for_save(character) -> int:
    """What to write into league.dat's Weight for this character.

    His own number when he has one, the suggestion from his height and build when he does not.
    Clamped to the same 50..400 the `weight_lbs` column allows, because this goes into an int16
    the game will happily display as nonsense.
    """
    chosen = character.get("weight_lbs")
    if chosen in (None, ""):
        chosen = build_weight(character.get("height_inches"), character.get("build"))
    return max(50, min(400, int(chosen)))


def format_height(inches) -> str:
    """76 -> 6'4\"."""
    n = _js_round(inches)
    return f"{n // 12}'{n % 12}\""


if __name__ == "__main__":  # a quick look at the model, for tuning
    import sys

    cid = sys.argv[1] if len(sys.argv) > 1 else "7c9f1a2e-0000-4000-8000-000000000001"
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    genes = int(sys.argv[3]) if len(sys.argv) > 3 else 50
    print(f"{cid}  start {format_height(start)}  genes {genes}")
    print(f"expected adult height: {format_height(expected_adult_height(start, genes))}")
    for row in growth_curve(cid, start, genes):
        print(f"  age {row['age']:>2}  {format_height(row['inches']):>6}"
              f"  ({row['hundredths'] / 100:.2f} in)")
    print("outlook:", {k: format_height(v) for k, v in height_outlook(start, genes).items()})
