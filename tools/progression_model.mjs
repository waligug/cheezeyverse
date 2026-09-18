/**
 * What eight seasons of points actually buy.
 *
 *   cd site/js && node ../../tools/progression_model.mjs
 *
 * Re-run this after changing the cost curve, the points per week, the offseason lump sum or the
 * potential constants. Balance here is not a feeling: the character has to leave prep better than
 * the prep fillers (8-38) and arrive in the pros somewhere inside the pro band (28-62), or the
 * whole arc is either a grind or a walkover.
 */
import * as R from '../site/js/rules.js';
const answers = {};
for (const q of R.QUIZ) answers[q.id] = q.answers[0].id;
const d = R.deriveCharacter({ firstName: 'Wheel', lastName: 'Gouda', hometown: 'Gouda',
  jersey: 7, position: 'SG', build: 'lean', heightInches: 70, answers });
const CORE = ['JumpShot','3pShot','Handling','Passing','PerimeterDefense','Quickness'];

let r = Object.fromEntries(CORE.map(k => [k, d.ratings[k]]));
const cap = Object.fromEntries(CORE.map(k => [k, R.ratingCeiling(k, d.potentials, d.traits)]));
const PER_SEASON = 26 + 15;   // weeks + offseason lump
console.log(`start: ${CORE.map(k => `${k} ${r[k]}`).join(', ')}`);
console.log(`ceilings: ${CORE.map(k => `${cap[k]}`).join(', ')}`);
let total = 0;
for (let season = 1; season <= 8; season++) {
  let budget = PER_SEASON; total += PER_SEASON;
  let moved = true;
  while (moved) {                       // spend evenly on whatever is furthest below its ceiling
    moved = false;
    const order = [...CORE].sort((a, b) => (cap[a] - r[a]) - (cap[b] - r[b])).reverse();
    for (const k of order) {
      if (r[k] >= cap[k]) continue;
      const c = R.stepCost(r[k]);
      if (c <= budget) { budget -= c; r[k] += 1; moved = true; }
    }
  }
  const level = season <= 4 ? 'prep' : season <= 8 ? 'college' : 'pro';
  console.log(`after season ${season} (${level}, ${total} pts): ` +
    CORE.map(k => `${r[k]}`).join('/') + `  avg ${Math.round(CORE.reduce((s,k)=>s+r[k],0)/CORE.length)}`);
}
console.log('\nfiller bands for comparison: prep 8-38, college 18-52, pro 28-62');
