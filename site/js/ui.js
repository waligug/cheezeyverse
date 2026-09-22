/* =====================================================================================
 * Shared DOM bits: the page chrome, notices, and the rating-sheet widget that create.html
 * and me.html both spend points with.
 *
 * Plain DOM, no framework, no build step. Everything is created with document.createElement
 * rather than innerHTML, so a name someone typed can never become markup.
 * ===================================================================================== */

import {
  RATING_LABELS, RATING_GROUPS, POSITION_LABELS, RATING_MAX,
  TRAITS, TRAIT_LABELS, TRAIT_BLURBS,
  hasPotential, isLocked, nextPointCost, formatHeight, biasFor, classify, careerGoal,
  scoutingWord, certaintyWord, scoutScale, heightAtAge, START_AGE, GROWTH_END_AGE,
} from './rules.js';
import { config, leagueSites, displayNameOf } from './supabase.js';

/* ------------------------------------------------------------------------- tiny tools */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** el('div', {class: 'x', onclick: fn}, 'text', childNode) */
export function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

export function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

/* Fetch a file the commissioner republishes, without getting a cached copy of the last one.
 *
 * GitHub Pages serves everything with `Cache-Control: max-age=600` and these paths never change
 * when their contents do, so for ten minutes after a Sim Week the browser or the CDN can answer
 * with the previous week's numbers - and it looks exactly like a sim that did not run. Measured
 * on the live site: Age: 113 from the edge while the file itself had already been replaced.
 *
 * A query string is part of the cache key on Pages, so a per-minute value is enough to make each
 * publish a distinct URL. Per MINUTE rather than per load: a unique value every time would also
 * defeat the browser's own cache while somebody clicks between tabs, turning every tab change
 * into a fresh download of the same file. */
export function freshJSON(path) {
  const minute = Math.floor(Date.now() / 60000);
  const url = `${path}${path.includes('?') ? '&' : '?'}v=${minute}`;
  return fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);
}

export function fmtDate(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function plural(n, one, many) { return `${n} ${n === 1 ? one : many || `${one}s`}`; }

/** A dollar figure the way this site writes them. `--` for nothing, never `$NaN`. */
export function money(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '--';
  return `$${Math.round(Number(n)).toLocaleString('en-US')}`;
}

const SVGNS = 'http://www.w3.org/2000/svg';

/**
 * el() for SVG. It exists because el() builds with document.createElement, which cannot make an
 * SVG node - it returns an HTMLUnknownElement that lays out as an empty box and renders nothing
 * at all. Lives here rather than in a page module because two pages now draw charts, and the
 * second copy of a builder is where two charts start behaving differently.
 */
export function svg(tag, attrs, ...kids) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

/* -------------------------------------------------------------------------- notices */

export function note(kind, message, items) {
  const box = el('div', { class: `cv-note${kind ? ` is-${kind}` : ''}` }, el('p', {}, message));
  if (items && items.length) {
    box.append(el('ul', {}, items.map((t) => el('li', {}, t))));
  }
  return box;
}

/** Drop a notice into a container, replacing whatever notice was there. */
export function showNote(container, kind, message, items) {
  clear(container);
  if (message) container.append(note(kind, message, items));
}

export function statusPill(status) {
  const label = {
    pending: 'awaiting a roster spot',
    active: 'on a roster',
    declared: 'declared for the draft',
    retired: 'retired',
  }[status] || status;
  return el('span', { class: `cv-pill is-${status}` }, label);
}

/* ---------------------------------------------------------------------------- chrome */

const NAV = [
  { href: 'index.html', label: 'The Universe' },
  { href: 'create.html', label: 'Create a player' },
  { href: 'players.html', label: 'Roll call' },
  { href: 'stories.html', label: 'Stories' },
  { href: 'h2h.html', label: 'Head to head' },
  { href: 'goats.html', label: 'All time' },
  { href: 'cap.html', label: 'The cap' },
  { href: 'me.html', label: 'My players' },
];

/**
 * Header band with the wordmark, the nav and the Discord button. Called once per page;
 * call `refresh(user, profile)` afterwards when the auth state settles.
 */
export function renderChrome({ active, onSignIn, onSignOut }) {
  const cfg = config();
  const who = el('div', { class: 'cv-who' });

  const header = el('header', { class: 'cv-top cv-cheese' },
    el('div', { class: 'cv-top-inner' },
      el('a', { class: 'cv-wordmark', href: 'index.html' },
        el('b', {}, cfg.universeName || 'The Cheezeyverse'),
        el('small', {}, 'Prep · College · Pro')),
      el('nav', { class: 'cv-nav' },
        NAV.map((n) => el('a', {
          href: n.href,
          'aria-current': n.href === active ? 'page' : null,
        }, n.label))),
      who));

  document.body.prepend(header);

  function refresh(user, profile) {
    clear(who);
    if (user) {
      who.append(
        el('span', {}, displayNameOf(user, profile)),
        el('button', { class: 'cv-btn cv-small cv-ghost', type: 'button', onclick: onSignOut },
          'Sign out'),
      );
    } else {
      who.append(el('button', { class: 'cv-btn cv-small', type: 'button', onclick: onSignIn },
        'Sign in with Discord'));
    }
  }

  refresh(null, null);
  return { refresh };
}

export function renderFooter() {
  const sites = leagueSites();
  const links = [];
  for (const [key, label] of [['prep', 'Prep'], ['college', 'College'], ['pro', 'Pro']]) {
    if (sites[key].ready) links.push(el('a', { href: sites[key].url }, `${label} league`));
  }
  const foot = el('footer', { class: 'cv-foot' },
    el('p', {}, 'The Cheezeyverse. Standings, box scores and stats live on the league sites.'),
  );
  if (links.length) {
    const row = el('p', {});
    links.forEach((a, i) => { if (i) row.append(' · '); row.append(a); });
    foot.append(row);
  }
  document.body.append(foot);
  return foot;
}

/** The banner shown when site/config.js is still full of placeholders. */
export function setupNeededNote() {
  return note('bad',
    'This site is not pointed at a Supabase project yet.',
    ['Open site/config.js and replace supabaseUrl and supabaseAnonKey with the values from '
     + 'Supabase -> Project Settings -> API.',
     'Then run supabase/schema.sql and supabase/seed.sql in the Supabase SQL editor.',
     'Then enable Discord in Supabase -> Authentication -> Providers.']);
}

/* ---------------------------------------------------------------------- rating sheet */

/**
 * The spend widget. One row per rating: the value, a minus and a plus with the price of
 * the next point on it, and a bar showing where the rating sits against its potential.
 *
 * It renders from a state object and knows nothing about where the numbers came from, so
 * create.html (which edits a local sheet) and me.html (which queues upgrade requests) can
 * both drive it.
 *
 * There are no archetype caps any more: a rating is held by its own potential, a potential
 * is held by 100, and the cost curve is the only other limit.
 *
 * state = {
 *   base:   {ratings, potentials},   // what it looked like before this session's spending
 *   ratings, potentials,             // what it looks like now
 *   bias,                            // {rating: percent} - the character's growth bias
 *   budget,                          // points still free
 *   showPotentials: bool,
 * }
 * onStep(rating, kind, direction) is called with direction +1 or -1.
 */
/**
 * A tab strip over a set of panels. Returns the strip; panels are appended after it.
 *
 * This exists because of a measurement, not a preference. On a 390px screen the My players page
 * was 11,571px tall for two characters - about thirty phone screens - with 128 buttons on it,
 * and 87% of each card was the "Spend points" sheet sitting permanently open. Everything on the
 * page was worth having; having all of it at once was not.
 *
 * Panels are hidden rather than left unbuilt. Lazy building would trim the DOM as well, but the
 * spend sheet has to be in the document before drawSpend() can measure and fill it, and a tab
 * that renders nothing until touched is a much better way to ship a blank panel than a tall one.
 *
 * `sections` is [{ label, node, badge }]. The first is shown.
 */
export function tabs(sections) {
  const live = sections.filter((s) => s && s.node);
  const strip = el('div', { class: 'cv-tabs', role: 'tablist' });
  const buttons = live.map((section, i) => {
    const b = el('button', {
      class: `cv-tab${i ? '' : ' is-on'}`, type: 'button', role: 'tab',
      'aria-selected': i ? 'false' : 'true',
    }, section.label, section.badge ? el('span', { class: 'cv-tab-badge' }, section.badge) : null);
    section.node.hidden = i !== 0;
    section.node.setAttribute('role', 'tabpanel');
    b.addEventListener('click', () => {
      buttons.forEach((other, j) => {
        const on = other === b;
        other.classList.toggle('is-on', on);
        other.setAttribute('aria-selected', on ? 'true' : 'false');
        live[j].node.hidden = !on;
      });
    });
    return b;
  });
  strip.append(...buttons);
  return strip;
}

export function renderSheet(container, state, onStep) {
  const focused = document.activeElement && document.activeElement.dataset
    ? document.activeElement.dataset.key : null;
  clear(container);

  for (const group of RATING_GROUPS) {
    const box = el('section', { class: 'cv-sheet' }, el('h3', {}, group.title));
    for (const r of group.ratings) box.append(ratingRow(r, state, onStep));
    container.append(box);
  }

  if (focused) {
    const again = container.querySelector(`[data-key="${CSS.escape(focused)}"]`);
    if (again && !again.disabled) again.focus();
  }
}

function ratingRow(rating, state, onStep) {
  const cap = RATING_MAX;
  const value = state.ratings[rating];
  const baseValue = state.base.ratings[rating];
  const pot = hasPotential(rating) ? state.potentials[rating] : null;
  const basePot = hasPotential(rating) ? state.base.potentials[rating] : null;
  const locked = isLocked(rating);
  const bias = biasFor(state.bias, rating);

  const ceiling = pot === null ? cap : Math.min(cap, pot);
  const ratingCost = nextPointCost(value, 'rating', bias);
  const potCost = pot === null ? 0 : nextPointCost(pot, 'potential', bias);

  const canRaise = !locked && value < ceiling && ratingCost <= state.budget;
  const canLower = !locked && value > baseValue;
  const canRaisePot = !locked && pot !== null && pot < cap && potCost <= state.budget;
  const canLowerPot = !locked && pot !== null && pot > basePot;

  const row = el('div', { class: `cv-rating${locked ? ' is-locked' : ''}` });

  const knack = bias < 100 ? `comes easy (${bias}%)` : bias > 100 ? `hard work (${bias}%)` : '';
  row.append(el('div', { class: 'cv-rating-name' },
    RATING_LABELS[rating] || rating,
    ' ',
    el('em', {}, locked ? 'set by the quiz, never bought' : knack)));

  const controls = el('div', { class: 'cv-rating-controls' });
  controls.append(
    el('button', {
      class: 'cv-step', type: 'button', disabled: !canLower,
      title: `Take a point back off ${RATING_LABELS[rating]}`,
      'aria-label': `Lower ${RATING_LABELS[rating]}`,
      dataset: { key: `${rating}:rating:-1` },
      onclick: () => onStep(rating, 'rating', -1),
    }, '−'),
    el('span', {
      class: `cv-val${value > baseValue ? ' is-up' : ''}`,
      title: `${RATING_LABELS[rating]} is ${value}, ceiling ${ceiling}`,
    }, String(value)),
    el('button', {
      class: 'cv-step', type: 'button', disabled: !canRaise,
      title: locked ? 'This one cannot be bought'
        : value >= ceiling ? `Capped at ${ceiling}` : `Costs ${ratingCost}`,
      'aria-label': `Raise ${RATING_LABELS[rating]}, costs ${ratingCost}`,
      dataset: { key: `${rating}:rating:1` },
      onclick: () => onStep(rating, 'rating', 1),
    }, '+'),
  );

  if (state.showPotentials && pot !== null) {
    controls.append(
      el('span', { class: 'cv-cap' }, 'pot'),
      el('button', {
        class: 'cv-step', type: 'button', disabled: !canLowerPot,
        'aria-label': `Lower ${RATING_LABELS[rating]} potential`,
        dataset: { key: `${rating}:potential:-1` },
        onclick: () => onStep(rating, 'potential', -1),
      }, '−'),
      el('span', {
        class: `cv-val${pot > basePot ? ' is-up' : ''}`,
        title: `Potential ${pot} of a possible ${cap}`,
      }, String(pot)),
      el('button', {
        class: 'cv-step', type: 'button', disabled: !canRaisePot,
        title: pot >= cap ? `Capped at ${cap}` : `Costs ${potCost} (potentials are double)`,
        'aria-label': `Raise ${RATING_LABELS[rating]} potential, costs ${potCost}`,
        dataset: { key: `${rating}:potential:1` },
        onclick: () => onStep(rating, 'potential', 1),
      }, '+'),
    );
  } else if (state.showPotentials) {
    controls.append(el('span', { class: 'cv-cap' }, 'no potential'));
  }

  row.append(controls);

  const bar = el('div', { class: 'cv-bar' }, el('i', { style: `width:${Math.max(0, Math.min(100, value))}%` }));
  if (pot !== null) bar.append(el('u', { style: `left:${Math.max(0, Math.min(100, pot))}%`, title: `potential ${pot}` }));
  row.append(bar);

  return row;
}

/** The sticky points readout above a sheet. */
/**
 * The points meter. Three numbers that mean three different things, said plainly.
 *
 * It used to read "0 POINTS LEFT / 10 ALREADY WAITING ON THE COMMISSIONER / 0 SPENT", which is
 * how somebody with ten points concludes he has none. Two separate problems: "points left" was
 * really "free to spend right now", with the rest held against requests already sent; and
 * "spent" meant "queued in this session and not sent yet", not the lifetime figure the same
 * word means everywhere else on the page.
 *
 * `free` is what you can actually click with, `reserved` is held against requests the
 * commissioner has not applied yet, and `queued` is what is staged here and not sent.
 */
export function renderMeter(container, { free, reserved = 0, queued = 0 }) {
  clear(container);
  container.className = 'cv-meter cv-cheese';
  // Each number and its words are ONE child, because the meter is a wrapping flex row: as two
  // children they wrapped apart, leaving a bare "10" on one line and "already asked for" on the
  // next, which is a worse sentence than the one this function exists to fix.
  const stat = (n, words) => el('span', { class: 'cv-meter-stat' },
    el('b', {}, String(n)), el('span', {}, words));
  // .filter(Boolean), because this is a NATIVE append, not el(). el() skips null kids; the DOM
  // stringifies them, so a conditional stat that was not wanted printed the word "null" into the
  // meter in yellow-on-yellow capitals.
  container.append(...[
    stat(free, free === 1 ? 'point free to spend' : 'points free to spend'),
    reserved ? stat(reserved, 'already asked for') : null,
    queued ? stat(queued, 'staged below, not sent') : null,
  ].filter(Boolean));
}

/** A read-only stat sheet, for someone else's player or a character you cannot edit. */
export function renderReadOnlySheet(container, character) {
  clear(container);
  const ratings = character.ratings || {};
  const potentials = character.potentials || {};
  for (const group of RATING_GROUPS) {
    const table = el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, group.title), el('th', { class: 'cv-right' }, 'Now'),
        el('th', { class: 'cv-right' }, 'Potential'))),
      el('tbody', {}, group.ratings.map((r) => el('tr', {},
        el('td', {}, RATING_LABELS[r] || r),
        el('td', { class: 'cv-right' }, String(ratings[r] ?? '-')),
        el('td', { class: 'cv-right' }, hasPotential(r) ? String(potentials[r] ?? '-') : '–')))));
    container.append(el('div', { class: 'cv-scroll' }, table));
  }
}

/**
 * The derived sheet on the create page: the same three columns plus what the growth bias
 * does to the price. Read-only - creation spends nothing.
 */
export function renderDerivedSheet(container, derived) {
  clear(container);
  for (const group of RATING_GROUPS) {
    const table = el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, group.title),
        el('th', { class: 'cv-right' }, 'At 14'),
        el('th', { class: 'cv-right' }, 'Potential'),
        el('th', { class: 'cv-right' }, 'Price'))),
      el('tbody', {}, group.ratings.map((r) => {
        const bias = biasFor(derived.bias, r);
        return el('tr', {},
          el('td', {}, RATING_LABELS[r] || r),
          el('td', { class: 'cv-right' }, String(derived.ratings[r] ?? '-')),
          el('td', { class: 'cv-right' },
            hasPotential(r) ? String(derived.potentials[r] ?? '-') : '–'),
          el('td', { class: 'cv-right' },
            isLocked(r) ? 'never' : bias === 100 ? 'normal' : `${bias}%`));
      })));
    container.append(el('div', { class: 'cv-scroll' }, table));
  }
}

/**
 * The scouting sheet: what you see BEFORE he is signed.
 *
 * Deliberately numberless. Each rating gets a shaded band showing every value the roll can
 * land on and a scout's phrase read off the middle of that band, so you can tell a shooter
 * from a rebounder without being able to watch a number tick up as you flip an answer.
 * The band is the real range - see THE ROLL in rules.js.
 */
export function renderScoutingSheet(container, derived) {
  clear(container);
  for (const group of RATING_GROUPS) {
    const box = el('section', { class: 'cv-sheet' }, el('h3', {}, group.title));
    for (const r of group.ratings) {
      const range = (derived.ranges || {})[r] || [0, 0];
      const scale = scoutScale(r);
      const lo = Math.max(0, Math.min(100, (100 * range[0]) / scale));
      const hi = Math.max(0, Math.min(100, (100 * range[1]) / scale));
      box.append(el('div', { class: 'cv-rating' },
        el('div', { class: 'cv-rating-name' },
          RATING_LABELS[r] || r, ' ',
          el('em', {}, certaintyWord((derived.bands || {})[r]))),
        el('div', { class: 'cv-scout-word' }, scoutingWord(r, range)),
        el('div', { class: 'cv-bar' },
          el('s', { style: `left:${lo}%;width:${Math.max(2, hi - lo)}%` }))));
    }
    container.append(box);
  }
}

/**
 * The eleven traits as bars.
 *
 * `numbers` is off on the create page for the same reason the sheet is: a visible number
 * turns the quiz back into a stat allocator. Once he is signed it goes on.
 */
export function renderTraitBars(container, traits, { numbers = true } = {}) {
  clear(container);
  for (const t of TRAITS) {
    const v = Math.max(0, Math.min(100, Number((traits || {})[t]) || 0));
    container.append(el('div', { class: 'cv-trait' },
      el('span', { class: 'cv-trait-name' }, TRAIT_LABELS[t] || t),
      el('span', { class: 'cv-bar' }, el('i', { style: `width:${v}%` })),
      el('span', { class: 'cv-trait-val' }, numbers ? String(v) : traitWord(v)),
      el('span', { class: 'cv-trait-blurb' }, TRAIT_BLURBS[t] || '')));
  }
}

function traitWord(v) {
  if (v < 20) return 'none';
  if (v < 40) return 'low';
  if (v < 60) return 'some';
  if (v < 80) return 'lots';
  return 'rare';
}

/** "Right now this looks like a Playmaker (61% sure)" */
export function classLine(klass) {
  return `Right now this looks like a ${klass.label} · ${klass.confidence}% sure`;
}

/**
 * "6'4\" Sharpshooter shooting guard, Prep, BKI"
 *
 * The class is recomputed from the sheet every time rather than read off the row, because
 * the row's `archetype` column is only the label he had on the day he was created.
 */
/** Current height is synchronized from the save after offseason growth. */
export function heightNow(character, currentSeason) {
  const inches = Number(character.height_inches);
  return Number.isFinite(inches) && inches > 0 ? inches : null;
}

export function describeCharacter(character, currentSeason) {
  const klass = classify(character.ratings || {}, character.position);
  const bits = [
    formatHeight(heightNow(character, currentSeason) ?? character.height_inches),
    klass.label,
    POSITION_LABELS[character.position] || character.position,
  ];
  if (character.team_abbrev) bits.push(character.team_abbrev);
  return bits.filter(Boolean).join(' · ');
}

/**
 * The listed position, and where he has actually been playing.
 *
 * FBPB3's AI coach builds the depth charts and will play someone away from his listed
 * slot whenever it suits the roster, so "listed" and "actual" are genuinely different
 * facts. `character.minutes_by_position` is the hook for the real thing: the commissioner
 * fills it from the league export. Until it exists this renders the listed position and
 * says plainly that the coach decides.
 */
export function positionLine(character) {
  const listed = POSITION_LABELS[character.position] || character.position;
  const actual = character.minutes_by_position;
  if (!actual || typeof actual !== 'object') {
    return `Listed at ${listed}. Where he actually plays is up to the coach.`;
  }
  const rows = Object.entries(actual)
    .map(([pos, mins]) => [pos, Number(mins) || 0])
    .filter(([, mins]) => mins > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!rows.length) return `Listed at ${listed}. He has not played a minute yet.`;
  const total = rows.reduce((sum, [, mins]) => sum + mins, 0);
  const share = rows.slice(0, 3)
    .map(([pos, mins]) => `${pos} ${Math.round((100 * mins) / total)}%`)
    .join(' · ');
  return `Listed at ${listed}. Actually playing: ${share}.`;
}

/** The career goal, as the line of character it is meant to be. */
export function goalLine(goalId) {
  const goal = careerGoal(goalId);
  return goal ? goal.line : '';
}
