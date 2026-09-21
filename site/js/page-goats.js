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
/* [key, heading, isRate, what the hover says]. The fourth is spelled out because a column
   headed PF or EFF tells a newcomer nothing, and the tooltip is the only place to say it. */
const COLUMNS = [
  ['name', 'Player', false, 'name'],
  ['team', 'Team', false, 'team'],
  ['Games', 'G', false, 'games played'],
  ['Points', 'PTS', false, 'total points'],
  ['ppg', 'PPG', true, 'points per game'],
  ['Rebounds', 'REB', false, 'total rebounds'],
  ['rpg', 'RPG', true, 'rebounds per game'],
  ['Assists', 'AST', false, 'total assists'],
  ['apg', 'APG', true, 'assists per game'],
  ['Steals', 'STL', false, 'total steals'],
  ['Blocks', 'BLK', false, 'total blocks'],
  ['efficiency', 'EFF', false, 'efficiency: points + rebounds + assists + steals + blocks, '
    + 'minus missed shots and turnovers'],
  ['fg_pct', 'FG%', true, 'field goal percentage'],
  ['tp_pct', '3P%', true, 'three point percentage'],
  ['ft_pct', 'FT%', true, 'free throw percentage'],
];

let LEAGUE = 'pro';          // the deepest history, so the page opens on something worth reading
let SORT = 'Points';
let DIR = -1;                // -1 biggest first, 1 smallest first
let ONLY_OURS = false;
let PER_GAME = false;
let MODE = 'regular';        // 'regular' | 'playoffs'
let OURS = new Set();        // lower-cased names of the characters, for the marker

/* WHICH ARCHIVE IS ON SCREEN. The postseason is its OWN set of careers, published as its own
   block, because FBPB3 keeps it in its own table and SeasonStats is the regular season alone -
   a playoff line is not a filter over a regular-season one, and the two never overlap. A league
   that has not reached a postseason has no block at all, which is why every reader comes
   through here instead of touching data.playoffs directly. */
function view(data) {
  if (MODE === 'playoffs' && data && data.playoffs) {
    return { careers: data.playoffs.careers || [], seasons: data.playoffs.seasons || [] };
  }
  return { careers: (data && data.careers) || [], seasons: (data && data.seasons) || [] };
}

/* The coverage line and the note under it both depend on the mode, so they are rendered here
   rather than in load() - the selector has to be able to update them without a refetch. */
function renderCoverage(data) {
  const active = view(data);
  const seasons = active.seasons || [];
  const playoffs = MODE === 'playoffs';
  $('#coverage').textContent = seasons.length
    ? `${LEAGUE} · ${active.careers.length} ${playoffs ? 'playoff ' : ''}careers · ${seasons.join(', ')}`
    : '';
  $('#career-note').textContent = seasons.length
    ? (playoffs
      ? 'Postseason totals only, across every playoff run in the archive.'
      : 'Regular-season totals across every season, including players the game invented.')
    : '';
}

function isOurs(row) {
  return OURS.has(String(row && row.name || '').toLowerCase());
}

/* Our players on one board, each carrying the rank he holds among EVERYBODY.
   Filtering the published top-ten would usually show nothing at all - none of the seven is top
   ten in a league of four hundred - and worse, re-ranking the survivors 1..7 would invent a
   standing that does not exist. The whole question is "where does he actually come", so the
   rank is taken from the full career list before anyone is filtered out. */
function ranked(careers, stat, limit) {
  const value = c => PER_GAME ? num(c[stat]) / Math.max(1, num(c.Games)) : num(c[stat]);
  const ordered = careers.filter(c => num(c.Games) > 0).slice().sort((a, b) =>
    value(b) - value(a) || String(a.name).localeCompare(String(b.name)));
  return ordered.map((c, i) => ({ name: c.name, value: value(c), games: c.Games,
    rank: i + 1, from: c.first_season, to: c.last_season }))
    .filter(c => !ONLY_OURS || isOurs(c)).slice(0, limit);
}

function renderControls(data) {
  const host = $('#all-time-controls');
  clear(host);
  host.append(el('label', {}, el('input', { type: 'checkbox', checked: ONLY_OURS || null,
    onchange: e => { ONLY_OURS = e.target.checked; renderLeaders(data); renderTable(data); }
  }), 'Real players only'));
  if (data && data.playoffs) {
    host.append(el('label', {}, 'Show: ', el('select', {
      onchange: e => {
        MODE = e.target.value;
        renderCoverage(data); renderLeaders(data); renderTable(data);
      }
    }, el('option', { value: 'regular', selected: MODE === 'regular' || null }, 'Regular season'),
       el('option', { value: 'playoffs', selected: MODE === 'playoffs' || null }, 'Playoffs'))));
  }
  host.append(el('label', {}, 'Leaders: ', el('select', {
    onchange: e => { PER_GAME = e.target.value === 'per-game'; renderLeaders(data); }
  }, el('option', { value: 'totals', selected: !PER_GAME || null }, 'Totals'),
     el('option', { value: 'per-game', selected: PER_GAME || null }, 'Per game'))));
  host.append(el('span', { class: 'cv-muted cv-small' },
    'Per-game leaders include careers with at least one game. Ranks are across the whole league.'));
}

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
    el('td', { class: 'cv-num cv-strong' }, PER_GAME ? num(entry.value).toFixed(1) : num(entry.value).toLocaleString()),
    el('td', { class: 'cv-num cv-muted' }, `${num(entry.games)} g`),
    el('td', { class: 'cv-muted' }, entry.from === entry.to ? `${entry.from}` : `${entry.from}-${entry.to}`));
}

function renderLeaders(data) {
  const host = $('#leaders');
  clear(host);
  const active = view(data);
  if (!active.careers.length) {
    host.append(el('p', { class: 'cv-muted' }, MODE === 'playoffs'
      ? 'No playoff careers in this league yet. They appear once a postseason has been played.'
      : 'Nothing published for this league yet. It appears after the next Sim Week.'));
    return;
  }
  const grid = el('div', { class: 'cv-grid' });
  for (const [key, label] of BOARDS) {
    const rows = ranked(active.careers, key, 10);
    if (!rows.length) continue;
    grid.append(el('div', { class: 'cv-card cv-sub' },
      el('h3', {}, PER_GAME ? `${label} per game` : `Most ${label.toLowerCase()}`),
      el('table', { class: 'cv-table cv-tight' },
        el('tbody', {}, rows.map((r) => leaderRow(r, r.rank, label))))));
  }
  host.append(grid);
}

/* Click the sorted column to reverse it; click a new one to sort it biggest-first, since that
   is what somebody asking "who has the most" wants. Name and team start A-Z instead. */
function sortBy(key) {
  if (SORT === key) {
    DIR = -DIR;
  } else {
    SORT = key;
    DIR = (key === 'name' || key === 'team') ? 1 : -1;
  }
}


function renderTable(data) {
  const host = $('#table');
  clear(host);
  const active = view(data);
  if (!active.careers.length) return;
  let rows = active.careers.slice();
  if (ONLY_OURS) rows = rows.filter(isOurs);
  rows.sort((a, b) => {
    const x = num(a[SORT]);
    const y = num(b[SORT]);
    if (x === y) return String(a.name || '').localeCompare(String(b.name || ''));
    return (x < y ? -1 : 1) * DIR;
  });
  const head = el('tr', {}, COLUMNS.map(([key, label, , hint]) => el('th', {
    class: `cv-sortable${key === 'name' || key === 'team' ? '' : ' cv-num'}`,
    // The title is the hover hint; the aria-sort is what a screen reader and the arrow both
    // read. A second click on the same column flips the direction, like a spreadsheet.
    title: key === SORT
      ? `Sorted by ${hint || label} - click to reverse`
      : `Sort by ${hint || label}`,
    tabindex: '0',
    'aria-sort': key === SORT ? (DIR === -1 ? 'descending' : 'ascending') : null,
    onclick: () => { sortBy(key); renderTable(data); },
    onkeydown: (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortBy(key); renderTable(data); }
    },
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
    el('p', { class: 'cv-muted cv-small' },
      `Click a column to sort, again to reverse. Showing ${Math.min(rows.length, 250)} of ${rows.length}.`),
    el('div', { class: 'cv-scroll' },
      el('table', { class: 'cv-table' }, el('thead', {}, head), el('tbody', {}, body))));
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
  clear($('#all-time-controls'));
  const requestedLeague = LEAGUE;
  const data = await careersOf(requestedLeague);
  if (requestedLeague !== LEAGUE) return;
  // A league with no postseason archive cannot stay on the playoff view when you tab to it.
  if (MODE === 'playoffs' && !(data && data.playoffs)) MODE = 'regular';
  renderCoverage(data);
  renderControls(data);
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
