/* ------------------------------------------------------------------------ the GOAT score
 *
 * The browser half of commissioner/publish/goat.py. The publish step works out each player's
 * COMPONENTS - season values, peak, playoffs, titles with the share he carried, honours - and
 * this combines them with weights the reader can move. Nothing here is a fixed verdict: the
 * sliders are the argument, and the default is just the one we open with.
 *
 * CARRYING. `share` is a player's season value over his whole team's that season, so 0.20 is a
 * fair share for a starter and 0.40 is a team leaning on one man. It shows up twice:
 *   - a ring is scaled by the share he had in the title season, as far as the "carry
 *     adjustment" slider says (0 = every ring counts the same, 1 = fully by share), so a ring
 *     he carried outweighs a ring he rode;
 *   - "load" adds points for every season he carried more than a fair share.
 * ------------------------------------------------------------------------------------ */

import { el, clear } from './ui.js';

export const DEFAULT_WEIGHTS = {
  peak: 2, career: 0.5, playoffs: 0.15, load: 0.5,
  title: 40, carryAdjust: 0.5, finals_mvp: 40, mvp: 30,
  all_league_1: 15, all_league_2: 10, all_league_3: 5,
  all_defensive: 4, dpoy: 10, all_star: 5, roy: 3,
};

/* [key, label, max, step, what it means] - the order the sliders appear in. */
const SLIDERS = [
  ['peak', 'Peak (best 3 seasons)', 5, 0.1, 'per point of his best-three average'],
  ['career', 'Career value', 2, 0.05, 'per point of every season added up - longevity'],
  ['playoffs', 'Playoff value', 1, 0.05, 'per point of playoff production'],
  ['load', 'Carrying load', 2, 0.05, 'per point of team share above a fair 20%, every season'],
  ['title', 'Each title', 100, 1, 'per ring'],
  ['carryAdjust', 'Ring carry adjustment', 1, 0.05,
    '0 = every ring counts the same; 1 = a ring is worth what share of the team he was'],
  ['finals_mvp', 'Finals MVP', 100, 1, 'each'],
  ['mvp', 'MVP', 100, 1, 'each'],
  ['all_league_1', 'All-League 1st team', 50, 1, 'each'],
  ['all_league_2', 'All-League 2nd team', 50, 1, 'each'],
  ['all_league_3', 'All-League 3rd team', 50, 1, 'each'],
  ['all_defensive', 'All-Defensive', 30, 1, 'each'],
  ['dpoy', 'Defensive Player of the Year', 50, 1, 'each'],
  ['all_star', 'All-Star', 30, 1, 'each'],
  ['roy', 'Rookie of the Year', 30, 1, 'each'],
];

const HONOUR_LABELS = {
  finals_mvp: 'Finals MVP', mvp: 'MVP', all_league_1: 'All-League 1st', all_league_2: 'All-League 2nd',
  all_league_3: 'All-League 3rd', all_defensive: 'All-Defensive', dpoy: 'DPOY', all_star: 'All-Star',
  roy: 'ROY',
};

const STORE_KEY = 'cv-goat-weights';
const CARRIER = 0.40;       // average team share at which a career reads as carrying
const PASSENGER = 0.12;     // a title won with less than this share reads as along for the ride

export function loadWeights() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (saved && typeof saved === 'object') return { ...DEFAULT_WEIGHTS, ...saved };
  } catch (err) { /* private window, blocked storage: defaults */ }
  return { ...DEFAULT_WEIGHTS };
}

function saveWeights(w) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(w)); } catch (err) { /* fine */ }
}

function num(v) { return typeof v === 'number' ? v : Number(v || 0); }

/** A ring's worth: the full weight at a fair share, more if he carried it, less if he rode. */
function ringFactor(share, fair, adjust) {
  const ratio = Math.max(0.25, Math.min(2, num(share) / fair));
  return 1 - adjust + adjust * ratio;
}

/** {total, parts: [[label, points]]} for one player under these weights. */
export function score(p, w, fair = 0.2) {
  const parts = [
    ['Peak', w.peak * num(p.peak)],
    ['Career', w.career * num(p.career)],
    ['Playoffs', w.playoffs * num(p.playoffs)],
    ['Carrying load', w.load * num(p.carry)],
  ];
  const rings = (p.titles || []).reduce((sum, t) => sum + w.title * ringFactor(t.share, fair, w.carryAdjust), 0);
  if (rings) parts.push([`Titles (${(p.titles || []).length})`, rings]);
  for (const [key, label] of Object.entries(HONOUR_LABELS)) {
    const n = num((p.honours || {})[key]);
    if (n && w[key]) parts.push([`${label} x${n}`, w[key] * n]);
  }
  return { total: parts.reduce((s, [, v]) => s + v, 0), parts: parts.filter(([, v]) => v) };
}

export function tag(p) {
  const titles = p.titles || [];
  if (titles.length && titles.reduce((s, t) => s + num(t.share), 0) / titles.length < PASSENGER) {
    return ['Passenger', 'his titles came with under 12% of his team\'s production'];
  }
  if (num(p.load) >= CARRIER) {
    return ['Carrier', `averaged ${Math.round(num(p.load) * 100)}% of his team's production`];
  }
  return null;
}

function breakdown(p, s, fair) {
  const seasons = (p.seasons || []).map((x) => el('tr', {},
    el('td', {}, String(x.s)), el('td', { class: 'cv-muted' }, x.team || ''),
    el('td', { class: 'cv-num' }, String(x.g)),
    el('td', { class: 'cv-num' }, num(x.v).toFixed(0)),
    el('td', { class: `cv-num${num(x.share) >= CARRIER ? ' cv-strong' : ''}` },
      `${Math.round(num(x.share) * 100)}%`),
    el('td', {}, (p.titles || []).some((t) => t.s === x.s) ? 'Champion' : '')));
  return el('div', { class: 'cv-goat-detail' },
    el('div', { class: 'cv-goat-parts' }, s.parts.map(([label, v]) =>
      el('div', {}, el('span', {}, label), el('b', {}, v.toFixed(0))))),
    el('p', { class: 'cv-muted cv-small' },
      `Season value: production above replacement, 100 = an average top-ten season that year. `
      + `Share: his part of his team's total. A fair share is ${Math.round(fair * 100)}%.`),
    el('div', { class: 'cv-scroll' }, el('table', { class: 'cv-table cv-tight' },
      el('thead', {}, el('tr', {}, ['Season', 'Team', 'G', 'Value', 'Share', ''].map((h) => el('th', {}, h)))),
      el('tbody', {}, seasons))));
}

/**
 * Render the whole GOAT section into `host`.
 * `opts.isOurs(name)` marks our characters; `opts.onlyOurs` filters to them (ranks stay league-wide).
 */
export function renderGoatBoard(host, data, opts = {}) {
  clear(host);
  const players = (data && data.players) || [];
  if (!players.length) {
    host.append(el('p', { class: 'cv-muted' },
      'No GOAT numbers for this league yet. They appear after the next Sim Week publishes.'));
    return;
  }
  const fair = num(data.fair_share) || 0.2;
  const w = opts.weights || loadWeights();
  const isOurs = opts.isOurs || (() => false);
  const open = new Set();

  const board = el('div', {});
  const draw = () => {
    clear(board);
    const ranked = players.map((p) => ({ p, s: score(p, w, fair) }))
      .sort((a, b) => b.s.total - a.s.total || String(a.p.name).localeCompare(String(b.p.name)))
      .map((r, i) => ({ ...r, rank: i + 1 }))
      .filter((r) => !opts.onlyOurs || isOurs(r.p.name));
    const shown = ranked.slice(0, opts.onlyOurs ? ranked.length : 50);
    const rows = [];
    for (const r of shown) {
      const { p, s } = r;
      const ours = isOurs(p.name);
      const t = tag(p);
      const key = `${p.name}|${p.dob}`;
      const h = p.honours || {};
      const allLeague = num(h.all_league_1) + num(h.all_league_2) + num(h.all_league_3);
      rows.push(el('tr', {
        class: `cv-goat-row${ours ? ' cv-ours' : ''}`, tabindex: '0',
        title: 'Click for the breakdown',
        onclick: () => { if (open.has(key)) open.delete(key); else open.add(key); draw(); },
        onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); if (open.has(key)) open.delete(key); else open.add(key); draw(); } },
      },
      el('td', { class: 'cv-rank' }, String(r.rank)),
      el('td', {}, p.name, ours ? el('span', { class: 'cv-pill is-active' }, 'ours') : null,
        t ? el('span', { class: `cv-pill cv-goat-tag is-${t[0].toLowerCase()}`, title: t[1] }, t[0]) : null,
        el('small', { class: 'cv-muted' }, ` ${p.from === p.to ? p.from : `${p.from}-${p.to}`}`)),
      el('td', { class: 'cv-num cv-strong' }, s.total.toFixed(0)),
      el('td', { class: 'cv-num' }, num(p.peak).toFixed(0)),
      el('td', { class: 'cv-num' }, num(p.career).toFixed(0)),
      el('td', { class: 'cv-num' }, String((p.titles || []).length || '')),
      el('td', { class: 'cv-num' }, String(num(h.mvp) || '')),
      el('td', { class: 'cv-num' }, String(allLeague || '')),
      el('td', { class: 'cv-num' }, `${Math.round(num(p.load) * 100)}%`)));
      if (open.has(key)) {
        rows.push(el('tr', { class: 'cv-goat-open' }, el('td', { colspan: '9' }, breakdown(p, s, fair))));
      }
    }
    board.append(el('div', { class: 'cv-scroll' }, el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {}, [
        ['#', ''], ['Player', ''], ['GOAT', 'the score under the weights above'],
        ['Peak', 'average value of his best three seasons'], ['Career', 'every season added up'],
        ['Rings', 'titles'], ['MVP', 'regular-season MVPs'], ['All-League', 'All-League teams, any tier'],
        ['Load', 'his average share of his team\'s production; 20% is fair, 40%+ is carrying'],
      ].map(([h, hint], i) => el('th', { class: i >= 2 ? 'cv-num' : null, title: hint || null }, h)))),
      el('tbody', {}, rows))));
    board.append(el('p', { class: 'cv-muted cv-small' }, opts.onlyOurs
      ? 'Our players only; the rank is among everybody in the league.'
      : `Top ${shown.length} of ${ranked.length}. Click a player for how his score is made.`));
  };

  const sliders = el('div', { class: 'cv-goat-sliders' });
  const drawSliders = () => {
    clear(sliders);
    for (const [key, label, max, step, hint] of SLIDERS) {
      const out = el('output', {}, String(w[key]));
      sliders.append(el('label', { title: hint },
        el('span', {}, label), out,
        el('input', {
          type: 'range', min: '0', max: String(max), step: String(step), value: String(w[key]),
          oninput: (e) => { w[key] = Number(e.target.value); out.textContent = e.target.value; saveWeights(w); draw(); },
        })));
    }
    sliders.append(el('button', {
      class: 'cv-btn cv-small cv-ghost', type: 'button',
      onclick: () => { Object.assign(w, DEFAULT_WEIGHTS); saveWeights(w); drawSliders(); draw(); },
    }, 'Reset to default'));
  };
  drawSliders();

  host.append(
    el('details', { class: 'cv-fold' },
      el('summary', {}, 'Adjust the formula'),
      el('p', { class: 'cv-muted cv-small' },
        'Move a slider and the board re-ranks. Your weights are remembered on this device.'),
      sliders),
    board);
  draw();
}
