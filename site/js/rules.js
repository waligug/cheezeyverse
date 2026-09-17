/* =====================================================================================
 * Cheezeyverse rules - the cost curve, the archetypes, and every validation the site does.
 *
 * Pure data and pure functions. No DOM, no `window`, no network: create.html, me.html and
 * tests/test_rules.py all import this same file, and the test runs it under Node.
 *
 * The cost curve is duplicated in supabase/schema.sql (cv_step_cost / cv_upgrade_cost).
 * That copy is the authoritative one - the database recomputes every request's cost and
 * ignores what the browser sent. This copy exists so the page can show a live price.
 * If you change one, change both, and re-run `python tests/test_rules.py`.
 * ===================================================================================== */

/* ---------------------------------------------------------------------------------------
 * Vocabulary
 * ------------------------------------------------------------------------------------ */

/** All 18 ratings, in the order FBPB3 stores them (CONVENTIONS.md, ratings block R+0). */
export const RATINGS = [
  'InsideScoring', 'JumpShot', 'FtShot', '3pUsage', '3pShot', 'Handling', 'Passing', 'Quickness',
  'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking', 'OReb', 'DReb', 'Jumping', 'Strength',
  'Stamina', 'Fouling',
];

/** The 12 that have a potential, in the order FBPB3 stores them (R+168). */
export const POTENTIAL_RATINGS = [
  'InsideScoring', 'JumpShot', 'FtShot', '3pShot', 'Handling', 'Passing',
  'OReb', 'DReb', 'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking',
];

/** Human labels for the column headers. */
export const RATING_LABELS = {
  InsideScoring: 'Inside Scoring',
  JumpShot: 'Jump Shot',
  FtShot: 'Free Throw',
  '3pUsage': '3PT Usage',
  '3pShot': '3PT Shot',
  Handling: 'Handling',
  Passing: 'Passing',
  Quickness: 'Quickness',
  PostDefense: 'Post Defense',
  PerimeterDefense: 'Perimeter Defense',
  Stealing: 'Stealing',
  Blocking: 'Blocking',
  OReb: 'Off. Rebounding',
  DReb: 'Def. Rebounding',
  Jumping: 'Jumping',
  Strength: 'Strength',
  Stamina: 'Stamina',
  Fouling: 'Fouling',
};

/** How the sheets are grouped on screen. Every rating appears exactly once. */
export const RATING_GROUPS = [
  { title: 'Scoring', ratings: ['InsideScoring', 'JumpShot', 'FtShot', '3pUsage', '3pShot'] },
  { title: 'Ball skills', ratings: ['Handling', 'Passing'] },
  { title: 'Defense', ratings: ['PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking'] },
  { title: 'Rebounding', ratings: ['OReb', 'DReb'] },
  { title: 'Athletic', ratings: ['Quickness', 'Jumping', 'Strength', 'Stamina', 'Fouling'] },
];

/**
 * Fouling is a *tendency*, not a skill: in FBPB3 it describes how often the player
 * commits one. We have not confirmed which direction the engine treats as good, so
 * buying it would be a trap - it is shown on the sheet but cannot be spent on. Remove
 * it from this list if you ever pin the behaviour down.
 *
 * 3pUsage is also a tendency but an obviously intentional one (how often he takes
 * threes), so it stays spendable, with archetype caps keeping bigs honest.
 */
export const LOCKED_RATINGS = ['Fouling'];

export const POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'];

export const POSITION_LABELS = {
  PG: 'Point Guard', SG: 'Shooting Guard', SF: 'Small Forward',
  PF: 'Power Forward', C: 'Center',
};

/** Ratings are 0-100 here and in the save file. */
export const RATING_MIN = 0;
export const RATING_MAX = 100;

/** Characters enter at 14 in the Prep league. */
export const START_AGE = 14;

export const MAX_NAME_LENGTH = 20;

/* ---------------------------------------------------------------------------------------
 * The cost curve
 * ------------------------------------------------------------------------------------ */

/**
 * Cost of the single step that *leaves* value v.
 * Below 50 a point is cheap; the last stretch to 100 is brutal on purpose, so nobody
 * maxes anything in a season and the difference between characters stays visible.
 */
export const COST_BANDS = [
  { upTo: 49, cost: 1, label: 'under 50' },
  { upTo: 69, cost: 2, label: '50 - 69' },
  { upTo: 84, cost: 3, label: '70 - 84' },
  { upTo: Infinity, cost: 5, label: '85 and up' },
];

export function stepCost(value) {
  const v = Number(value);
  if (v < 50) return 1;
  if (v < 70) return 2;
  if (v < 85) return 3;
  return 5;
}

/** A potential step costs double the rating step at the same value. */
export const POTENTIAL_MULTIPLIER = 2;

/**
 * Cost of raising a value from `from` by `steps`, charged one step at a time at the band
 * of the value being left. 48 -> 52 is 1 + 1 + 2 + 2 = 6.
 *
 * @param {number} from   the value right now
 * @param {number} steps  how many points of increase
 * @param {'rating'|'potential'} kind
 */
export function upgradeCost(from, steps, kind = 'rating') {
  const n = Math.floor(Number(steps));
  if (!Number.isFinite(n) || n <= 0) return 0;
  let total = 0;
  for (let i = 0; i < n; i += 1) total += stepCost(Number(from) + i);
  return kind === 'potential' ? total * POTENTIAL_MULTIPLIER : total;
}

/** Cost of the next single point, for the +1 button's price tag. */
export function nextPointCost(current, kind = 'rating') {
  return upgradeCost(current, 1, kind);
}

/**
 * How many points of `rating` you can afford out of `budget` before hitting `ceiling`.
 * Used to grey out the +1 button and to size the "max" shortcut.
 */
export function affordableSteps(from, budget, ceiling = RATING_MAX, kind = 'rating') {
  let steps = 0;
  let spent = 0;
  let v = Number(from);
  while (v < ceiling) {
    const next = stepCost(v) * (kind === 'potential' ? POTENTIAL_MULTIPLIER : 1);
    if (spent + next > budget) break;
    spent += next;
    v += 1;
    steps += 1;
  }
  return { steps, cost: spent };
}

/* ---------------------------------------------------------------------------------------
 * Archetypes
 * ------------------------------------------------------------------------------------ *
 * `caps` is the ceiling this archetype can ever reach in a rating. For the 12 ratings that
 * have a potential, the cap is the ceiling on the *potential* (and the rating is then
 * capped by its own potential, as the game requires). For the 6 that have no potential
 * (3pUsage, Quickness, Jumping, Strength, Stamina, Fouling) the cap applies to the rating
 * directly.
 *
 * Anything not listed falls back to CAP_DEFAULT. These numbers are meant to be tuned:
 * they are the whole balance of the universe and nothing else depends on their values.
 *
 * Scale check against commissioner/universe/config.py, so a created player looks right
 * standing next to the AI population:
 *   prep fillers      ratings  8-38,  potentials 25-58
 *   prep reserve rows ratings  3-12,  potentials 10-25
 *   a new character   ratings  5-30,  potentials 20-55, caps 35-95
 * So a 14 year old starts around the bottom of the filler band - genuinely terrible, as
 * intended - but his ceiling is far above anything the AI generates, which is the whole
 * point of being a real person in the league.
 * ------------------------------------------------------------------------------------ */

export const CAP_DEFAULT = 60;

/** Potential starts this far above the rating, or SIGNATURE_HEADROOM for the archetype's own skills. */
export const POTENTIAL_HEADROOM = 12;
export const SIGNATURE_HEADROOM = 25;

/** A signature rating also starts a few points higher than the position template. */
export const SIGNATURE_BONUS = 3;

export const ARCHETYPES = {
  slasher: {
    label: 'Slasher',
    blurb: 'Lives at the rim. Quick, bouncy, gets fouled; not a shooter yet.',
    positions: ['PG', 'SG', 'SF'],
    signature: ['InsideScoring', 'Handling', 'Quickness', 'Jumping'],
    caps: {
      InsideScoring: 92, JumpShot: 78, FtShot: 82, '3pUsage': 60, '3pShot': 68,
      Handling: 85, Passing: 70, Quickness: 92, PostDefense: 55, PerimeterDefense: 78,
      Stealing: 75, Blocking: 45, OReb: 55, DReb: 60, Jumping: 92, Strength: 70,
      Stamina: 85, Fouling: 60,
    },
  },
  sharpshooter: {
    label: 'Sharpshooter',
    blurb: 'Pure shooter. Everything outside 18 feet; nothing much inside it.',
    positions: ['PG', 'SG', 'SF'],
    signature: ['JumpShot', '3pShot', 'FtShot', '3pUsage'],
    caps: {
      InsideScoring: 62, JumpShot: 95, FtShot: 95, '3pUsage': 95, '3pShot': 95,
      Handling: 75, Passing: 70, Quickness: 75, PostDefense: 50, PerimeterDefense: 70,
      Stealing: 68, Blocking: 40, OReb: 45, DReb: 55, Jumping: 65, Strength: 55,
      Stamina: 78, Fouling: 55,
    },
  },
  playmaker: {
    label: 'Playmaker',
    blurb: 'Runs the team. Handle, vision and hands; small and not a rebounder.',
    positions: ['PG', 'SG'],
    signature: ['Handling', 'Passing', 'Stealing', 'Quickness'],
    caps: {
      InsideScoring: 70, JumpShot: 80, FtShot: 88, '3pUsage': 75, '3pShot': 78,
      Handling: 95, Passing: 95, Quickness: 90, PostDefense: 45, PerimeterDefense: 75,
      Stealing: 88, Blocking: 35, OReb: 40, DReb: 55, Jumping: 72, Strength: 55,
      Stamina: 88, Fouling: 60,
    },
  },
  rim_protector: {
    label: 'Rim Protector',
    blurb: 'Anchors the defense. Blocks, boards and post defense; offense is a work in progress.',
    positions: ['PF', 'C'],
    signature: ['Blocking', 'PostDefense', 'DReb', 'Jumping'],
    caps: {
      InsideScoring: 78, JumpShot: 55, FtShot: 60, '3pUsage': 30, '3pShot': 45,
      Handling: 45, Passing: 55, Quickness: 62, PostDefense: 95, PerimeterDefense: 60,
      Stealing: 55, Blocking: 95, OReb: 85, DReb: 92, Jumping: 90, Strength: 88,
      Stamina: 78, Fouling: 70,
    },
  },
  glue_guy: {
    label: 'Glue Guy',
    blurb: 'No holes and no headline skill. Plays any position, never comes off the floor.',
    positions: ['PG', 'SG', 'SF', 'PF', 'C'],
    signature: ['PerimeterDefense', 'Stamina', 'Passing', 'DReb'],
    caps: {
      InsideScoring: 72, JumpShot: 72, FtShot: 78, '3pUsage': 65, '3pShot': 72,
      Handling: 72, Passing: 75, Quickness: 75, PostDefense: 75, PerimeterDefense: 82,
      Stealing: 78, Blocking: 68, OReb: 70, DReb: 75, Jumping: 72, Strength: 72,
      Stamina: 88, Fouling: 62,
    },
  },
  big_man: {
    label: 'Big Man',
    blurb: 'Back to the basket. Scores and rebounds inside; do not ask him to switch.',
    positions: ['PF', 'C'],
    signature: ['InsideScoring', 'Strength', 'OReb', 'PostDefense'],
    caps: {
      InsideScoring: 95, JumpShot: 68, FtShot: 70, '3pUsage': 25, '3pShot': 40,
      Handling: 50, Passing: 60, Quickness: 55, PostDefense: 88, PerimeterDefense: 50,
      Stealing: 50, Blocking: 82, OReb: 92, DReb: 90, Jumping: 75, Strength: 95,
      Stamina: 75, Fouling: 72,
    },
  },
};

export const ARCHETYPE_KEYS = Object.keys(ARCHETYPES);

/**
 * A mediocre 14 year old, by position. Nobody starts good: the highest number here is 21.
 * Tendencies (3pUsage, Fouling) start where the position normally sits rather than low,
 * because they are habits, not ability.
 */
export const POSITION_TEMPLATES = {
  PG: {
    InsideScoring: 12, JumpShot: 15, FtShot: 19, '3pUsage': 25, '3pShot': 14,
    Handling: 21, Passing: 21, Quickness: 22, PostDefense: 7, PerimeterDefense: 14,
    Stealing: 15, Blocking: 5, OReb: 7, DReb: 10, Jumping: 16, Strength: 9,
    Stamina: 20, Fouling: 25,
  },
  SG: {
    InsideScoring: 14, JumpShot: 17, FtShot: 18, '3pUsage': 28, '3pShot': 16,
    Handling: 17, Passing: 15, Quickness: 20, PostDefense: 9, PerimeterDefense: 16,
    Stealing: 14, Blocking: 7, OReb: 9, DReb: 12, Jumping: 18, Strength: 11,
    Stamina: 19, Fouling: 26,
  },
  SF: {
    InsideScoring: 16, JumpShot: 15, FtShot: 16, '3pUsage': 22, '3pShot': 13,
    Handling: 14, Passing: 13, Quickness: 17, PostDefense: 12, PerimeterDefense: 15,
    Stealing: 12, Blocking: 11, OReb: 13, DReb: 15, Jumping: 18, Strength: 14,
    Stamina: 18, Fouling: 28,
  },
  PF: {
    InsideScoring: 18, JumpShot: 12, FtShot: 14, '3pUsage': 14, '3pShot': 9,
    Handling: 10, Passing: 11, Quickness: 13, PostDefense: 16, PerimeterDefense: 12,
    Stealing: 10, Blocking: 16, OReb: 18, DReb: 20, Jumping: 17, Strength: 18,
    Stamina: 17, Fouling: 32,
  },
  C: {
    InsideScoring: 20, JumpShot: 9, FtShot: 12, '3pUsage': 10, '3pShot': 6,
    Handling: 8, Passing: 9, Quickness: 11, PostDefense: 18, PerimeterDefense: 9,
    Stealing: 8, Blocking: 20, OReb: 19, DReb: 21, Jumping: 16, Strength: 20,
    Stamina: 16, Fouling: 34,
  },
};

/**
 * Height in inches. The prep league is an elite one, so these are adult ranges - see the
 * `youth_shrink` note in CONVENTIONS.md.
 */
export const HEIGHT_RANGES = {
  PG: [68, 76], SG: [71, 79], SF: [74, 82], PF: [76, 85], C: [78, 88],
};

export const HEIGHT_DEFAULTS = { PG: 72, SG: 75, SF: 78, PF: 81, C: 84 };

/* ---------------------------------------------------------------------------------------
 * Derived helpers
 * ------------------------------------------------------------------------------------ */

export function archetype(key) {
  const a = ARCHETYPES[key];
  if (!a) throw new Error(`unknown archetype ${key}`);
  return a;
}

export function archetypesForPosition(position) {
  return ARCHETYPE_KEYS.filter((k) => ARCHETYPES[k].positions.includes(position));
}

export function hasPotential(rating) {
  return POTENTIAL_RATINGS.includes(rating);
}

export function isLocked(rating) {
  return LOCKED_RATINGS.includes(rating);
}

/** The archetype's ceiling for one rating. */
export function capFor(archetypeKey, rating) {
  const caps = archetype(archetypeKey).caps || {};
  return Math.min(RATING_MAX, caps[rating] === undefined ? CAP_DEFAULT : caps[rating]);
}

export function capsFor(archetypeKey) {
  const out = {};
  for (const r of RATINGS) out[r] = capFor(archetypeKey, r);
  return out;
}

/**
 * The starting sheet: position template, a small bonus on the archetype's signature
 * skills, everything clamped to the archetype's caps. Potentials start a fixed distance
 * above the rating (further, on signature skills), also clamped to the cap.
 *
 * Clamping is what guarantees the invariant every other function relies on:
 * rating <= potential <= cap, for every rating, for every (position, archetype) pair.
 */
export function startingSheet(position, archetypeKey) {
  const template = POSITION_TEMPLATES[position];
  if (!template) throw new Error(`unknown position ${position}`);
  const arch = archetype(archetypeKey);
  const ratings = {};
  const potentials = {};
  for (const r of RATINGS) {
    const cap = capFor(archetypeKey, r);
    const signature = arch.signature.includes(r);
    const base = template[r] + (signature ? SIGNATURE_BONUS : 0);
    ratings[r] = Math.max(RATING_MIN, Math.min(cap, base));
  }
  for (const r of POTENTIAL_RATINGS) {
    const cap = capFor(archetypeKey, r);
    const headroom = arch.signature.includes(r) ? SIGNATURE_HEADROOM : POTENTIAL_HEADROOM;
    potentials[r] = Math.min(cap, ratings[r] + headroom);
  }
  return { ratings, potentials };
}

/**
 * The ceiling a rating can currently be raised to.
 * For the 12 with a potential that is the potential; for the other 6 it is the cap.
 */
export function ratingCeiling(rating, archetypeKey, potentials) {
  const cap = capFor(archetypeKey, rating);
  if (!hasPotential(rating)) return cap;
  const pot = Number((potentials || {})[rating]);
  return Math.min(cap, Number.isFinite(pot) ? pot : cap);
}

/** The ceiling a potential can be raised to: the archetype cap, nothing else. */
export function potentialCeiling(rating, archetypeKey) {
  return capFor(archetypeKey, rating);
}

/** Total cost of a whole sheet relative to its starting point. Used by create.html. */
export function sheetCost(position, archetypeKey, ratings, potentials) {
  const start = startingSheet(position, archetypeKey);
  let total = 0;
  for (const r of RATINGS) {
    const from = start.ratings[r];
    const to = Number((ratings || {})[r]);
    if (Number.isFinite(to) && to > from) total += upgradeCost(from, to - from, 'rating');
  }
  for (const r of POTENTIAL_RATINGS) {
    const from = start.potentials[r];
    const to = Number((potentials || {})[r]);
    if (Number.isFinite(to) && to > from) total += upgradeCost(from, to - from, 'potential');
  }
  return total;
}

/**
 * Everything create.html has to be sure of before it inserts a row.
 * Returns { ok, errors: [string], spent, remaining }.
 */
export function validateBuild(build, opts = {}) {
  const startingPoints = Number(opts.startingPoints === undefined ? 20 : opts.startingPoints);
  const errors = [];
  const { position, archetype: archetypeKey, ratings = {}, potentials = {} } = build || {};

  if (!POSITIONS.includes(position)) errors.push('Pick a position.');
  if (!ARCHETYPE_KEYS.includes(archetypeKey)) errors.push('Pick an archetype.');
  if (errors.length) return { ok: false, errors, spent: 0, remaining: startingPoints };

  if (!ARCHETYPES[archetypeKey].positions.includes(position)) {
    errors.push(`A ${ARCHETYPES[archetypeKey].label} cannot be a ${POSITION_LABELS[position]}.`);
  }

  const first = String(build.firstName || '').trim();
  const last = String(build.lastName || '').trim();
  if (!first) errors.push('First name is required.');
  if (!last) errors.push('Last name is required.');
  if (first.length > MAX_NAME_LENGTH) errors.push(`First name is longer than ${MAX_NAME_LENGTH} characters.`);
  if (last.length > MAX_NAME_LENGTH) errors.push(`Last name is longer than ${MAX_NAME_LENGTH} characters.`);

  const [hLo, hHi] = HEIGHT_RANGES[position] || [60, 95];
  const height = Number(build.heightInches);
  if (!Number.isFinite(height) || height < hLo || height > hHi) {
    errors.push(`A ${POSITION_LABELS[position]} is ${formatHeight(hLo)} to ${formatHeight(hHi)}.`);
  }

  const start = startingSheet(position, archetypeKey);
  for (const r of RATINGS) {
    const v = Number(ratings[r]);
    if (!Number.isFinite(v)) { errors.push(`${RATING_LABELS[r]} is missing.`); continue; }
    if (v < start.ratings[r]) errors.push(`${RATING_LABELS[r]} cannot start below ${start.ratings[r]}.`);
    if (isLocked(r) && v !== start.ratings[r]) errors.push(`${RATING_LABELS[r]} cannot be bought.`);
    const ceiling = ratingCeiling(r, archetypeKey, potentials);
    if (v > ceiling) errors.push(`${RATING_LABELS[r]} is ${v}, past its ceiling of ${ceiling}.`);
  }
  for (const r of POTENTIAL_RATINGS) {
    const v = Number(potentials[r]);
    if (!Number.isFinite(v)) { errors.push(`${RATING_LABELS[r]} potential is missing.`); continue; }
    if (v < start.potentials[r]) errors.push(`${RATING_LABELS[r]} potential cannot start below ${start.potentials[r]}.`);
    if (v > potentialCeiling(r, archetypeKey)) {
      errors.push(`${RATING_LABELS[r]} potential is ${v}, past the ${ARCHETYPES[archetypeKey].label} cap of ${potentialCeiling(r, archetypeKey)}.`);
    }
  }

  const spent = sheetCost(position, archetypeKey, ratings, potentials);
  if (spent > startingPoints) errors.push(`That is ${spent} points and you only have ${startingPoints}.`);

  return { ok: errors.length === 0, errors, spent, remaining: startingPoints - spent };
}

/**
 * Whether one more upgrade request can be filed.
 * `reserved` is the cost of requests already queued but not yet applied - those have
 * already claimed their points even though the balance has not moved yet.
 */
export function validateUpgrade(character, rating, delta, kind, reserved = 0) {
  const errors = [];
  if (!RATINGS.includes(rating)) errors.push(`${rating} is not a rating.`);
  if (kind === 'potential' && !hasPotential(rating)) {
    errors.push(`${RATING_LABELS[rating] || rating} has no potential in this game.`);
  }
  if (isLocked(rating)) errors.push(`${RATING_LABELS[rating] || rating} cannot be bought.`);
  const steps = Math.floor(Number(delta));
  if (!Number.isFinite(steps) || steps <= 0) errors.push('Pick at least one point.');
  if (errors.length) return { ok: false, errors, cost: 0 };

  const archetypeKey = character.archetype;
  const from = Number((kind === 'potential' ? character.potentials : character.ratings)[rating]) || 0;
  const ceiling = kind === 'potential'
    ? potentialCeiling(rating, archetypeKey)
    : ratingCeiling(rating, archetypeKey, character.potentials);
  if (from + steps > ceiling) {
    errors.push(`That would reach ${from + steps}; the ceiling is ${ceiling}.`);
  }
  const cost = upgradeCost(from, steps, kind);
  const budget = Number(character.points_available || 0) - Number(reserved || 0);
  if (cost > budget) {
    errors.push(`That costs ${cost} and only ${budget} point${budget === 1 ? '' : 's'} are free.`);
  }
  return { ok: errors.length === 0, errors, cost };
}

/** The max-characters rule. `characters` is the caller's own list. */
export function canCreateAnother(characters, maxCharacters = 2) {
  const live = (characters || []).filter((c) => c.status !== 'retired').length;
  return { ok: live < maxCharacters, live, max: maxCharacters };
}

/** 76 -> 6'4" */
export function formatHeight(inches) {
  const n = Math.round(Number(inches));
  if (!Number.isFinite(n)) return '';
  return `${Math.floor(n / 12)}'${n % 12}"`;
}

/** One line of plain English for the help text, generated from COST_BANDS so it never drifts. */
export function describeCurve() {
  return COST_BANDS.map((b) => `${b.label}: ${b.cost}`).join(' | ')
    + ` | potential: x${POTENTIAL_MULTIPLIER}`;
}

/**
 * Codec field names, for the commissioner's benefit. The site talks in the 18/12 names
 * above; commissioner/codec/league_dat.py calls the potentials PotInside, PotJumpShot...
 * Keep this in step with POTENTIALS in that module.
 */
export const CODEC_POTENTIAL_NAMES = {
  InsideScoring: 'PotInside',
  JumpShot: 'PotJumpShot',
  FtShot: 'PotFtShot',
  '3pShot': 'Pot3pShot',
  Handling: 'PotHandling',
  Passing: 'PotPassing',
  OReb: 'PotOReb',
  DReb: 'PotDReb',
  PostDefense: 'PotPostDefense',
  PerimeterDefense: 'PotPerimeterDefense',
  Stealing: 'PotStealing',
  Blocking: 'PotBlocking',
};
