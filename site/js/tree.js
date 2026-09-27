/* The skill tree on the site: what a step costs, how far it may go, and the tree itself.

   supabase/skill_tree.sql is the authority - it prices every request and refuses what the
   rules refuse, whatever this page believes. Everything here mirrors it so the page offers only
   what the database will accept and quotes the price it will charge:

     prices     settings.price_bands with the tree on, the old 1/2/3/5 curve with it off
     ceilings   triple price with the tree on (double off), and at most `ceiling_room` above
                the rating
     stage caps nothing bought past 70 in prep or 85 in college
     bias       traits, the goal and the tree's discounts, clamped to 50..200 (85..115 off)
     nodes      stage, the node before it, points spent in the branch since the tree opened,
                one Signature per player

   A NEW FILE ON PURPOSE. ui.js, rules.js and supabase.js are each cached for ten minutes by
   GitHub Pages, and a page importing a NEW named export from a stale copy does not load at all.
   This module only imports names those files have always exported. */

import { client } from './supabase.js';
import { RATING_LABELS } from './rules.js';
import { el, clear } from './ui.js';

export const BRANCH_ORDER = ['Scoring', 'Playmaking', 'Defense', 'Rebounding', 'Athletic'];
export const BRANCHES = {
  Scoring: ['InsideScoring', 'JumpShot', 'FtShot', '3pShot'],
  Playmaking: ['Handling', 'Passing'],
  Defense: ['PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking'],
  Rebounding: ['OReb', 'DReb'],
  Athletic: ['Quickness', 'Jumping', 'Strength', 'Stamina'],
};
const BRANCH_OF = Object.fromEntries(Object.entries(BRANCHES).flatMap(([b, rs]) => rs.map((r) => [r, b])));
BRANCH_OF['3pUsage'] = 'Scoring';

const OLD_BANDS = [[50, 1], [70, 2], [85, 3], [9999, 5]];
const DEFAULT_BANDS = [[50, 1], [60, 2], [70, 3], [80, 5], [85, 8], [90, 12], [95, 18], [100, 25], [999, 35]];
const STAGE_RANK = { prep: 1, college: 2, pro: 3 };
const TIER_WORDS = { 1: 'Tier 1', 2: 'Tier 2', 3: 'Tier 3', 4: 'Signature' };

const truthy = (v) => (typeof v === 'string' ? ['true', '1', 'yes', 'on'].includes(v.trim().toLowerCase()) : Boolean(v));
const int = (v, d) => (Number.isFinite(Number(v)) && v !== null && v !== '' ? Number(v) : d);

/** The tree's settings, read once per page load from the settings the page already fetched. */
export function treeRules(cfg) {
  const s = cfg || {};
  const on = truthy(s.skill_tree_enabled);
  let bands = s.price_bands;
  if (typeof bands === 'string') { try { bands = JSON.parse(bands); } catch (err) { bands = null; } }
  return {
    on,
    bands: on ? (Array.isArray(bands) && bands.length ? bands : DEFAULT_BANDS) : OLD_BANDS,
    potMult: on ? int(s.potential_multiplier, 3) : 2,
    room: on ? int(s.ceiling_room, 10) : null,
    caps: on ? { prep: int(s.stage_cap_prep, 70), college: int(s.stage_cap_college, 85) } : {},
    biasMin: on ? int(s.bias_min, 50) : 85,
    biasMax: on ? int(s.bias_max, 200) : 115,
    breakerSize: int(s.breaker_size, 3),
    breakerMax: int(s.breaker_max_per_rating, 3),
    openedAt: s.tree_opened_at || null,
  };
}

/** The single step that leaves value v - cv_step_cost. */
export function stepCost(rules, v) {
  for (const [below, cost] of rules.bands) if (v < below) return cost;
  return rules.bands[rules.bands.length - 1][1];
}

/** from -> from + steps, each step at the band it leaves; a ceiling step multiplied - cv_upgrade_cost. */
export function upgradeCost(rules, from, steps, kind = 'rating') {
  let total = 0;
  for (let i = 0; i < steps; i += 1) total += stepCost(rules, Number(from) + i);
  return kind === 'potential' ? total * rules.potMult : total;
}

/** The guard's growth-bias arithmetic: greatest(1, round(cost * clamped / 100)). */
export function biasedCost(rules, from, steps, kind, biasPct) {
  const b = Math.min(rules.biasMax, Math.max(rules.biasMin, Number.isFinite(Number(biasPct)) ? Number(biasPct) : 100));
  return Math.max(1, Math.round((upgradeCost(rules, from, steps, kind) * b) / 100));
}

/** The highest a rating may be BOUGHT to in this league: 70 in prep, 85 in college. */
export function stageCap(rules, league) {
  return (rules.caps && rules.caps[league]) || 150;
}

/** One line of prices for the "How pricing works" fold. */
export function describePrices(rules) {
  let lo = 0;
  const parts = rules.bands.map(([below, cost]) => {
    const words = below >= 999 ? `${lo} and up` : lo === 0 ? `under ${below}` : `${lo}-${below - 1}`;
    lo = below;
    return `${words}: ${cost}`;
  });
  return parts.join(' · ');
}

/* ---------------------------------------------------------------------------- the nodes */

let NODES = null;

/** Every node, from public.tree_nodes. [] if the table is not there yet. */
export async function treeNodes() {
  if (NODES) return NODES;
  try {
    const { data, error } = await client().from('tree_nodes').select('*').eq('active', true)
      .order('branch').order('sort');
    NODES = error ? [] : (data || []);
  } catch (err) {
    NODES = [];
  }
  return NODES;
}

/** Points he has put into a branch's ratings and ceilings since the tree opened - cv_branch_spent. */
export function branchSpent(requests, branch, openedAt) {
  const since = openedAt ? Date.parse(openedAt) : 0;
  return (requests || [])
    .filter((r) => (r.kind === 'rating' || r.kind === 'potential')
      && ['pending', 'approved', 'applied'].includes(r.status)
      && BRANCH_OF[r.rating] === branch
      && (!since || Date.parse(r.requested_at) >= since))
    .reduce((sum, r) => sum + Number(r.cost || 0), 0);
}

/** Where one node stands for one character, and why, in the guard's own order. */
export function nodeState(character, node, requests, rules, nodes) {
  const owned = new Set(character.nodes || []);
  const queued = new Set((requests || []).filter((r) => r.kind === 'node'
    && (r.status === 'pending' || r.status === 'approved')).map((r) => r.rating));
  if (owned.has(node.id)) return { state: 'owned', why: 'His.' };
  if (queued.has(node.id)) return { state: 'queued', why: 'Asked for. It lands at the next sim.' };
  if (!rules.on) return { state: 'locked', why: 'The tree is not open yet.' };
  const reasons = [];
  if ((STAGE_RANK[character.league] || 0) < (STAGE_RANK[node.stage] || 0)) {
    reasons.push(`Opens in ${node.stage}.`);
  }
  for (const req of node.requires || []) {
    if (!owned.has(req) && !queued.has(req)) {
      const n = (nodes || []).find((x) => x.id === req);
      reasons.push(`Needs ${n ? n.name : req} first.`);
    }
  }
  if (node.group_key) {
    const other = (nodes || []).find((x) => x.group_key === node.group_key && x.id !== node.id
      && (owned.has(x.id) || queued.has(x.id)));
    if (other) reasons.push(`He already has ${other.name}. One Signature per player.`);
  }
  const spent = branchSpent(requests, node.branch, rules.openedAt);
  if (spent < Number(node.min_spent || 0)) {
    reasons.push(`Needs ${node.min_spent} points spent on ${node.branch} (${spent} so far).`);
  }
  for (const [k, v] of Object.entries(node.min_ratings || {})) {
    if (Number((character.ratings || {})[k] || 0) < Number(v)) reasons.push(`Needs ${RATING_LABELS[k] || k} ${v}.`);
  }
  return reasons.length ? { state: 'locked', why: reasons.join(' ') } : { state: 'open', why: '' };
}

/** "+3 ceiling on Inside Scoring, Jump Shot..." - what a node does, from its effects. */
export function describeEffects(fx) {
  const out = [];
  const list = (obj) => Object.entries(obj || {});
  const group = (entries, words) => {
    const byAmount = {};
    for (const [k, v] of entries) (byAmount[v] = byAmount[v] || []).push(RATING_LABELS[k] || k);
    for (const [v, names] of Object.entries(byAmount)) out.push(words(Number(v), names.join(', ')));
  };
  group(list(fx.ratings), (v, names) => `${v > 0 ? '+' : ''}${v} ${names}`);
  group(list(fx.potentials), (v, names) => `+${v} ceiling: ${names}`);
  group(list(fx.tendency), (v, names) => `${v > 0 ? '+' : ''}${v} ${names}`);
  if (list(fx.bias).length) out.push(`${Math.abs(list(fx.bias)[0][1])}% cheaper to train`);
  if (list(fx.shield).length) out.push('ages half as fast');
  return out;
}

/**
 * The Tree tab. `onUnlock(node)` and `onBreaker(rating)` file the requests; the page reloads.
 */
export function renderTree(container, { character, nodes, requests, rules, free, onUnlock, onBreaker }) {
  clear(container);
  if (!rules.on) {
    container.append(el('p', { class: 'cv-muted' }, 'The skill tree opens soon.'));
    return;
  }
  if (!nodes.length) {
    container.append(el('p', { class: 'cv-muted' }, 'The tree did not load. Try again in a minute.'));
    return;
  }
  container.append(el('p', { class: 'cv-hint' },
    'Five branches. Each tier opens by level and by what he has put into that branch\'s ratings. '
    + 'The Signature at the top is one per player, so choose it like a career.'));

  const breakers = Number(character.cap_breakers || 0);
  const queuedBreakers = (requests || []).filter((r) => r.kind === 'breaker'
    && (r.status === 'pending' || r.status === 'approved'));
  const left = breakers - queuedBreakers.length;
  if (breakers > 0 || queuedBreakers.length) {
    const used = {};
    for (const r of requests || []) {
      if (r.kind === 'breaker' && r.status !== 'rejected') used[r.rating] = (used[r.rating] || 0) + 1;
    }
    const choices = Object.keys(RATING_LABELS).filter((r) => r in (character.potentials || {})
      && (used[r] || 0) < rules.breakerMax);
    const pick = el('select', { class: 'cv-select', 'aria-label': 'Which ceiling' },
      ...choices.map((r) => el('option', { value: r }, `${RATING_LABELS[r]} (ceiling ${character.potentials[r]})`)));
    container.append(el('div', { class: 'cv-tree-breakers' },
      el('div', {}, el('b', {}, `${left} Cap Breaker${left === 1 ? '' : 's'}`),
        el('span', { class: 'cv-muted' }, ` · +${rules.breakerSize} on one ceiling each, at most ${rules.breakerMax} on any rating`)),
      left > 0 && choices.length ? el('div', { class: 'cv-actions' }, pick,
        el('button', {
          class: 'cv-btn cv-small', type: 'button',
          onclick: (e) => { e.currentTarget.disabled = true; onBreaker(pick.value); },
        }, 'Use one')) : null));
  }

  const grid = el('div', { class: 'cv-tree' });
  for (const branch of BRANCH_ORDER) {
    const mine = nodes.filter((n) => n.branch === branch).sort((a, b) => a.tier - b.tier || a.sort - b.sort);
    if (!mine.length) continue;
    const spent = branchSpent(requests, branch, rules.openedAt);
    const col = el('section', { class: 'cv-tree-branch' },
      el('h3', {}, branch),
      el('p', { class: 'cv-muted cv-tree-spent' }, `${spent} point${spent === 1 ? '' : 's'} put into ${branch.toLowerCase()} so far`));
    for (const node of mine) {
      const st = nodeState(character, node, requests, rules, nodes);
      const affordable = Number(node.cost) <= free;
      const card = el('article', { class: `cv-tree-node is-${st.state}${node.tier === 4 ? ' is-signature' : ''}` },
        el('div', { class: 'cv-tree-top' },
          el('span', { class: 'cv-tree-tier' }, TIER_WORDS[node.tier] || `Tier ${node.tier}`),
          el('b', { class: 'cv-tree-cost' }, `${node.cost}`)),
        el('h4', {}, node.name),
        el('p', { class: 'cv-tree-blurb' }, node.blurb || describeEffects(node.effects || {}).join(' · ')),
        st.state === 'open'
          ? el('button', {
            class: 'cv-btn cv-small', type: 'button', disabled: !affordable,
            title: affordable ? `Unlock for ${node.cost} points` : `Costs ${node.cost}; ${free} free`,
            onclick: (e) => { e.currentTarget.disabled = true; onUnlock(node); },
          }, affordable ? `Unlock · ${node.cost}` : `Needs ${node.cost}`)
          : el('p', { class: `cv-tree-state is-${st.state}` },
            st.state === 'owned' ? '✓ Unlocked' : st.state === 'queued' ? 'Asked for' : st.why));
      col.append(card);
    }
    grid.append(col);
  }
  container.append(grid);
}

/**
 * The order to send staged changes in, so every request is legal WHEN it arrives.
 *
 * A rating may not pass its ceiling, and (tree on) a ceiling may not be bought more than
 * `room` over the rating. Staging both on one rating can need them interleaved - raise the
 * ceiling a little, the rating up to it, the ceiling again. Returns [{rating, delta, kind}].
 */
export function sendOrder(rules, base, draft, ratings, potentialRatings) {
  const cur = { r: { ...base.ratings }, p: { ...base.potentials } };
  const want = { r: {}, p: {} };
  for (const k of ratings) {
    const d = Number(draft.ratings[k] ?? cur.r[k]) - Number(cur.r[k] ?? 0);
    if (d > 0) want.r[k] = d;
  }
  for (const k of potentialRatings) {
    const d = Number(draft.potentials[k] ?? cur.p[k]) - Number(cur.p[k] ?? 0);
    if (d > 0) want.p[k] = d;
  }
  const out = [];
  for (let guard = 0; guard < 400; guard += 1) {
    let moved = false;
    for (const k of Object.keys(want.p)) {
      if (!want.p[k]) continue;
      const roomLeft = rules.room === null ? want.p[k]
        : Number(cur.r[k] ?? 0) + rules.room - Number(cur.p[k] ?? 0);
      const d = Math.min(want.p[k], Math.max(0, roomLeft));
      if (d > 0) {
        out.push({ rating: k, delta: d, kind: 'potential' });
        cur.p[k] = Number(cur.p[k] ?? 0) + d; want.p[k] -= d; moved = true;
      }
    }
    for (const k of Object.keys(want.r)) {
      if (!want.r[k]) continue;
      const hasPot = k in (base.potentials || {});
      const top = hasPot ? Number(cur.p[k] ?? 0) : Infinity;
      const d = Math.min(want.r[k], Math.max(0, top - Number(cur.r[k] ?? 0)));
      if (d > 0) {
        out.push({ rating: k, delta: d, kind: 'rating' });
        cur.r[k] = Number(cur.r[k] ?? 0) + d; want.r[k] -= d; moved = true;
      }
    }
    if (!moved) break;
  }
  const left = [...Object.entries(want.r), ...Object.entries(want.p)].filter(([, d]) => d > 0);
  return { order: out, stuck: left.map(([k]) => k) };
}
