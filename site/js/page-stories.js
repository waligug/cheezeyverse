/* Stories: the feed, reshaped on 2026-09-26.

   What was wrong with it: 448 stories and 340 of them are awards, MVP-watch ranks and rivalry
   head-counts - a wall of
   near-identical cards ("X made the All-Star team", "X is #73 in the MVP watch") that buried the
   career nights and the ugly tape. So:
     - awards, MVP-watch ranks and rivalries fold into ONE card per season each, unless you ask
       for them;
     - the best recent story gets the big dark card at the top;
     - chips instead of dropdowns for the kind of story and whose, with counts;
     - 24 at a time, with "show more", instead of every story ever on one page. */

import {
  $, el, clear, renderChrome, renderFooter, freshJSON, showNote,
} from './ui.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, errorText,
} from './supabase.js';
import { STORY_LABELS, storyCard } from './story-ui.js';

const chrome = renderChrome({
  active: 'stories.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const LEAGUE_NAMES = { prep: 'Prep', college: 'College', pro: 'Pro' };
const PAGE = 24;

/* Which story earns the big card: the first of these kinds in the newest season on screen. */
const HEADLINE_ORDER = ['career_high', 'playoff', 'hot_streak', 'disaster', 'rivalry', 'cold_streak', 'trade', 'award_race', 'award'];

/* Kinds that come in dozens at once and read better as one list than as a card each. Rivalries
   too: 67 of them, and "they have shared the floor 9 times" is a table row, not a headline. */
const FOLDED = { award: 'honours', award_race: 'MVP watch', rivalry: 'rivalries' };

const state = { type: '', player: '', season: '', limit: PAGE };
let data = { events: [], players: {} };

boot().catch((err) => {
  $('#loading').hidden = true;
  showNote($('#notices'), 'bad', errorText(err));
});

async function boot() {
  if (isConfigured()) {
    currentUser().then(async (user) => chrome.refresh(user, user ? await ensureProfile() : null)).catch(() => {});
  }
  const loaded = await freshJSON('data/stories.json');
  $('#loading').hidden = true;
  if (!loaded) {
    showNote($('#notices'), 'bad', 'The story feed has not been published yet.');
    return;
  }
  data = loaded;
  const params = new URLSearchParams(window.location.search);
  if (params.get('player') && data.players && data.players[params.get('player')]) state.player = params.get('player');
  if (params.get('type') && STORY_LABELS[params.get('type')]) state.type = params.get('type');
  if (params.get('season')) state.season = params.get('season');

  const seasons = [...new Set(data.events.map((e) => e.season).filter(Boolean))].sort((a, b) => b - a);
  for (const season of seasons) $('#season').append(el('option', { value: String(season) }, `Season ${season}`));
  $('#season').value = state.season;
  $('#season').addEventListener('change', (e) => set({ season: e.target.value }));
  draw();
}

/** Change a filter, start the list again from the top, and put the filters in the address. */
function set(change) {
  Object.assign(state, change, { limit: PAGE });
  const params = new URLSearchParams();
  for (const key of ['player', 'type', 'season']) if (state[key]) params.set(key, state[key]);
  const query = params.toString();
  window.history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
  $('#season').value = state.season;
  draw();
}

const matches = (e, { type = state.type, player = state.player, season = state.season } = {}) =>
  (!type || e.type === type)
  && (!player || (e.character_ids || []).includes(player))
  && (!season || String(e.season) === String(season));

function draw() {
  drawTypeChips();
  drawPeopleChips();

  const events = data.events.filter((e) => matches(e));
  const featured = headline(events);
  drawFeatured(featured);

  const rest = events.filter((e) => e !== featured);
  const items = group(rest);
  const root = $('#stories');
  clear(root);
  if (!events.length) {
    root.append(el('section', { class: 'cv-card' }, el('h2', {}, 'Nothing in that slice yet'),
      el('p', {}, 'The feed grows after every published sim as games, honours and transactions enter the record.')));
    clear($('#more'));
    return;
  }

  // Season headings between the groups, unless one season is picked (then the select says it).
  let season = null;
  let grid = null;
  for (const item of items.slice(0, state.limit)) {
    const itemSeason = item.season;
    if (!grid || (!state.season && itemSeason !== season)) {
      season = itemSeason;
      if (!state.season) root.append(el('h2', { class: 'cv-season-head' }, `Season ${season}`));
      grid = el('div', { class: 'cv-story-list' });
      root.append(grid);
    }
    grid.append(item.fold ? foldCard(item) : storyCard(item.event, data.players));
  }

  const more = $('#more');
  clear(more);
  if (items.length > state.limit) {
    more.append(el('button', {
      class: 'cv-btn cv-ghost', type: 'button',
      onclick: () => { state.limit += PAGE; draw(); },
    }, `Show more (${items.length - state.limit} left)`));
  }
}

/** The feed as items: one per story, except the kinds in FOLDED, which become one per season. */
function group(events) {
  const items = [];
  const folds = new Map();
  for (const event of events) {
    if (FOLDED[event.type] && state.type !== event.type) {
      const key = `${event.type}:${event.season}`;
      if (!folds.has(key)) {
        const fold = { fold: true, type: event.type, season: event.season, events: [] };
        folds.set(key, fold);
        items.push(fold);
      }
      folds.get(key).events.push(event);
    } else {
      items.push({ event, season: event.season });
    }
  }
  return items;
}

function headline(events) {
  if (!events.length) return null;
  const newest = events[0].season;
  const recent = events.filter((e) => e.season === newest);
  for (const type of HEADLINE_ORDER) {
    const hit = recent.find((e) => e.type === type);
    if (hit) return hit;
  }
  return recent[0];
}

/** "20 points, 11 rebounds, 6 assists" -> tiles. Only the counting stats the detail spells out. */
function statTiles(event) {
  const words = { points: 'PTS', rebounds: 'REB', assists: 'AST', steals: 'STL', blocks: 'BLK' };
  const tiles = [];
  const found = new Set();
  const text = String(event.detail || '');
  for (const m of text.matchAll(/(\d+(?:\.\d+)?)\s+(points|rebounds|assists|steals|blocks)\b/g)) {
    if (found.has(m[2])) continue;
    found.add(m[2]);
    tiles.push([m[1], words[m[2]]]);
  }
  return tiles.slice(0, 5);
}

function drawFeatured(event) {
  const box = $('#featured');
  clear(box);
  if (!event) return;
  const where = [
    STORY_LABELS[event.type] || event.type,
    LEAGUE_NAMES[event.league] || event.league,
    event.season ? `Season ${event.season}` : '',
    event.day ? `Day ${event.day}` : '',
  ].filter(Boolean).join(' · ');
  const tiles = statTiles(event);
  const people = el('p', { class: 'cv-feature-people' });
  (event.character_ids || []).forEach((id, i) => {
    const person = (data.players || {})[id];
    if (i) people.append(' · ');
    people.append(person
      ? el('a', { href: `career.html?id=${encodeURIComponent(id)}` }, `${person.name}'s career`)
      : el('span', {}, event.player || ''));
  });
  box.append(el('section', { class: 'cv-feature' },
    el('div', {},
      el('div', { class: 'cv-kicker' }, where),
      el('h2', {}, event.title),
      el('p', {}, event.detail),
      event.game ? el('p', { class: 'cv-feature-game' }, event.game) : null,
      people),
    tiles.length ? el('div', { class: 'cv-feature-tiles' },
      tiles.map(([n, k]) => el('div', {}, el('b', {}, n), el('span', {}, k)))) : null));
}

function foldCard(fold) {
  const label = FOLDED[fold.type];
  const shown = fold.events.slice(0, 6);
  const card = el('article', { class: 'cv-story cv-story-fold' },
    el('div', { class: 'cv-story-kicker' },
      el('span', { class: `cv-story-kind is-${fold.type}` }, STORY_LABELS[fold.type] || fold.type),
      el('span', { class: 'cv-muted' }, `Season ${fold.season} · ${fold.events.length}`)),
    el('h3', {}, `Season ${fold.season} ${label}`),
    el('ul', { class: 'cv-fold-list' }, shown.map((e) => {
      const id = (e.character_ids || [])[0];
      // a rivalry's title is only the two names; who leads is in the detail
      const lead = e.type === 'rivalry' ? String(e.detail || '').replace(/^.*?;\s*/, '').replace(/\.$/, '') : '';
      return el('li', {},
        id ? el('a', { href: `career.html?id=${encodeURIComponent(id)}` }, e.title) : el('b', {}, e.title),
        lead ? el('span', {}, ` · ${lead}`) : null,
        el('span', { class: 'cv-muted' }, ` ${LEAGUE_NAMES[e.league] || e.league || ''}`));
    })));
  if (fold.events.length > shown.length || state.type !== fold.type) {
    card.append(el('button', {
      class: 'cv-btn cv-small cv-ghost', type: 'button',
      onclick: () => { set({ type: fold.type, season: String(fold.season) }); window.scrollTo(0, 0); },
    }, fold.events.length > shown.length ? `See all ${fold.events.length}` : 'Open as cards'));
  }
  return card;
}

function chip(label, count, on, onclick, extra, title) {
  return el('button', {
    type: 'button', class: `cv-chip-btn${on ? ' is-on' : ''}`, 'aria-pressed': on ? 'true' : 'false', onclick, title,
  }, extra || null, label, count !== null ? el('span', { class: 'cv-chip-count' }, String(count)) : null);
}

function drawTypeChips() {
  const box = $('#types');
  clear(box);
  const pool = data.events.filter((e) => matches(e, { type: '' }));
  box.append(chip('Everything', pool.length, !state.type, () => set({ type: '' })));
  for (const type of Object.keys(STORY_LABELS)) {
    const n = pool.filter((e) => e.type === type).length;
    if (!n && state.type !== type) continue;
    box.append(chip(STORY_LABELS[type], n, state.type === type,
      () => set({ type: state.type === type ? '' : type })));
  }
}

function drawPeopleChips() {
  const box = $('#people');
  clear(box);
  const people = Object.entries(data.players || {}).sort((a, b) => a[1].name.localeCompare(b[1].name));
  box.append(chip('Everybody', null, !state.player, () => set({ player: '' })));
  for (const [id, person] of people) {
    const initials = person.name.split(/\s+/).map((w) => w[0] || '').join('').slice(0, 2).toUpperCase();
    box.append(chip(person.name.split(/\s+/)[0], null, state.player === id,
      () => set({ player: state.player === id ? '' : id }),
      el('span', { class: `cv-avatar is-tiny is-${person.league}`, 'aria-hidden': 'true' }, initials),
      person.name));
  }
}
