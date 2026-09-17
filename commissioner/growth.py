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


def expected_adult_hundredths(start_inches, height_genes) -> int:
    """Where he is expected to finish, in hundredths. Pure - no seed, no rolls.

    It is an expectation, not a cap: the creep and the freak roll both push past it.
    """
    start = int(round(float(start_inches or 0))) * 100
    genes = max(0, min(100, int(round(float(height_genes or 0)))))
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
    cur = int(round(float(start_inches or 0))) * 100

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


def format_height(inches) -> str:
    """76 -> 6'4\"."""
    n = int(round(float(inches)))
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
