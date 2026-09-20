/* -------------------------------------------------------------------------- all time
 *
 * Every career ever played, per league. Reads the careers.json the publish step writes beside
 * each league's pages, which is built from the season-by-season archive under universe/history
 * rather than from the game's current export.
 *
 * WHY THAT DISTINCTION MATTERS HERE. FBPB3's stats table is a snapshot of the save, and the
 * save retires people out of existence - four pro players aged 34-35 vanished at the 2026
 * rollover. A leaderboard built from the live export is a leaderboard of whoever happens to
 * still be playing, which is the opposite of all-time. The archive keeps each season the day it
 * happens and never rewrites it, so a career survives the man.
 *
 * Everything the game invented is in here alongside the real players: 668 careers, and the
 * point of the page is who is best, not who is ours. Characters are marked so they can be
 * picked out of the crowd.
 * ------------------------------------------------------------------------------------ */

import { $, el, clear, freshJSON, renderChrome, renderFooter } from './ui.js';
import { client, isConfigured } from './supabase.js';

const LEAGUES = [['prep', 'Prep'], ['college', 'College'], ['pro', 'Pro']];

/* The boards the publish step works out for us, in the order they are worth reading. Points
   first because that is the question everybody actually asks. */
const BOARDS = [
  ['Points', 'Points'], ['efficiency', 'Efficiency'], ['Rebounds', 'Rebounds'],
  ['Assists', 'Assists'], ['Blocks', 'Blocks'], ['Steals', 'Steals'],
  ['3PM', 'Threes'], ['Minutes', 'Minutes'],
];

/* The columns of the full table. `key` reads a career row; `rate` marks the ones that are
   averages so they can be right-aligned with one decimal. */
const COLUMNS = [
  ['name', 'Player', false], ['team', 'Team', false], ['Games', 'G', false],
  ['Points', 'PTS', false], ['ppg', 'PPG', true], ['Rebounds', 'REB', false],
  ['rpg', 'RPG', true], ['Assists', 'AST', false], ['apg', 'APG', true],
  ['Steals', 'STL', false], ['Blocks', 'BLK', false], ['efficiency', 'EFF', false],
  ['fg_pct', 'FG%', true], ['tp_pct', '3P%', true], ['ft_pct', 'FT%', true],
];

let LEAGUE = 'pro';          // the deepest history, so the page opens on something worth reading
let SORT = 'Points';
let OURS = new Set();        // lower-cased names of the characters, for the marker

const CACHE = new Map();
function careersOf(league) {
  if (!CACHE.has(league)) CACHE.set(league, freshJSON(`leagues/${league}/careers.json`));
  return CACHE.get(league);
}

function num(value) {
  return typeof value === 'number' ? value : Number(value || 0);
}

/** One row of a leaderboard: the rank, who, the number, and enough to judge it by. */
function leaderRow(entry, rank, label) {
  const ours = OURS.has(String(entry.name || '').toLowerCase());
  return el('tr', { class: ours ? 'cv-ours' : null },
    el('td', { class: 'cv-rank' }, `${rank}`),
    el('td', {}, entry.name || '', ours ? el('span', { class: 'cv-pill is-active' }, 'ours') : null),
    el('td', { class: 'cv-num cv-strong' }, `${num(entry.value).toLocaleString()}`),
    el('td', { class: 'cv-num cv-muted' }, `${num(entry.games)} g`),
    el('td', { class: 'cv-muted' }, entry.from === entry.to ? `${entry.from}` : `${entry.from}-${entry.to}`));
}

function renderLeaders(data) {
  const host = $('#leaders');
  clear(host);
  if (!data || !data.leaders) {
    host.append(el('p', { class: 'cv-muted' },
      'Nothing published for this league yet. It appears after the next Sim Week.'));
    return;
  }
  const grid = el('div', { class: 'cv-grid' });
  for (const [key, label] of BOARDS) {
    const rows = (data.leaders[key] || []).slice(0, 10);
    if (!rows.length) continue;
    grid.append(el('div', { class: 'cv-card cv-sub' },
      el('h3', {}, `Most ${label.toLowerCase()}`),
      el('table', { class: 'cv-table cv-tight' },
        el('tbody', {}, rows.map((r, i) => leaderRow(r, i + 1, label))))));
  }
  host.append(grid);
}

function renderTable(data) {
  const host = $('#table');
  clear(host);
  if (!data || !data.careers || !data.careers.length) return;
  const rows = data.careers.slice().sort((a, b) => num(b[SORT]) - num(a[SORT]));
  const head = el('tr', {}, COLUMNS.map(([key, label]) => el('th', {
    class: key === 'name' || key === 'team' ? null : 'cv-num',
    'aria-sort': key === SORT ? 'descending' : null,
    onclick: () => { SORT = key; renderTable(data); },
  }, label)));
  const body = rows.slice(0, 250).map((c) => {
    const ours = OURS.has(String(c.name || '').toLowerCase());
    return el('tr', { class: ours ? 'cv-ours' : null }, COLUMNS.map(([key, , rate]) => {
      const v = c[key];
      if (key === 'name') {
        return el('td', {}, String(v || ''),
          ours ? el('span', { class: 'cv-pill is-active' }, 'ours') : null,
          el('small', { class: 'cv-muted' },
            ` ${c.first_season === c.last_season ? c.first_season : `${c.first_season}-${c.last_season}`}`));
      }
      if (key === 'team') return el('td', { class: 'cv-muted' }, String(v || ''));
      return el('td', { class: 'cv-num' }, rate ? num(v).toFixed(1) : num(v).toLocaleString());
    }));
  });
  host.append(
    el('p', { class: 'cv-muted cv-small' }, 'Click a column to sort. Showing the top 250.'),
    el('table', { class: 'cv-table' }, el('thead', {}, head), el('tbody', {}, body)));
}

function renderTabs() {
  const host = $('#leaders').parentElement;
  const bar = el('nav', { class: 'cv-tabs' }, LEAGUES.map(([key, label]) => el('button', {
    class: `cv-btn cv-small${key === LEAGUE ? '' : ' cv-ghost'}`,
    type: 'button',
    onclick: () => { LEAGUE = key; load(); },
  }, label)));
  host.insertBefore(bar, $('#leaders'));
}

async function load() {
  $('#leaders').replaceChildren(el('p', { class: 'cv-spinner' }, 'Loading every career...'));
  clear($('#table'));
  const data = await careersOf(LEAGUE);
  const seasons = data && data.seasons ? data.seasons : [];
  $('#coverage').textContent = seasons.length
    ? `${LEAGUE} · ${data.careers.length} careers · ${seasons.join(', ')}`
    : '';
  $('#career-note').textContent = seasons.length
    ? 'Totals across every season, including players the game invented.'
    : '';
  renderLeaders(data);
  renderTable(data);
  // the tab buttons rebuild with the new "current" styling
  const old = document.querySelector('.cv-tabs');
  if (old) old.remove();
  renderTabs();
}

/* Our own characters, so they can be marked in a field of four hundred strangers. Matched by
   name because that is what the archive carries - it is the game's own record, and it has no
   idea which of its players somebody on Discord is attached to. Never fatal: signed out, or
   Supabase unreachable, simply means nobody is highlighted. */
async function markOurs() {
  try {
    if (!isConfigured()) return;
    const db = client();
    if (!db) return;
    const { data } = await db.from('characters').select('first_name,last_name');
    for (const c of data || []) {
      OURS.add(`${c.first_name} ${c.last_name}`.trim().toLowerCase());
    }
  } catch (err) {
    /* a page about history does not need to know who is logged in */
  }
}

renderChrome({ active: 'goats.html' });
renderFooter();
markOurs().then(load);
