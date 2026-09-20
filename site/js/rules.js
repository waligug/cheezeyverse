/* =====================================================================================
 * Cheezeyverse rules - the cost curve, the quiz, the traits, the height model, and every
 * validation the site does.
 *
 * Pure data and pure functions. No DOM, no `window`, no network: create.html, me.html and
 * tests/test_rules.py all import this same file, and the test runs it under Node. It has
 * deliberately NO relative imports, because the test harness copies this one file on its
 * own into tmp/ and runs it there.
 *
 * WHAT CHANGED (player creation redesign)
 *   * Archetypes are gone as a *choice*. There are no caps and nothing is gated. What a
 *     player "is" is now a label computed from his ratings by classify(), recomputed every
 *     time the sheet moves.
 *   * You do not allocate stats at creation. You answer a quiz. Answers carry weights over
 *     a named TRAIT vector, and the traits derive the sheet, the potentials, the
 *     tendencies and a small cost bias. That indirection is the point: it keeps any one
 *     answer from being an obvious min-max button and makes the whole thing tunable by
 *     editing one table.
 *   * Height grows every offseason toward an expectation set by the `height_genes` trait,
 *     with diminishing returns and NO hard ceiling. The model is mirrored exactly in
 *     commissioner/growth.py and the two are pinned against each other by the tests.
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

/**
 * The two ratings that are *habits*, not ability. They are derived from traits by
 * tendencies() rather than from the position template plus a skill bonus, and they are
 * excluded from the "a 14 year old is terrible" bound, because 25 shots-from-three out of
 * 100 is not a skill level.
 */
export const TENDENCY_RATINGS = ['3pUsage', 'Fouling'];

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
 * buying it would be a trap - it is shown on the sheet but cannot be spent on. It is set
 * once, at creation, by tendencies(): a disciplined kid fouls less. Remove it from this
 * list if you ever pin the engine's behaviour down.
 *
 * 3pUsage is also a tendency but an obviously intentional one (how often he takes
 * threes). The quiz sets its starting value and it stays spendable afterwards - with no
 * archetype caps left, the cost curve is the only thing keeping a centre honest, which is
 * fine, because a centre who spends four months of points learning to chuck threes has
 * earned the right to embarrass himself.
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
export const MAX_HOMETOWN_LENGTH = 40;

/** FBPB3 jersey numbers are 0-99. */
export const JERSEY_MIN = 0;
export const JERSEY_MAX = 99;

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

/* --- the growth bias, applied on top of the curve, never inside it -------------------- */

/**
 * A character's traits make some ratings come a little more readily than others. The
 * effect is a percentage applied to the *finished* cost of a request, never a change to
 * the curve itself, and it is clamped hard so it can never become a min-max lever:
 *
 *     final cost = max(1, round(curve cost * bias% / 100))
 *
 * schema.sql applies exactly this line in cv_upgrade_request_guard(), after
 * cv_upgrade_cost() has returned, so the database stays the authority on price.
 */
export const BIAS_MIN = 85;
export const BIAS_MAX = 115;
export const BIAS_NEUTRAL = 100;

export function clampBias(pct) {
  const n = Math.round(Number(pct));
  if (!Number.isFinite(n)) return BIAS_NEUTRAL;
  return Math.max(BIAS_MIN, Math.min(BIAS_MAX, n));
}

/** max(1, round(cost * bias / 100)) - the same statement schema.sql runs. */
export function applyBias(cost, biasPct = BIAS_NEUTRAL) {
  const c = Number(cost);
  if (!Number.isFinite(c) || c <= 0) return 0;
  return Math.max(1, Math.round((c * clampBias(biasPct)) / 100));
}

/** upgradeCost with the character's bias for that rating already applied. */
export function biasedUpgradeCost(from, steps, kind = 'rating', biasPct = BIAS_NEUTRAL) {
  return applyBias(upgradeCost(from, steps, kind), biasPct);
}

/** Cost of the next single point, for the +1 button's price tag. */
export function nextPointCost(current, kind = 'rating', biasPct = BIAS_NEUTRAL) {
  return applyBias(upgradeCost(current, 1, kind), biasPct);
}

/**
 * How many points of `rating` you can afford out of `budget` before hitting `ceiling`.
 * Used to grey out the +1 button and to size the "max" shortcut.
 */
export function affordableSteps(from, budget, ceiling = RATING_MAX, kind = 'rating',
  biasPct = BIAS_NEUTRAL) {
  let steps = 0;
  let spent = 0;
  let v = Number(from);
  while (v < ceiling) {
    const next = applyBias(stepCost(v) * (kind === 'potential' ? POTENTIAL_MULTIPLIER : 1), biasPct);
    if (spent + next > budget) break;
    spent += next;
    v += 1;
    steps += 1;
  }
  return { steps, cost: spent };
}

/* ---------------------------------------------------------------------------------------
 * Traits
 * ------------------------------------------------------------------------------------ *
 * Eleven names, each 0-100, all starting at 50. Quiz answers move them; nothing else does.
 * They exist so that no answer writes a rating directly - every answer moves traits, and
 * exactly four documented functions turn traits into numbers the game understands:
 *
 *     startingSheet()  traits -> the 16 skills and the 12 potentials
 *     tendencies()     traits -> 3pUsage and Fouling
 *     growthBias()     traits -> a +-15% cost nudge per rating
 *     the height model traits -> height_genes -> an expected adult height
 *
 * Why these eleven: each one is a thing a person would say about a fourteen year old, and
 * each maps onto a cluster of FBPB3 ratings that really do move together. `frame` and
 * `explosiveness` are separate because they trade off (the thick kid is not the bouncy
 * one). `discipline` and `confidence` are separate because their *combination* is what
 * decides whether someone is a shooter or a chucker. `coachability` is the only one that
 * mostly buys headroom rather than present ability, which is exactly what it should mean
 * at fourteen. `height_genes` touches no rating at all - it feeds the growth model.
 * ------------------------------------------------------------------------------------ */

export const TRAITS = [
  'touch', 'handle', 'vision', 'motor', 'frame', 'explosiveness',
  'discipline', 'confidence', 'coachability', 'basketball_iq', 'height_genes',
];

export const TRAIT_LABELS = {
  touch: 'Touch',
  handle: 'Handle',
  vision: 'Vision',
  motor: 'Motor',
  frame: 'Frame',
  explosiveness: 'Explosiveness',
  discipline: 'Discipline',
  confidence: 'Confidence',
  coachability: 'Coachability',
  basketball_iq: 'Feel for the game',
  height_genes: 'Growing left to do',
};

/** What each trait is for, in one line, for the review screen. */
export const TRAIT_BLURBS = {
  touch: 'the ball goes in when he is left alone',
  handle: 'keeps it on a string under pressure',
  vision: 'sees the pass before the pass is there',
  motor: 'still running in the fourth quarter',
  frame: 'holds his ground and takes up room',
  explosiveness: 'first step and second jump',
  discipline: 'takes the right shot, does not reach',
  confidence: 'wants it, whether or not he should',
  coachability: 'how much of the ceiling he will actually reach',
  basketball_iq: 'knows where everyone is going next',
  height_genes: 'how much taller he is going to get',
};

export const TRAIT_BASE = 50;
export const TRAIT_MIN = 0;
export const TRAIT_MAX = 100;

export function blankTraits() {
  const out = {};
  for (const t of TRAITS) out[t] = TRAIT_BASE;
  return out;
}

function clampTrait(v) {
  return Math.max(TRAIT_MIN, Math.min(TRAIT_MAX, Math.round(Number(v) || 0)));
}

/* ---------------------------------------------------------------------------------------
 * The quiz
 * ------------------------------------------------------------------------------------ *
 * Fourteen questions about who he is, then a career goal and one summer. Nothing here is
 * a slider, and no answer is written as "+5 Passing" - `measures` says what the question
 * is really for so these are easy to rewrite without guessing.
 *
 * House rule that keeps this honest, and that tests/test_rules.py enforces: every answer
 * carries at least one NEGATIVE weight on a trait no sibling answer is also negative on.
 * That makes it impossible for any answer to Pareto-dominate another - every answer is
 * strictly worse than every other answer at something - so there is never a "best" button.
 *
 * Two questions deliberately interact rather than adding up: `scored_on` / `last_shot` /
 * `role_change` move confidence, `open_three` / `conditioning` move discipline, and it is
 * the pair of them that tendencies() reads to decide whether this kid starts as a shooter
 * or a chucker. High confidence with low discipline is the highest 3pUsage in the game.
 * ------------------------------------------------------------------------------------ */

export const QUIZ = [
  {
    id: 'roots',
    prompt: 'Where did you actually learn the game?',
    measures: 'the environment that got the reps - crowds and contact, or repetition and coaching',
    answers: [
      { id: 'park', text: 'The park. Outdoor rim, no net, grown men, winners stay on.',
        weights: { motor: 6, confidence: 6, frame: 4, coachability: -5 } },
      { id: 'church', text: 'A church league my dad coached. Every Saturday for six years.',
        weights: { coachability: 6, discipline: 5, basketball_iq: 4, explosiveness: -4 } },
      { id: 'driveway', text: 'My driveway. Alone. Thousands of shots nobody ever saw.',
        weights: { touch: 7, handle: 4, confidence: -4 } },
      { id: 'travel', text: 'A travel program with real coaches from the time I was nine.',
        weights: { basketball_iq: 6, handle: 4, coachability: 3, motor: -4 } },
    ],
  },
  {
    id: 'empty_gym',
    prompt: 'The gym is empty and you have it for an hour. What happens?',
    measures: 'what he works on when nobody is making him - the single strongest skill signal',
    answers: [
      { id: 'shots', text: 'Shoot. Same spot until it stops missing, then move a step.',
        weights: { touch: 8, discipline: 3, motor: -4 } },
      { id: 'handles', text: 'Two balls and some cones, until it is boring and then past that.',
        weights: { handle: 8, discipline: 3, frame: -4 } },
      { id: 'iron', text: 'Weights, then sprints. The skill stuff can wait for practice.',
        weights: { frame: 6, explosiveness: 5, motor: 3, touch: -5 } },
      { id: 'film', text: 'Sit in the corner with film on my phone.',
        weights: { basketball_iq: 8, vision: 4, explosiveness: -5 } },
    ],
  },
  {
    id: 'scored_on',
    prompt: 'Somebody scores on you. Badly. What do you do next?',
    measures: 'the confidence axis, and whether the response is controlled or hot',
    answers: [
      { id: 'quiet', text: 'Say nothing. Get it back at the other end.',
        weights: { confidence: 6, discipline: 4, vision: -3 } },
      { id: 'chest', text: 'Get in his chest the next three trips and make him hate it.',
        weights: { motor: 6, frame: 3, confidence: 3, discipline: -5 } },
      { id: 'ask', text: 'Ask the coach what I did wrong on that one.',
        weights: { coachability: 7, basketball_iq: 5, confidence: -5 } },
      { id: 'shrug', text: 'Nothing. He got one. It happens to everybody.',
        weights: { discipline: 3, confidence: 4, motor: -5 } },
    ],
  },
  {
    id: 'family',
    prompt: 'How tall are the adults in your family?',
    measures: 'height_genes, and nothing else really - this is the one question that sets how much growing is left',
    answers: [
      { id: 'giants', text: 'Everybody ducks in doorways. My uncle is nearly seven feet.',
        weights: { height_genes: 30, frame: 4, explosiveness: -5 } },
      { id: 'tallish', text: 'Tall enough. My dad played, my mum is taller than my friends.',
        weights: { height_genes: 16, frame: 2, handle: -3 } },
      { id: 'average', text: 'Dead average. Nobody is short, nobody is tall.',
        weights: { height_genes: 2, motor: 2, frame: -2 } },
      { id: 'short', text: 'Short. All of them. I am already the tallest in the house.',
        weights: { height_genes: -18, handle: 5, touch: 4, confidence: 3 } },
    ],
  },
  {
    id: 'body',
    prompt: 'Your body, right now, at fourteen.',
    measures: 'frame against explosiveness - they trade off, and the build picker leans the same lever',
    answers: [
      { id: 'skinny', text: 'Skinny. All arms and legs. I have not grown into it yet.',
        weights: { explosiveness: 5, handle: 3, height_genes: 6, frame: -7 } },
      { id: 'thick', text: 'Thick already. I move people off the block and it is not close.',
        weights: { frame: 8, motor: 3, explosiveness: -6 } },
      { id: 'springy', text: 'Springy. I am off the floor before I decide to be.',
        weights: { explosiveness: 9, motor: 3, basketball_iq: -5 } },
      { id: 'engine', text: 'Nothing special to look at, but I have never been tired in my life.',
        weights: { motor: 8, discipline: 4, height_genes: -5 } },
    ],
  },
  {
    id: 'last_shot',
    prompt: 'Tie game, last shot, and the coach draws it up for somebody else.',
    measures: 'confidence again, from the other side - who needs the ball to matter',
    answers: [
      { id: 'screen', text: 'Good. I will set the best screen of my life.',
        weights: { discipline: 6, coachability: 5, motor: 3, confidence: -6 } },
      { id: 'open', text: 'I will get open anyway. He will find me.',
        weights: { confidence: 6, vision: 4, frame: -4 } },
      { id: 'mine', text: 'I am taking it. I do not care what he drew up.',
        weights: { confidence: 9, touch: 3, coachability: -7 } },
      { id: 'board', text: 'Fine. I am getting the rebound when it misses.',
        weights: { motor: 5, frame: 5, touch: -4 } },
    ],
  },
  {
    id: 'open_three',
    prompt: 'You catch it wide open from deep.',
    measures: 'shot selection - this and the confidence questions together set 3pUsage',
    answers: [
      { id: 'always', text: 'Up. Every single time.',
        weights: { confidence: 7, touch: 4, discipline: -6 } },
      { id: 'rhythm', text: 'Up, if it is in rhythm and my feet are already set.',
        weights: { touch: 6, discipline: 5, motor: -3 } },
      { id: 'extra', text: 'One more pass is nearly always a better shot than mine.',
        weights: { vision: 7, basketball_iq: 4, confidence: -5 } },
      { id: 'drive', text: 'I would rather put it on the floor and get to the rim.',
        weights: { explosiveness: 6, handle: 5, touch: -5 } },
    ],
  },
  {
    id: 'missed_pass',
    prompt: 'A teammate misses you when you were standing there wide open.',
    measures: 'vision and feel against noise - does he fix the read or fix the teammate',
    answers: [
      { id: 'loud', text: 'Tell him. Loudly. It only happens once.',
        weights: { confidence: 5, motor: 3, coachability: -5 } },
      { id: 'harder', text: 'Get open again, harder, until he cannot miss me.',
        weights: { motor: 6, discipline: 3, vision: -4 } },
      { id: 'adjust', text: 'He does not see that read. I will stand somewhere he can.',
        weights: { vision: 7, basketball_iq: 6, confidence: -4 } },
      { id: 'wait', text: 'Nothing. He will find me eventually.',
        weights: { discipline: 4, touch: 2, motor: -5 } },
    ],
  },
  {
    id: 'role_change',
    prompt: 'The coach moves you out of the role you wanted.',
    measures: 'coachability, which mostly buys potential rather than present ability',
    answers: [
      { id: 'whatever', text: 'Whatever wins. Tell me what you need and it is done.',
        weights: { coachability: 9, discipline: 4, confidence: -5 } },
      { id: 'why', text: 'I will do it, and I will keep asking why until I understand it.',
        weights: { basketball_iq: 7, coachability: 4, motor: -4 } },
      { id: 'stubborn', text: 'I know what I am. He will work it out by February.',
        weights: { confidence: 8, touch: 3, coachability: -8 } },
    ],
  },
  {
    id: 'conditioning',
    prompt: 'Two hours of conditioning. No ball comes out of the bag.',
    measures: 'motor and discipline - the pair that decides whether the body holds up',
    answers: [
      { id: 'love', text: 'Best day of the week. Genuinely.',
        weights: { motor: 8, discipline: 5, touch: -5 } },
      { id: 'grind', text: 'I will finish every rep and hate every second of it quietly.',
        weights: { discipline: 7, motor: 4, confidence: -4 } },
      { id: 'pace', text: 'Pace it. I need my legs on Friday, not on Tuesday.',
        weights: { basketball_iq: 5, touch: 3, motor: -6 } },
      { id: 'skip', text: 'I will be at the back of the line, looking for a ball.',
        weights: { touch: 5, handle: 4, discipline: -7 } },
    ],
  },
  {
    id: 'new_team',
    prompt: 'First day with a new team. Nobody knows who you are.',
    measures: 'how he establishes himself - talk, work, score, or defend',
    answers: [
      { id: 'talk', text: 'Talk. Learn every name before the first water break.',
        weights: { vision: 5, coachability: 4, confidence: 3, frame: -4 } },
      { id: 'work', text: 'Say nothing and out-work all of them until somebody notices.',
        weights: { motor: 7, discipline: 4, vision: -4 } },
      { id: 'score', text: 'Score. That settles it faster than anything I could say.',
        weights: { confidence: 7, touch: 4, coachability: -5 } },
      { id: 'guard', text: 'Guard their best player and take it personally.',
        weights: { frame: 4, explosiveness: 4, basketball_iq: 3, touch: -5 } },
    ],
  },
  {
    id: 'annoyance',
    prompt: 'What actually annoys you during a game?',
    measures: 'where his attention lives, which is a good proxy for what he practises',
    answers: [
      { id: 'rotation', text: 'A blown defensive rotation. Ours.',
        weights: { basketball_iq: 7, discipline: 5, confidence: -4 } },
      { id: 'boards', text: 'Getting out-rebounded. I take that personally.',
        weights: { motor: 6, frame: 5, touch: -4 } },
      { id: 'read', text: 'A pass I should have made and did not make.',
        weights: { vision: 7, handle: 3, frame: -4 } },
      { id: 'missing', text: 'Missing. Just missing. Nothing else bothers me.',
        weights: { touch: 7, confidence: 4, basketball_iq: -5 } },
    ],
  },
  {
    id: 'idol',
    prompt: 'The first game you ever watched all the way through - who did you copy?',
    measures: 'the shape of the player he is trying to be, before anyone tells him what he is',
    answers: [
      { id: 'pace', text: 'A guard who never sped up and never got sped up.',
        weights: { handle: 6, basketball_iq: 5, explosiveness: -5 } },
      { id: 'wall', text: 'A big who did not let one single thing near the rim.',
        weights: { frame: 5, explosiveness: 4, motor: 3, touch: -5 } },
      { id: 'logo', text: 'Somebody who shot it from the logo without blinking.',
        weights: { touch: 7, confidence: 5, frame: -5 } },
      { id: 'dimes', text: 'The one with fourteen assists who never shot it once.',
        weights: { vision: 8, coachability: 4, confidence: -5 } },
    ],
  },
  {
    id: 'adversity',
    prompt: 'It is going badly. Nothing is falling and the gym is on you.',
    measures: 'the last confidence read, and the one that decides if he shoots his way out',
    answers: [
      { id: 'keep', text: 'Keep shooting. It goes in eventually. It always has.',
        weights: { confidence: 8, touch: 3, discipline: -6 } },
      { id: 'elsewhere', text: 'Get it somewhere else. Defend, rebound, set screens.',
        weights: { motor: 5, discipline: 5, frame: 3, confidence: -5 } },
      { id: 'simplify', text: 'Simplify. Two easy passes and a layup and I am back.',
        weights: { basketball_iq: 6, vision: 5, explosiveness: -4 } },
      { id: 'foul', text: 'Go get fouled. Two free throws fixes most things.',
        weights: { handle: 5, explosiveness: 4, touch: 2, basketball_iq: -5 } },
    ],
  },
];

export const QUIZ_IDS = QUIZ.map((q) => q.id);

/**
 * The career goal. Stored on the character, shown on his page as a line of character, and
 * worth one small mechanical thing: THE FOUR RATINGS THE GOAL NAMES COST `GOAL_DISCOUNT`% LESS PER
 * POINT, forever. That is the whole effect - it stacks with the trait bias into the same
 * clamped 85..115 window, so it can never be more than a nudge.
 */
export const CAREER_GOALS = [
  {
    id: 'bucket',
    label: 'Put my name at the top of the scoring list',
    line: 'He wants to lead the league in scoring. He has wanted it since he was nine.',
    discount: ['InsideScoring', 'JumpShot', '3pShot', 'FtShot'],
    weights: { confidence: 6, touch: 3, discipline: -4 },
  },
  {
    id: 'ring',
    label: 'Win something at every level I play at',
    line: 'He would rather be the fourth best player on the last team standing.',
    discount: ['PerimeterDefense', 'Passing', 'Stamina', 'DReb'],
    weights: { coachability: 6, discipline: 4, confidence: -4 },
  },
  {
    id: 'nobody_scores',
    label: 'Be the man nobody in the gym can score on',
    line: 'He keeps a private list of everyone who has scored on him.',
    discount: ['PerimeterDefense', 'PostDefense', 'Stealing', 'Blocking'],
    weights: { discipline: 5, motor: 4, touch: -4 },
  },
  {
    id: 'orchestrate',
    label: 'Make every single player around me better',
    line: 'He would rather have the assist. He has always been like that.',
    discount: ['Passing', 'Handling', 'Stealing', 'FtShot'],
    weights: { vision: 6, basketball_iq: 4, frame: -4 },
  },
  {
    id: 'never_off',
    label: 'Never come off the floor, not once, not ever',
    line: 'He treats a substitution as a personal insult and always has.',
    discount: ['Stamina', 'Strength', 'DReb', 'OReb'],
    weights: { motor: 6, frame: 4, touch: -4 },
  },
];

/** A trait of exactly 0 is a real value, not a missing one - `|| TRAIT_BASE` would hide it. */
function coachTrait(traits) {
  const v = Number((traits || {}).coachability);
  return Number.isFinite(v) ? v : TRAIT_BASE;
}

export const GOAL_DISCOUNT = 5; // percent off the cost of a step, on the four it names

/**
 * The one piece of direct agency left in creation: what he did over the summer before he
 * turned up. It nudges a small, named group of ratings by a couple of points and leans a
 * trait or two. This is flavour first and mechanics second, on purpose - the quiz is
 * meant to be doing the shaping.
 */
export const SUMMER_WORK = [
  {
    id: 'jumper',
    label: 'Shooting. Six hundred a day, charted, all summer.',
    ratings: ['JumpShot', '3pShot', 'FtShot'], bump: 3,
    weights: { touch: 4, motor: -2 },
  },
  {
    id: 'handles',
    label: 'Ball handling. Two balls, tennis balls, the whole circus.',
    ratings: ['Handling', 'Passing', 'Quickness'], bump: 3,
    weights: { handle: 4, frame: -2 },
  },
  {
    id: 'body',
    label: 'The weight room and the track. Nothing else.',
    ratings: ['Strength', 'Stamina', 'Jumping'], bump: 3,
    weights: { frame: 3, motor: 2, touch: -2 },
  },
  {
    id: 'defense',
    label: 'Slides, closeouts, and film of my own mistakes.',
    ratings: ['PerimeterDefense', 'PostDefense', 'Stealing'], bump: 3,
    weights: { discipline: 4, confidence: -2 },
  },
  {
    id: 'boards',
    label: 'Rebounding drills nobody else volunteered for.',
    ratings: ['OReb', 'DReb', 'Blocking'], bump: 3,
    weights: { motor: 3, frame: 2, handle: -2 },
  },
  {
    id: 'played',
    label: 'Nothing organised. I just played, every day, all day.',
    ratings: ['InsideScoring', 'Handling', 'Passing', 'PerimeterDefense'], bump: 2,
    weights: { confidence: 3, basketball_iq: 3, discipline: -3 },
  },
];

/**
 * Build. A form field rather than a quiz question, because it is a fact about him, but it
 * leans the same frame/explosiveness lever the `body` question does, so the two agree or
 * argue and the result is somewhere in between.
 */
export const BUILDS = [
  { id: 'wiry', label: 'Wiry', blurb: 'light, quick, gets moved', lbs: -14,
    weights: { frame: -8, explosiveness: 6, motor: 2 } },
  { id: 'lean', label: 'Lean', blurb: 'long and light on his feet', lbs: -7,
    weights: { frame: -3, explosiveness: 3 } },
  { id: 'solid', label: 'Solid', blurb: 'normal for his age', lbs: 0, weights: {} },
  { id: 'strong', label: 'Strong', blurb: 'already built, already stronger', lbs: 9,
    weights: { frame: 6, explosiveness: -3 } },
  { id: 'heavy', label: 'Heavy', blurb: 'wide, immovable, slow to the corner', lbs: 20,
    weights: { frame: 10, explosiveness: -7, motor: -3 } },
];

export const BUILD_IDS = BUILDS.map((b) => b.id);

export function quizQuestion(id) { return QUIZ.find((q) => q.id === id) || null; }
export function careerGoal(id) { return CAREER_GOALS.find((g) => g.id === id) || null; }
export function summerWork(id) { return SUMMER_WORK.find((s) => s.id === id) || null; }
export function buildOption(id) { return BUILDS.find((b) => b.id === id) || null; }

/**
 * Weight in pounds, for the save file and for the review screen. FBPB3 stores an int16.
 * Derived rather than stored, so there is one fewer column and one fewer thing to keep in
 * step: the commissioner recomputes it from height_inches and build whenever it writes a
 * player into league.dat.
 */
export function buildWeight(heightInches, buildId) {
  const h = Number(heightInches) || 70;
  const b = buildOption(buildId);
  return Math.round((h - 60) * 4.6 + 96) + (b ? b.lbs : 0);
}

/* ---------------------------------------------------------------------------------------
 * Quiz -> traits
 * ------------------------------------------------------------------------------------ */

/**
 * `answers` is { questionId: answerId }. Unanswered questions contribute nothing, so a
 * half-finished quiz still derives a legal (boring) player and the review screen can
 * update as you go. Build, goal and summer all lean traits too; they are passed in
 * separately because they are not quiz questions.
 *
 * Pure. Same input, same output, always - tests/test_rules.py pins that.
 */
export function traitsFromQuiz(answers, opts = {}) {
  const traits = blankTraits();
  const add = (weights) => {
    for (const [t, w] of Object.entries(weights || {})) {
      if (traits[t] === undefined) continue;
      traits[t] += Number(w) || 0;
    }
  };

  for (const q of QUIZ) {
    const picked = (answers || {})[q.id];
    const answer = q.answers.find((a) => a.id === picked);
    if (answer) add(answer.weights);
  }
  const build = buildOption(opts.build);
  if (build) add(build.weights);
  const goal = careerGoal(opts.goal);
  if (goal) add(goal.weights);
  const summer = summerWork(opts.summer);
  if (summer) add(summer.weights);

  for (const t of TRAITS) traits[t] = clampTrait(traits[t]);
  return traits;
}

/** How many of the fourteen have been answered. */
export function quizProgress(answers) {
  const done = QUIZ.filter((q) => q.answers.some((a) => a.id === (answers || {})[q.id])).length;
  return { done, total: QUIZ.length, complete: done === QUIZ.length };
}

/* ---------------------------------------------------------------------------------------
 * Traits -> the sheet
 * ------------------------------------------------------------------------------------ *
 * SHEET_WEIGHTS is the whole translation layer, and it is the first thing to tune. Each
 * number is roughly "points on this rating when the trait is at 100 instead of 50", before
 * the total is clamped. The two tendencies are not here - tendencies() owns them.
 * ------------------------------------------------------------------------------------ */

export const SHEET_WEIGHTS = {
  InsideScoring: { explosiveness: 4, frame: 3, confidence: 2, touch: 1 },
  JumpShot: { touch: 7, discipline: 2, confidence: 1 },
  FtShot: { touch: 6, discipline: 3, basketball_iq: 1 },
  '3pShot': { touch: 7, confidence: 2, discipline: 1 },
  Handling: { handle: 8, basketball_iq: 1, explosiveness: 1 },
  Passing: { vision: 7, basketball_iq: 3 },
  Quickness: { explosiveness: 6, motor: 2, frame: -2 },
  PostDefense: { frame: 6, basketball_iq: 2, discipline: 2 },
  PerimeterDefense: { discipline: 4, motor: 3, basketball_iq: 2, explosiveness: 1 },
  Stealing: { basketball_iq: 4, motor: 3, handle: 1, discipline: -1 },
  Blocking: { explosiveness: 5, frame: 2, height_genes: 2, discipline: -1 },
  OReb: { motor: 5, frame: 3, explosiveness: 2 },
  DReb: { frame: 4, motor: 3, basketball_iq: 2, height_genes: 1 },
  Jumping: { explosiveness: 8, motor: 1, frame: -1 },
  Strength: { frame: 8, motor: 1 },
  Stamina: { motor: 8, discipline: 2, frame: -1 },
};

/** The 16 that SHEET_WEIGHTS covers: everything except the two tendencies. */
export const SKILL_RATINGS = RATINGS.filter((r) => !TENDENCY_RATINGS.includes(r));

/** A brand new 14 year old is terrible. Nothing on the sheet starts above this. */
export const START_RATING_CEILING = 44;
export const START_RATING_FLOOR = 4;
/** And his potentials sit inside the prep filler band (25-58 in universe/config.py). */
export const START_POTENTIAL_CEILING = 95;

/** The most a trait profile can move one rating off its position template, either way. */
export const TRAIT_SWING = 8;

/**
 * A mediocre 14 year old, by position. Nobody starts good: the highest number here is 22.
 * The tendency rows are the position's *habit* and tendencies() moves them from there.
 */
export const POSITION_TEMPLATES = {
  PG: {
    InsideScoring: 19, JumpShot: 24, FtShot: 30, '3pUsage': 25, '3pShot': 22,
    Handling: 32, Passing: 32, Quickness: 34, PostDefense: 12, PerimeterDefense: 22,
    Stealing: 24, Blocking: 9, OReb: 12, DReb: 16, Jumping: 25, Strength: 15,
    Stamina: 20, Fouling: 25,
  },
  SG: {
    InsideScoring: 22, JumpShot: 27, FtShot: 28, '3pUsage': 28, '3pShot': 25,
    Handling: 27, Passing: 24, Quickness: 31, PostDefense: 15, PerimeterDefense: 25,
    Stealing: 22, Blocking: 12, OReb: 15, DReb: 19, Jumping: 28, Strength: 18,
    Stamina: 19, Fouling: 26,
  },
  SF: {
    InsideScoring: 25, JumpShot: 24, FtShot: 25, '3pUsage': 22, '3pShot': 21,
    Handling: 22, Passing: 21, Quickness: 27, PostDefense: 19, PerimeterDefense: 24,
    Stealing: 19, Blocking: 18, OReb: 21, DReb: 24, Jumping: 28, Strength: 22,
    Stamina: 18, Fouling: 28,
  },
  PF: {
    InsideScoring: 28, JumpShot: 19, FtShot: 22, '3pUsage': 14, '3pShot': 15,
    Handling: 16, Passing: 18, Quickness: 21, PostDefense: 25, PerimeterDefense: 19,
    Stealing: 16, Blocking: 25, OReb: 28, DReb: 31, Jumping: 27, Strength: 28,
    Stamina: 17, Fouling: 32,
  },
  C: {
    InsideScoring: 31, JumpShot: 15, FtShot: 19, '3pUsage': 10, '3pShot': 11,
    Handling: 14, Passing: 15, Quickness: 18, PostDefense: 28, PerimeterDefense: 15,
    Stealing: 14, Blocking: 31, OReb: 30, DReb: 32, Jumping: 25, Strength: 31,
    Stamina: 16, Fouling: 34,
  },
};

/**
 * How well this trait profile suits one rating, as -1..+1. One function, used three
 * times: the starting rating leans on it, the starting potential leans on it harder, and
 * growthBias() turns it into a price.
 */
export function ratingAffinity(rating, traits) {
  const weights = SHEET_WEIGHTS[rating];
  if (!weights) return 0;
  let sum = 0;
  let scale = 0;
  for (const [t, w] of Object.entries(weights)) {
    const v = Number((traits || {})[t]);
    sum += w * ((Number.isFinite(v) ? v : TRAIT_BASE) - TRAIT_BASE) / 50;
    scale += Math.abs(w);
  }
  if (!scale) return 0;
  return Math.max(-1, Math.min(1, sum / scale));
}

/**
 * 3pUsage and Fouling, from traits. These are habits, so they are set once and read like
 * a personality rather than a skill level.
 *
 *   3pUsage  = the position's habit, plus a lot of confidence, plus some touch, MINUS
 *              discipline. That last sign is the interesting one: the kid who says "up,
 *              every single time" (confidence up, discipline down) ends up with the
 *              highest usage in the game, and the kid who says "one more pass is better"
 *              ends up barely shooting them. Neither answer is a slider, and neither is
 *              wrong - it just decides which player he is.
 *   Fouling  = the position's habit, minus discipline, plus motor and frame. It is in
 *              LOCKED_RATINGS so it can never be bought, which is exactly why deriving it
 *              from the quiz is worth doing.
 */
export function tendencies(position, traits) {
  const template = POSITION_TEMPLATES[position] || POSITION_TEMPLATES.SF;
  const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : TRAIT_BASE);
  const t = (k) => num((traits || {})[k]) - TRAIT_BASE;

  const usage = template['3pUsage']
    + (t('confidence') * 45) / 100
    + (t('touch') * 35) / 100
    - (t('discipline') * 15) / 100;

  const fouling = template.Fouling
    - (t('discipline') * 50) / 100
    + (t('motor') * 20) / 100
    + (t('frame') * 15) / 100;

  return {
    '3pUsage': Math.max(2, Math.min(92, Math.round(usage))),
    Fouling: Math.max(5, Math.min(90, Math.round(fouling))),
  };
}

/**
 * The starting sheet. Position template, plus the trait swing, plus the summer's nudge,
 * then clamped - in that order, so the summer can never push a rating past the ceiling.
 *
 * Potentials come off the finished ratings: a flat 12 of headroom, leaning further out on
 * the things his traits suit and pulling in on the things they do not, plus coachability,
 * which is the trait that mostly buys ceiling rather than ability. Everything lands inside
 * A ceiling is a PROJECTION, not a current stat, so it is not anchored to the filler band the
 * way the starting ratings are. Anchoring it there was a real bug: it gave a fresh character
 * 9 to 18 points of headroom, which one season of points exhausts, and left him with a lower
 * ceiling than the AI free agent beside him. A ceiling has to hold a whole career - prep,
 * college and a professional peak - so it sits where a scout would put it on a fourteen year
 * old, and the quiz decides WHERE it is high rather than whether it is high at all.
 *
 * It matters twice over: FBPB3's own progression develops a young player toward his potential
 * on its own, so a low ceiling was also suppressing the growth we were not paying for.
 *
 * Invariant this guarantees, which everything downstream relies on:
 *     START_RATING_FLOOR <= rating <= START_RATING_CEILING <= potential <= START_POTENTIAL_CEILING
 * for every skill, for every position, for every possible set of quiz answers.
 */
export const POTENTIAL_HEADROOM = 34;
export const POTENTIAL_AFFINITY_SWING = 22;
export const POTENTIAL_COACH_SWING = 12;
export const POTENTIAL_MIN_HEADROOM = 3;

/**
 * THE ROLL.
 *
 * The trait maths above decides the middle of the distribution; a seeded roll decides
 * where in it he actually lands. Two reasons, and the second is the important one:
 *
 *   1. Two people who answer the quiz identically should not get the same player.
 *   2. Nobody should be able to sit on the form and watch a number tick up as they flip
 *      an answer, because then the quiz is just a stat allocator with extra steps. The
 *      page shows a shaded band and a scout's opinion until he is signed, and the band is
 *      genuinely the set of values the roll can produce - not decoration over a fixed
 *      number.
 *
 * The band is WIDE where the answers said little about that skill and TIGHT where they
 * were emphatic. A kid whose every answer was about shooting really will be a shooter;
 * how good a rebounder he turns out to be is anyone's guess.
 *
 * The seed is the identity (identitySeed), not the answers, so re-submitting the same
 * form is not a reroll.
 */
export const ROLL_BAND_MAX = 6;         // skills, when nothing in the quiz pointed at it
export const ROLL_BAND_MIN = 2;         // skills, when every answer pointed at it
export const TENDENCY_BAND_MAX = 8;
export const TENDENCY_BAND_MIN = 3;
export const POTENTIAL_BAND_MAX = 5;
export const POTENTIAL_BAND_MIN = 2;

function bandFor(max, min, conviction) {
  return Math.round(max - (max - min) * Math.max(0, Math.min(1, conviction)));
}

/** How emphatic the quiz was about the two habits. */
function tendencyConviction(traits) {
  const off = (k) => {
    const v = Number((traits || {})[k]);
    return Math.abs((Number.isFinite(v) ? v : TRAIT_BASE) - TRAIT_BASE);
  };
  return Math.min(1, (off('confidence') + off('discipline') + off('touch')) / 90);
}

/** The roll band for every one of the 18, in the canonical order. */
export function ratingBands(traits) {
  const out = {};
  const tend = tendencyConviction(traits);
  for (const r of RATINGS) {
    out[r] = TENDENCY_RATINGS.includes(r)
      ? bandFor(TENDENCY_BAND_MAX, TENDENCY_BAND_MIN, tend)
      : bandFor(ROLL_BAND_MAX, ROLL_BAND_MIN, Math.abs(ratingAffinity(r, traits)));
  }
  return out;
}

/** And for the 12 potentials. */
export function potentialBands(traits) {
  const out = {};
  for (const r of POTENTIAL_RATINGS) {
    out[r] = bandFor(POTENTIAL_BAND_MAX, POTENTIAL_BAND_MIN, Math.abs(ratingAffinity(r, traits)));
  }
  return out;
}

/**
 * The starting sheet. Position template, plus the trait swing, plus the summer's nudge,
 * plus the seeded roll, then clamped - in that order, so nothing downstream of the clamp
 * can push a rating past the ceiling.
 *
 * Potentials come off the finished ratings: a flat 12 of headroom, leaning further out on
 * the things his traits suit and pulling in on the things they do not, plus coachability,
 * which is the trait that mostly buys ceiling rather than ability, plus its own smaller
 * roll. Everything lands inside the prep filler potential band, so a created 14 year old
 * reads as a real prospect next to the AI population rather than as an alien.
 *
 * Invariant this guarantees, which everything downstream relies on:
 *     START_RATING_FLOOR <= rating <= START_RATING_CEILING <= potential <= START_POTENTIAL_CEILING
 * for every skill, for every position, for every set of answers, for every seed.
 *
 * `ranges` and `potentialRanges` are what the create page draws: for each rating, the
 * lowest and highest value the roll could have produced from these answers.
 *
 * @param {string} position
 * @param {object} traits
 * @param {{summer?: string, seed?: number}} opts
 */
export function startingSheet(position, traits, opts = {}) {
  const template = POSITION_TEMPLATES[position];
  if (!template) throw new Error(`unknown position ${position}`);
  const summer = summerWork(opts.summer);
  const seed = Number(opts.seed) >>> 0;

  const bands = ratingBands(traits);
  const potBands = potentialBands(traits);
  const swings = rollSwings(seed, [
    ...RATINGS.map((r) => bands[r]),
    ...POTENTIAL_RATINGS.map((r) => potBands[r]),
  ]);
  const swingOf = {};
  RATINGS.forEach((r, i) => { swingOf[r] = swings[i]; });
  POTENTIAL_RATINGS.forEach((r, i) => { swingOf[`pot:${r}`] = swings[RATINGS.length + i]; });

  const habits = tendencies(position, traits);
  const clampSkill = (v) => Math.max(START_RATING_FLOOR, Math.min(START_RATING_CEILING, v));
  const clampHabit = (r, v) => (r === '3pUsage'
    ? Math.max(2, Math.min(92, v))
    : Math.max(5, Math.min(90, v)));

  const ratings = {};
  const ranges = {};
  for (const r of RATINGS) {
    const band = bands[r];
    if (TENDENCY_RATINGS.includes(r)) {
      const mid = habits[r];
      ratings[r] = clampHabit(r, mid + swingOf[r]);
      ranges[r] = [clampHabit(r, mid - band), clampHabit(r, mid + band)];
    } else {
      const nudge = summer && summer.ratings.includes(r) ? summer.bump : 0;
      const mid = template[r] + Math.round(TRAIT_SWING * ratingAffinity(r, traits)) + nudge;
      ratings[r] = clampSkill(mid + swingOf[r]);
      ranges[r] = [clampSkill(mid - band), clampSkill(mid + band)];
    }
  }

  const coach = Math.round(
    (POTENTIAL_COACH_SWING * (coachTrait(traits) - TRAIT_BASE)) / 50,
  );
  const potentials = {};
  const potentialRanges = {};
  for (const r of POTENTIAL_RATINGS) {
    const lean = Math.round(POTENTIAL_AFFINITY_SWING * ratingAffinity(r, traits));
    const band = potBands[r];
    const want = (base) => base + POTENTIAL_HEADROOM + lean + coach;
    const clampPot = (v, floorRating) => Math.max(floorRating + POTENTIAL_MIN_HEADROOM,
      Math.min(START_POTENTIAL_CEILING, v));
    potentials[r] = clampPot(want(ratings[r]) + swingOf[`pot:${r}`], ratings[r]);
    potentialRanges[r] = [
      clampPot(want(ranges[r][0]) - band, ranges[r][0]),
      clampPot(want(ranges[r][1]) + band, ranges[r][1]),
    ];
  }
  return { ratings, potentials, ranges, potentialRanges, bands, potentialBands: potBands };
}

/**
 * The growth bias: which ratings come a little more readily. -10% for a rating his traits
 * suit, +10% for one they do not, another -5% on the four his career goal names, and the
 * whole thing clamped to 85..115 so it stays a nudge. Locked ratings are always neutral.
 *
 * The database applies this, not the browser: see the note on applyBias().
 */
export function growthBias(traits, goalId) {
  const goal = careerGoal(goalId);
  const out = {};
  for (const r of RATINGS) {
    if (LOCKED_RATINGS.includes(r)) { out[r] = BIAS_NEUTRAL; continue; }
    let pct = BIAS_NEUTRAL - Math.round(10 * ratingAffinity(r, traits));
    if (goal && goal.discount.includes(r)) pct -= GOAL_DISCOUNT;
    out[r] = clampBias(pct);
  }
  return out;
}

/** The bias for one rating out of a stored bias map, defaulting to neutral. */
export function biasFor(bias, rating) {
  const pct = (bias || {})[rating];
  return pct === undefined ? BIAS_NEUTRAL : clampBias(pct);
}

/* ---------------------------------------------------------------------------------------
 * Emergent class
 * ------------------------------------------------------------------------------------ *
 * A label, not a contract. classify() is recomputed every time the sheet moves, sets no
 * caps and gates nothing at all. Weights are a *shape*: what stands out on this sheet
 * relative to the rest of it, not how good the sheet is. That matters, because a 14 year
 * old whose whole sheet is in the teens still has a shape.
 * ------------------------------------------------------------------------------------ */

export const CLASSES = [
  {
    id: 'slasher', label: 'Slasher',
    blurb: 'Lives at the rim. Quick, bouncy, gets fouled; not a shooter yet.',
    positions: ['PG', 'SG', 'SF'],
    weights: {
      InsideScoring: 5, Quickness: 4, Jumping: 3, Handling: 3, FtShot: 1,
      '3pShot': -3, '3pUsage': -2, Blocking: -2,
    },
  },
  {
    id: 'sharpshooter', label: 'Sharpshooter',
    blurb: 'Pure shooter. Everything outside eighteen feet; nothing much inside it.',
    positions: ['PG', 'SG', 'SF'],
    weights: {
      '3pShot': 6, JumpShot: 5, '3pUsage': 4, FtShot: 3,
      InsideScoring: -3, Strength: -2, Blocking: -2, OReb: -2,
    },
  },
  {
    id: 'playmaker', label: 'Playmaker',
    blurb: 'Runs the team. Handle, vision and hands; small, and not a rebounder.',
    positions: ['PG', 'SG'],
    weights: {
      Passing: 6, Handling: 6, Stealing: 3, Quickness: 3,
      OReb: -3, Strength: -3, Blocking: -3, PostDefense: -2,
    },
  },
  {
    id: 'three_and_d', label: '3 & D Wing',
    blurb: 'Stands in the corner, makes it, and guards the other side\'s best wing.',
    positions: ['SG', 'SF', 'PF'],
    weights: {
      PerimeterDefense: 5, '3pShot': 5, Stealing: 3, '3pUsage': 3, JumpShot: 2,
      Passing: -3, Handling: -3, InsideScoring: -2,
    },
  },
  {
    id: 'point_forward', label: 'Point Forward',
    blurb: 'Big enough to post, skilled enough to run it. Brings it up himself.',
    positions: ['SF', 'PF'],
    weights: {
      Passing: 6, Handling: 4, DReb: 3, InsideScoring: 3, Strength: 2,
      '3pUsage': -2, Stealing: -1, Blocking: -1,
    },
  },
  {
    id: 'rim_protector', label: 'Rim Protector',
    blurb: 'Anchors the defense. Blocks, boards, post defense; the offense will come.',
    positions: ['PF', 'C'],
    weights: {
      Blocking: 6, PostDefense: 5, DReb: 4, Jumping: 3, Strength: 2,
      Handling: -4, '3pShot': -4, '3pUsage': -4, Passing: -2,
    },
  },
  {
    id: 'big_man', label: 'Big Man',
    blurb: 'Back to the basket. Scores and rebounds inside; do not ask him to switch.',
    positions: ['PF', 'C'],
    weights: {
      InsideScoring: 5, Strength: 5, OReb: 5, PostDefense: 4, DReb: 3,
      '3pShot': -5, '3pUsage': -5, Handling: -3, Quickness: -2,
    },
  },
  {
    id: 'stretch_big', label: 'Stretch Big',
    blurb: 'A big who stands at the arc and makes them come out to him.',
    positions: ['PF', 'C'],
    weights: {
      '3pShot': 5, '3pUsage': 4, JumpShot: 3, DReb: 3, InsideScoring: 2, Strength: 2,
      Quickness: -3, Stealing: -3, Handling: -2,
    },
  },
  {
    id: 'glue_guy', label: 'Glue Guy',
    blurb: 'No holes and no headline. Plays any position, never comes off the floor.',
    positions: ['PG', 'SG', 'SF', 'PF', 'C'],
    // A weighting cannot express "nothing stands out", so this one gets a small standing
    // bonus instead: on a sheet with no shape, everyone else scores about zero and he wins.
    bias: 0.09,
    weights: {
      PerimeterDefense: 3, Stamina: 3, DReb: 2, Passing: 2, FtShot: 2, Handling: 1,
    },
  },
];

export const CLASS_IDS = CLASSES.map((c) => c.id);

export function playerClass(id) { return CLASSES.find((c) => c.id === id) || null; }

/** Listed position nudges the label a little. It never rules anything out. */
export const POSITION_AFFINITY_BONUS = 0.06;
/** Softmax temperature for the confidence readout. Lower = more opinionated. */
export const CLASSIFY_TEMPERATURE = 0.18;
/** 3pUsage is on its own scale (a habit, 0-100); this is where "normal" sits. */
const USAGE_MIDPOINT = 35;
const USAGE_SCALE = 20;

/**
 * What this sheet looks like right now.
 *
 * @returns {{id, label, blurb, confidence, runnerUp, scores}}
 *   `confidence` is 0-100 and is a softmax over the class scores, so "Playmaker, 34%"
 *   honestly means the sheet is not yet committed to anything.
 *
 * Sets no caps. Gates nothing. It is a sentence on a page.
 */
export function classify(ratings, position) {
  const values = SKILL_RATINGS.map((r) => Number((ratings || {})[r]) || 0);
  const mean = values.reduce((a, b) => a + b, 0) / (values.length || 1);
  const variance = values.reduce((a, b) => a + (b - mean) * (b - mean), 0) / (values.length || 1);
  // a floor on the spread so an almost-flat sheet does not amplify one stray point
  const spread = Math.max(6, Math.sqrt(variance));

  const z = {};
  for (const r of SKILL_RATINGS) z[r] = ((Number((ratings || {})[r]) || 0) - mean) / spread;
  z['3pUsage'] = ((Number((ratings || {})['3pUsage']) || 0) - USAGE_MIDPOINT) / USAGE_SCALE;

  const scores = CLASSES.map((c) => {
    let sum = 0;
    let scale = 0;
    for (const [r, w] of Object.entries(c.weights)) {
      sum += w * (z[r] || 0);
      scale += Math.abs(w);
    }
    let score = scale ? sum / scale : 0;
    score += c.bias || 0;
    if (position) {
      score += c.positions.includes(position) ? POSITION_AFFINITY_BONUS : -POSITION_AFFINITY_BONUS;
    }
    return { id: c.id, label: c.label, blurb: c.blurb, score };
  }).sort((a, b) => b.score - a.score);

  const top = scores[0];
  const exps = scores.map((s) => Math.exp((s.score - top.score) / CLASSIFY_TEMPERATURE));
  const total = exps.reduce((a, b) => a + b, 0);

  return {
    id: top.id,
    label: top.label,
    blurb: top.blurb,
    confidence: Math.round((100 * exps[0]) / total),
    runnerUp: { id: scores[1].id, label: scores[1].label, blurb: scores[1].blurb },
    scores,
  };
}

/* ---------------------------------------------------------------------------------------
 * Seeded rolls - the PRNG both the sheet and the height model draw from
 * ------------------------------------------------------------------------------------ *
 * MIRRORED EXACTLY IN commissioner/growth.py, and pinned there by tests/test_rules.py.
 *
 * Two things are seeded, and they are seeded differently on purpose:
 *   * the STAT ROLL, off the identity a person typed (name, hometown, jersey), because the
 *     numbers have to exist before the database row does; and
 *   * the HEIGHT CURVE, off the character id, because the commissioner replays it every
 *     offseason long after creation.
 * Both are deterministic, so the same kid always comes out the same and resubmitting the
 * form never rerolls anything.
 * ------------------------------------------------------------------------------------ */

/** 32-bit FNV-1a over the UTF-16 code units of a string. */
export function fnv1a32(text) {
  const s = String(text === undefined || text === null ? '' : text);
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h = (h ^ s.charCodeAt(i)) >>> 0;
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

/** The growth PRNG's seed: the character's own id. */
export function heightSeed(characterId) { return fnv1a32(characterId); }

/** The separator between identity fields. A control character, so no name can contain it. */
export const SEED_SEPARATOR = String.fromCharCode(1);

/**
 * The stat roll's seed: who he is, not what you answered.
 *
 * Name, hometown and jersey number, trimmed, lowercased and joined. Two people who answer
 * the quiz identically still get different players, and the same person re-submitting the
 * same form gets the same one back - there is no reroll hiding in the submit button.
 */
export function identitySeed(identity) {
  const id = identity || {};
  const part = (v) => String(v === undefined || v === null ? '' : v).trim().toLowerCase();
  return fnv1a32([part(id.firstName), part(id.lastName), part(id.hometown), part(id.jersey)]
    .join(SEED_SEPARATOR));
}

/** mulberry32. Returns uint32 per call. Small, fast, and trivial to mirror. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0;
    t = (t ^ (t + Math.imul(t ^ (t >>> 7), t | 61))) >>> 0;
    return (t ^ (t >>> 14)) >>> 0;
  };
}

/**
 * One swing per band, each uniform over [-band, +band], drawn in order off one stream.
 * The order is fixed by the caller and must never change, or every existing character
 * would reroll into somebody else.
 *
 * @param {number} seed
 * @param {number[]} bands
 * @returns {number[]}
 */
export function rollSwings(seed, bands) {
  const rng = mulberry32(seed);
  return (bands || []).map((band) => {
    const b = Math.max(0, Math.floor(Number(band) || 0));
    return (rng() % (2 * b + 1)) - b;
  });
}

/* ---------------------------------------------------------------------------------------
 * Height growth
 * ------------------------------------------------------------------------------------ *
 * MIRRORED EXACTLY IN commissioner/growth.py. The commissioner writes the inches into
 * league.dat each offseason; this copy is here so the create page can show the range he is
 * heading for and the player page can show the growth so far. The two are pinned against
 * each other by tests/test_rules.py, which runs the same character ids through both and
 * asserts identical inch-by-inch curves. If you touch one, touch both.
 *
 * Design:
 *   * An expected adult height comes from the starting height at fourteen plus the
 *     `height_genes` trait. That number is PURE - no randomness - so it can be stored on
 *     the character at creation.
 *   * Each offseason he closes about a QUARTER of whatever gap is left, times a 0.75-1.25
 *     jitter. A quarter of a shrinking gap is the diminishing-returns curve, and it is
 *     deliberately gradual: he is still visibly growing at nineteen.
 *   * On top of that there is a CREEP that decays every year but never reaches zero. That
 *     is what makes the absence of a ceiling literal rather than rhetorical: there is no
 *     age and no height at which this model says "that is it".
 *   * And a freak roll, which can add a real spurt, with its chance HALVED for every full
 *     inch he is already past his expectation. A seven footer is possible out of the right
 *     answers and is never, ever guaranteed.
 *
 * All arithmetic is integer HUNDREDTHS OF AN INCH. No floats anywhere in this section:
 * that is what makes the JS and the Python agree bit for bit rather than nearly.
 * ------------------------------------------------------------------------------------ */

export const HEIGHT_GROWTH = {
  startAge: START_AGE,          // 14
  /** Offseasons the model runs: 14->15, 15->16, ... 22->23. */
  offseasons: 9,
  /** Hundredths of an inch a kid with height_genes = 0 still has coming. */
  gainBase: 100,
  /** Plus this many hundredths per ten points of genes: genes 100 -> +8.00 inches. */
  gainPerTenGenes: 70,
  /** Percent of the remaining gap closed each offseason, before the jitter. */
  closePct: 26,
  /** The jitter on the close and on the creep: x0.75 to x1.25. */
  jitterLo: 75,
  jitterSpan: 51,
  /** The creep, in hundredths: this much in the first offseason... */
  creepBase: 42,
  /** ...this much less each year after... */
  creepDecay: 5,
  /** ...and never, ever less than this. A twentieth of an inch a year, forever. */
  creepFloor: 5,
  /** How live the freak roll is: 100% at fourteen, falling, never quite nothing. */
  agePctBase: 100,
  agePctDecay: 12,
  agePctFloor: 5,
  /** Chance of a spurt, before the age damping and the halving-per-inch-over. */
  freakChance: 25,
  /** And its size in hundredths: 0.05 to 0.60 of an inch, before the age damping. */
  freakLo: 5,
  freakSpan: 56,
  /**
   * How far his real target can sit from the expectation, in hundredths: 1.50 inches
   * short of it to 2.50 inches past it, drawn once off his id.
   *
   * This is where almost all of the spread in adult height comes from, and it is why the
   * create page shows a BAND and not a number. A pure gap-closing model is
   * self-correcting - grow fast one year and the gap you have left is smaller, so you grow
   * less the next - which converges everybody on the same finish. Putting the uncertainty
   * in the target instead keeps the year-to-year curve smooth and makes the outcome
   * genuinely uncertain. The skew is deliberately upward: more room above than below.
   */
  targetDown: 150,
  targetUp: 250,
};

/** The last age the model has anything to say about. */
export const GROWTH_END_AGE = HEIGHT_GROWTH.startAge + HEIGHT_GROWTH.offseasons; // 23

function toInches(hundredths) { return Math.floor((hundredths + 50) / 100); }

function creepAt(i) {
  return Math.max(HEIGHT_GROWTH.creepFloor,
    HEIGHT_GROWTH.creepBase - i * HEIGHT_GROWTH.creepDecay);
}

function agePctAt(i) {
  return Math.max(HEIGHT_GROWTH.agePctFloor,
    HEIGHT_GROWTH.agePctBase - i * HEIGHT_GROWTH.agePctDecay);
}

/**
 * Where he is expected to finish, in hundredths. Pure: start height plus a gain read
 * straight off height_genes. Stored as `expected_adult_height` (whole inches).
 *
 * It is an expectation, not a cap. The creep and the freak roll both push past it.
 */
export function expectedAdultHundredths(startInches, heightGenes) {
  const start = Math.round(Number(startInches) || 0) * 100;
  const genes = Math.max(0, Math.min(100, Math.round(Number(heightGenes) || 0)));
  return start + HEIGHT_GROWTH.gainBase
    + Math.floor((genes * HEIGHT_GROWTH.gainPerTenGenes) / 10);
}

export function expectedAdultHeight(startInches, heightGenes) {
  return toInches(expectedAdultHundredths(startInches, heightGenes));
}

/**
 * The whole curve, one entry per age from 14 to 23 inclusive.
 * Deterministic given (characterId, startInches, heightGenes) and nothing else.
 *
 * @returns {Array<{age:number, hundredths:number, inches:number}>}
 */
export function growthCurve(characterId, startInches, heightGenes) {
  const rng = mulberry32(heightSeed(characterId));
  const expected = expectedAdultHundredths(startInches, heightGenes);
  let cur = Math.round(Number(startInches) || 0) * 100;

  // One draw, up front: where he is really heading. See `targetDown` / `targetUp`.
  const target = expected - HEIGHT_GROWTH.targetDown
    + (rng() % (HEIGHT_GROWTH.targetDown + HEIGHT_GROWTH.targetUp + 1));

  const out = [{ age: HEIGHT_GROWTH.startAge, hundredths: cur, inches: toInches(cur) }];
  for (let i = 0; i < HEIGHT_GROWTH.offseasons; i += 1) {
    // Four draws every offseason, in this order, whether or not each one is used, so the
    // stream stays aligned with the Python copy.
    const j1 = HEIGHT_GROWTH.jitterLo + (rng() % HEIGHT_GROWTH.jitterSpan);
    const j2 = HEIGHT_GROWTH.jitterLo + (rng() % HEIGHT_GROWTH.jitterSpan);
    const hit = rng() % 100;
    const roll = HEIGHT_GROWTH.freakLo + (rng() % HEIGHT_GROWTH.freakSpan);

    const gap = target - cur;
    const close = gap > 0 ? Math.floor((gap * HEIGHT_GROWTH.closePct * j1) / 10000) : 0;
    const creep = Math.floor((creepAt(i) * j2) / 100);

    const agePct = agePctAt(i);
    const over = Math.max(0, (cur + close + creep) - target);
    const halvings = Math.min(31, Math.floor(over / 100));
    const chance = Math.floor((HEIGHT_GROWTH.freakChance * agePct) / 100) >> halvings;
    const spurt = hit < chance ? Math.floor((roll * agePct) / 100) : 0;

    cur += close + creep + spurt;
    out.push({ age: HEIGHT_GROWTH.startAge + i + 1, hundredths: cur, inches: toInches(cur) });
  }
  return out;
}

/** His height at any age. Before 14 is the starting height; past 23 he is done growing. */
export function heightAtAge(characterId, startInches, heightGenes, age) {
  const curve = growthCurve(characterId, startInches, heightGenes);
  const a = Math.floor(Number(age));
  if (!Number.isFinite(a) || a <= HEIGHT_GROWTH.startAge) return curve[0].inches;
  if (a >= GROWTH_END_AGE) return curve[curve.length - 1].inches;
  return curve[a - HEIGHT_GROWTH.startAge].inches;
}

/**
 * Fixed sample ids, so the create page can show an honest range before the character has
 * an id. Deterministic, and mirrored in Python, so the two agree about the range too.
 */
export const OUTLOOK_SAMPLES = 64;

function sampleId(i) { return `cv-outlook-sample-${i}`; }

/**
 * The range he is heading for. This is what the create page shows, and it is the only
 * height figure shown before he is signed: a band, not a promise.
 *
 * @returns {{expected, low, high, ceiling, floor}} all in whole inches
 */
export function heightOutlook(startInches, heightGenes) {
  const finals = [];
  for (let i = 0; i < OUTLOOK_SAMPLES; i += 1) {
    const curve = growthCurve(sampleId(i), startInches, heightGenes);
    finals.push(curve[curve.length - 1].hundredths);
  }
  finals.sort((a, b) => a - b);
  const at = (q) => finals[Math.min(finals.length - 1, Math.floor((finals.length * q) / 100))];
  return {
    expected: expectedAdultHeight(startInches, heightGenes),
    low: toInches(at(20)),
    high: toInches(at(80)),
    ceiling: toInches(finals[finals.length - 1]),
    floor: toInches(finals[0]),
  };
}

/* ---------------------------------------------------------------------------------------
 * Heights at creation
 * ------------------------------------------------------------------------------------ *
 * These are heights AT FOURTEEN, not adult heights, because the growth model above now
 * carries him the rest of the way. ADULT_HEIGHT_RANGES is what a grown one looks like, for
 * the readout under the slider.
 * ------------------------------------------------------------------------------------ */

export const HEIGHT_RANGES = {
  PG: [62, 71], SG: [64, 74], SF: [66, 76], PF: [68, 78], C: [70, 80],
};

export const HEIGHT_DEFAULTS = { PG: 67, SG: 70, SF: 72, PF: 74, C: 76 };

export const ADULT_HEIGHT_RANGES = {
  PG: [68, 76], SG: [71, 79], SF: [74, 82], PF: [76, 85], C: [78, 88],
};

/* ---------------------------------------------------------------------------------------
 * Derived helpers
 * ------------------------------------------------------------------------------------ */

export function hasPotential(rating) {
  return POTENTIAL_RATINGS.includes(rating);
}

export function isLocked(rating) {
  return LOCKED_RATINGS.includes(rating);
}

/**
 * The athletic ceilings.
 *
 * Six ratings have no potential in FBPB3 - Quickness, Strength, Jumping, Stamina, 3pUsage and
 * the locked Fouling - so nothing held them back and every surplus point after the skilled
 * twelve topped out flowed into them. A character who ran out of skill ceiling in college
 * finished with Quickness 77 and climbing, which is not a build, it is a leak.
 *
 * They get a ceiling of their own, from the body the quiz described: the frame trait for
 * strength, explosiveness for quickness and jumping, motor for stamina. Wide enough that
 * nobody bumps it early, low enough that it is a real limit.
 */
export const ATHLETIC_BASE = 55;
export const ATHLETIC_SWING = 30;
export const ATHLETIC_TRAIT = {
  Quickness: 'explosiveness', Jumping: 'explosiveness', Strength: 'frame',
  Stamina: 'motor', '3pUsage': 'confidence', Fouling: 'discipline',
};

export function athleticCeiling(rating, traits) {
  const name = ATHLETIC_TRAIT[rating];
  if (!name) return RATING_MAX;
  const raw = Number((traits || {})[name]);
  const t = Number.isFinite(raw) ? raw : TRAIT_BASE;
  return Math.max(35, Math.min(RATING_MAX,
    Math.round(ATHLETIC_BASE + (ATHLETIC_SWING * (t - TRAIT_BASE)) / 50)));
}

/**
 * The ceiling a rating can currently be raised to: its potential for the twelve that have one,
 * its athletic ceiling for the six that do not.
 */
export function ratingCeiling(rating, potentials, traits) {
  if (!hasPotential(rating)) return athleticCeiling(rating, traits);
  const pot = Number((potentials || {})[rating]);
  return Math.min(RATING_MAX, Number.isFinite(pot) ? pot : RATING_MAX);
}

/** The ceiling a potential can be raised to: 100. Potentials cost double; that is the limit. */
export function potentialCeiling() {
  return RATING_MAX;
}

/* -------------------------------------------------------------------------------------
 * Moving up a level
 *
 * The same skill is worth less against bigger, older, better opposition, so a promotion
 * carries ratings across at less than face value - and going up early costs more. These
 * must stay in step with commissioner/offseason.py, which does the actual conversion;
 * this copy exists so the site can tell somebody what declaring will cost him BEFORE he
 * clicks the button that cannot be undone.
 *
 * Potentials are never converted. The ceiling is who he can still become, and leaving
 * early must not close it: the points are earnable again, the years are not.
 * ---------------------------------------------------------------------------------- */
export const LEVEL_CONVERSION = { college: 0.97, pro: 0.94 };
export const EARLY_PENALTY_PER_YEAR = 0.07;
export const EARLY_PENALTY_MAX = 0.24;
export const COLLEGE_MAX_YEARS = 4;
export const CONVERSION_FLOOR = 8;

export function conversionFor(toLeague, collegeYearsUsed = COLLEGE_MAX_YEARS) {
  const base = LEVEL_CONVERSION[toLeague] ?? 1;
  const early = toLeague === 'pro'
    ? Math.max(0, COLLEGE_MAX_YEARS - Number(collegeYearsUsed || 0)) : 0;
  const penalty = Math.min(EARLY_PENALTY_MAX, early * EARLY_PENALTY_PER_YEAR);
  return { factor: Number((base - penalty).toFixed(4)), base, earlyYears: early, penalty };
}

/** What a sheet looks like on the other side of the move. */
export function convertSheet(ratings, factor) {
  const out = {};
  for (const [k, v] of Object.entries(ratings || {})) {
    out[k] = v <= CONVERSION_FLOOR ? v : Math.max(CONVERSION_FLOOR, Math.round(v * factor));
  }
  return out;
}

/** One honest sentence about what declaring now would do to him. */
export function declareWarning(character, collegeYearsUsed) {
  const c = conversionFor('pro', collegeYearsUsed);
  const pct = Math.round(c.factor * 100);
  const ratings = character.ratings || {};
  const before = Object.values(ratings).reduce((a, b) => a + Number(b || 0), 0);
  const after = Object.values(convertSheet(ratings, c.factor))
    .reduce((a, b) => a + Number(b || 0), 0);
  const lost = Math.max(0, before - after);
  const years = c.earlyYears;
  return {
    ...c, lost,
    headline: years
      ? `Declaring ${years} year${years === 1 ? '' : 's'} early: he carries ${pct}% of his `
        + `ratings into the pros, about ${lost} rating points gone.`
      : `He has used his eligibility, so he carries ${pct}% of his ratings into the pros.`,
    detail: years
      ? 'His ceilings do not move, so every point is earnable again - and he starts collecting '
        + 'professional points three seasons sooner. Staying would pay him a development bonus '
        + 'each year instead. Neither is wrong.'
      : 'His ceilings do not move.',
  };
}

/**
 * Total cost of moving a sheet from `start` to where it is now, with the character's bias
 * applied per rating. Not used at creation any more (creation spends nothing) - me.html
 * uses it to price a queued batch.
 */
export function sheetCost(start, ratings, potentials, bias) {
  let total = 0;
  for (const r of RATINGS) {
    const from = Number((start.ratings || {})[r]);
    const to = Number((ratings || {})[r]);
    if (Number.isFinite(from) && Number.isFinite(to) && to > from) {
      total += biasedUpgradeCost(from, to - from, 'rating', biasFor(bias, r));
    }
  }
  for (const r of POTENTIAL_RATINGS) {
    const from = Number((start.potentials || {})[r]);
    const to = Number((potentials || {})[r]);
    if (Number.isFinite(from) && Number.isFinite(to) && to > from) {
      total += biasedUpgradeCost(from, to - from, 'potential', biasFor(bias, r));
    }
  }
  return total;
}

/* ---------------------------------------------------------------------------------------
 * The whole derivation, in one call
 * ------------------------------------------------------------------------------------ */

/**
 * Everything create.html shows on the review screen, and everything it inserts.
 *
 * The identity fields are part of the input because they seed the roll: change the name
 * and you are making a different kid, who rolls differently. That is the only way to
 * reroll, it costs you the name you wanted, and you cannot see what you are rolling into
 * while you do it.
 *
 * @param {object} input {firstName, lastName, hometown, jersey, position, heightInches,
 *                        build, answers, goal, summer}
 * @returns {{traits, ratings, potentials, ranges, potentialRanges, seed, bias, klass,
 *            outlook, expectedAdultHeight, weightLbs}}
 */
export function deriveCharacter(input) {
  const position = POSITIONS.includes(input.position) ? input.position : 'SG';
  const traits = traitsFromQuiz(input.answers, {
    build: input.build, goal: input.goal, summer: input.summer,
  });
  const seed = identitySeed(input);
  const sheet = startingSheet(position, traits, { summer: input.summer, seed });
  const heightInches = Number(input.heightInches) || HEIGHT_DEFAULTS[position];
  return {
    traits,
    seed,
    ratings: sheet.ratings,
    potentials: sheet.potentials,
    ranges: sheet.ranges,
    potentialRanges: sheet.potentialRanges,
    bands: sheet.bands,
    bias: growthBias(traits, input.goal),
    klass: classify(sheet.ratings, position),
    outlook: heightOutlook(heightInches, traits.height_genes),
    expectedAdultHeight: expectedAdultHeight(heightInches, traits.height_genes),
    // What he was SET to, falling back to the usual weight for that height and build. Derived
    // was the only option while nothing stored a weight; now that the builder offers a slider,
    // recomputing here would quietly discard whatever the person chose with it and show them a
    // review card that disagreed with the control they had just dragged.
    weightLbs: Number(input.weightLbs) || buildWeight(heightInches, input.build),
  };
}

/* ---------------------------------------------------------------------------------------
 * Scouting language - what a rating looks like before he is signed
 * ------------------------------------------------------------------------------------ *
 * Numbers are not shown on the create page. A scout's phrase and a shaded band are, which
 * is roughly what you would actually get if you asked somebody about a fourteen year old.
 * ------------------------------------------------------------------------------------ */

export const SCOUTING_LADDER = [
  { upTo: 7, word: 'nothing there yet' },
  { upTo: 11, word: 'raw' },
  { upTo: 15, word: 'about his age' },
  { upTo: 19, word: 'coming along' },
  { upTo: 23, word: 'ahead of his age' },
  { upTo: 27, word: 'advanced' },
  { upTo: Infinity, word: 'already a weapon' },
];

export const USAGE_LADDER = [
  { upTo: 12, word: 'never shoots it' },
  { upTo: 25, word: 'rarely' },
  { upTo: 40, word: 'when it comes to him' },
  { upTo: 55, word: 'often' },
  { upTo: 70, word: 'a lot' },
  { upTo: Infinity, word: 'constantly' },
];

export const FOULING_LADDER = [
  { upTo: 15, word: 'never fouls' },
  { upTo: 25, word: 'clean' },
  { upTo: 35, word: 'normal' },
  { upTo: 50, word: 'handsy' },
  { upTo: Infinity, word: 'in foul trouble a lot' },
];

function ladderWord(ladder, value) {
  for (const rung of ladder) if (value <= rung.upTo) return rung.word;
  return ladder[ladder.length - 1].word;
}

/**
 * One phrase for one rating, from the MIDDLE of its range - never from the rolled value,
 * so the phrase does not leak the roll.
 */
export function scoutingWord(rating, range) {
  const mid = Math.round(((range || [0, 0])[0] + (range || [0, 0])[1]) / 2);
  if (rating === '3pUsage') return ladderWord(USAGE_LADDER, mid);
  if (rating === 'Fouling') return ladderWord(FOULING_LADDER, mid);
  return ladderWord(SCOUTING_LADDER, mid);
}

/** "could go either way" / "pretty clear" - how wide the band on that rating is. */
export function certaintyWord(band) {
  const b = Number(band) || 0;
  if (b <= 2) return 'no doubt about it';
  if (b <= 3) return 'fairly clear';
  if (b <= 5) return 'hard to say';
  return 'anybody’s guess';
}

/** Where a starting rating sits on the bar. Starting sheets live in 0-40, not 0-100. */
export const SCOUT_SCALE = 40;

export function scoutScale(rating) {
  return TENDENCY_RATINGS.includes(rating) ? RATING_MAX : SCOUT_SCALE;
}

/* ---------------------------------------------------------------------------------------
 * Validation
 * ------------------------------------------------------------------------------------ */

/**
 * Everything create.html has to be sure of before it inserts a row.
 *
 * Note on the jersey number: a character has no team at creation - the commissioner claims
 * a dormant reserve slot for him at the next sim - so uniqueness inside a team cannot be
 * checked here and is not checked here. What is stored is a *preference*
 * (`jersey_preference`). The commissioner resolves collisions when it puts him on a
 * roster, and the number he actually wears is whatever ends up in the save file.
 *
 * Returns { ok, errors: [string] }.
 */
export function validateBuild(build) {
  const errors = [];
  const b = build || {};

  if (!POSITIONS.includes(b.position)) errors.push('Pick a position.');

  const first = String(b.firstName || '').trim();
  const last = String(b.lastName || '').trim();
  if (!first) errors.push('First name is required.');
  if (!last) errors.push('Last name is required.');
  if (first.length > MAX_NAME_LENGTH) errors.push(`First name is longer than ${MAX_NAME_LENGTH} characters.`);
  if (last.length > MAX_NAME_LENGTH) errors.push(`Last name is longer than ${MAX_NAME_LENGTH} characters.`);

  if (POSITIONS.includes(b.position)) {
    const [hLo, hHi] = HEIGHT_RANGES[b.position];
    const height = Number(b.heightInches);
    if (!Number.isFinite(height) || height < hLo || height > hHi) {
      errors.push(`A fourteen year old ${POSITION_LABELS[b.position]} is `
        + `${formatHeight(hLo)} to ${formatHeight(hHi)}.`);
    }
  }

  // Number(null) and Number('') are both 0, which would validate a blank field as jersey 0 -
  // and worse, identitySeed would hash '' while the stored row says 0, so the character could
  // never re-derive its own roll. Reject empty explicitly before coercing.
  const jerseyRaw = b.jersey;
  const jerseyBlank = jerseyRaw === null || jerseyRaw === undefined || String(jerseyRaw).trim() === '';
  const jersey = jerseyBlank ? NaN : Number(jerseyRaw);
  if (!Number.isInteger(jersey) || jersey < JERSEY_MIN || jersey > JERSEY_MAX) {
    errors.push(`Pick a jersey number from ${JERSEY_MIN} to ${JERSEY_MAX}.`);
  }

  const hometown = String(b.hometown || '').trim();
  if (!hometown) errors.push('Where is he from?');
  if (hometown.length > MAX_HOMETOWN_LENGTH) {
    errors.push(`Hometown is longer than ${MAX_HOMETOWN_LENGTH} characters.`);
  }

  if (!BUILD_IDS.includes(b.build)) errors.push('Pick a build.');

  const progress = quizProgress(b.answers);
  if (!progress.complete) {
    errors.push(`${progress.total - progress.done} question`
      + `${progress.total - progress.done === 1 ? '' : 's'} still unanswered.`);
  }
  if (!careerGoal(b.goal)) errors.push('Pick what he wants out of this.');
  if (!summerWork(b.summer)) errors.push('Pick what he did over the summer.');

  // The sheet is derived, never typed, so the only thing to check is that it still is.
  if (!errors.length) {
    const derived = deriveCharacter(b);
    for (const r of RATINGS) {
      if (Number((b.ratings || {})[r]) !== derived.ratings[r]) {
        errors.push('The stat sheet does not match the answers. Reload and try again.');
        break;
      }
    }
    if (!errors.length) {
      for (const r of POTENTIAL_RATINGS) {
        if (Number((b.potentials || {})[r]) !== derived.potentials[r]) {
          errors.push('The potentials do not match the answers. Reload and try again.');
          break;
        }
      }
    }
  }

  return { ok: errors.length === 0, errors };
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

  const from = Number((kind === 'potential' ? character.potentials : character.ratings)[rating]) || 0;
  const ceiling = kind === 'potential'
    ? potentialCeiling()
    : ratingCeiling(rating, character.potentials);
  if (from + steps > ceiling) {
    errors.push(`That would reach ${from + steps}; the ceiling is ${ceiling}.`);
  }
  const cost = biasedUpgradeCost(from, steps, kind, biasFor(character.growth_bias, rating));
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
