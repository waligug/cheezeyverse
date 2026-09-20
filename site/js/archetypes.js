/* "You play like Dwight Howard" - the NBA player a character most resembles.
 *
 * WHY A SHAPE AND NOT THE RATINGS THEMSELVES. A fourteen-year-old is rated in the twenties and
 * an All-Star in the nineties, so nearest-neighbour on raw numbers matches every character to
 * whoever is worst in the league - which is both useless and a bit insulting. Each player is
 * therefore centred on his OWN mean and scaled by his OWN spread: what he is good at relative to
 * himself. Cousins reads "scores inside, cannot shoot, rebounds" at any level, and so does a
 * prep kid built the same way.
 *
 * The comparison is cosine similarity on that shape, which ignores magnitude entirely - two
 * players with the same strengths in the same proportions score 1.0 however good either is.
 *
 * This mirrors tools/nba_archetypes.py exactly, field order included, because the JSON it writes
 * carries shapes computed there and a shape computed differently here would be compared against
 * them as though it were the same thing. The two implementations are checked against each other
 * in tests/test_archetypes.py rather than trusted to stay in step.
 *
 * Stamina and Fouling are deliberately absent: Stamina was set administratively to a flat 70 for
 * every character, so it carries no information about anybody, and Fouling is a habit rather than
 * an ability.
 */

/** The 16 rating fields the shape is built from, in the order the JSON's vectors use. */
export const SHAPE_FIELDS = [
  'InsideScoring', 'JumpShot', 'FtShot', '3pShot', '3pUsage', 'Handling', 'Passing',
  'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking', 'OReb', 'DReb',
  'Strength', 'Quickness', 'Jumping',
];

let CACHE = null;

/** The archetype file, fetched once. Null when it is missing - the page carries on without it. */
export function loadArchetypes(path = 'data/nba-archetypes.json') {
  if (!CACHE) {
    CACHE = fetch(path)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => (d && Array.isArray(d.players) ? d : null))
      .catch(() => null);
  }
  return CACHE;
}

/**
 * A ratings object as a shape vector, or null when he has no spread at all.
 *
 * Rounded to 4 decimals to match the python, so a value that lands exactly between two
 * candidates breaks the same way in both.
 */
export function shapeOf(ratings) {
  const values = SHAPE_FIELDS.map((k) => Number((ratings || {})[k]) || 0);
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const spread = Math.sqrt(
    values.reduce((a, v) => a + (v - mean) ** 2, 0) / values.length);
  if (spread < 1e-6) return null;       // every rating identical: he has no shape to compare
  return values.map((v) => Math.round(((v - mean) / spread) * 10000) / 10000);
}

/** Cosine similarity, 1.0 being the same style of player. */
export function similarity(a, b) {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i += 1) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  return (na && nb) ? dot / (Math.sqrt(na) * Math.sqrt(nb)) : 0;
}

/**
 * The `count` NBA players a ratings sheet most resembles, best first.
 *
 * Ties are broken by name, so the same sheet always produces the same answer - two players can
 * sit at identical similarity to four decimal places, and "you play like" changing between two
 * reloads of the same character would read as the page being broken.
 */
export function matchArchetypes(ratings, data, count = 3) {
  if (!data || !Array.isArray(data.players)) return [];
  const mine = shapeOf(ratings);
  if (!mine) return [];
  return data.players
    .filter((p) => Array.isArray(p.shape) && p.shape.length === mine.length)
    .map((p) => ({ ...p, score: similarity(mine, p.shape) }))
    .sort((x, y) => y.score - x.score || x.name.localeCompare(y.name))
    .slice(0, count);
}

/** "scores inside, rebounds, blocks shots" - what the match is known for, from his top ratings. */
export function archetypeStrengths(player) {
  return (player.top || []).map(([field]) => field);
}
