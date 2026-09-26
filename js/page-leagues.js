/* Leagues: the three levels, one at a time. Standings and per-game leaders from each league's
   stats.json - the same file the Home glance and the MVP race read, written by the publish step
   beside the league's own pages - with our players marked, and the way into the game's own
   pages for everything this does not show. Public, like the league sites. */

import {
  isConfigured, leagueSites, signIn, signOut, currentUser, ensureProfile, errorText,
  allCharacters, LEAGUE_LABELS,
} from './supabase.js';
import { $, el, clear, renderChrome, renderFooter, showNote, note, freshJSON } from './ui.js';

const LEAGUES = [
  { key: 'prep', games: 30 },
  { key: 'college', games: 32 },
  { key: 'pro', games: 58 },
];

/* The same qualifier the MVP race uses: 60% of his team's games, or a leaderboard is whoever
   had one big night in the first week. */
const MIN_SHARE = 0.6;

const BOARDS = [
  { stat: 'PTS', title: 'Points per game' },
  { stat: 'REB', title: 'Rebounds per game' },
  { stat: 'AST', title: 'Assists per game' },
];

/* The game's pages worth a direct link, in the order somebody looks for them. */
const SITE_PAGES = [
  ['standings.htm', 'Full standings'], ['schedule.htm', 'Schedule and box scores'],
  ['leaders.htm', 'Leaders'], ['teamleaders.htm', 'Team stats'], ['playoffs.htm', 'Playoff bracket'],
  ['transactions.htm', 'Transactions'], ['injuries.htm', 'Injuries'], ['freeagents.htm', 'Free agents'],
  ['draft.htm', 'Draft'], ['awards.htm', 'Awards'], ['champs.htm', 'Champions'],
];

const chrome = renderChrome({
  active: 'leagues.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const STATS = new Map();
function statsFor(key) {
  if (!STATS.has(key)) STATS.set(key, freshJSON(`leagues/${key}/stats.json`));
  return STATS.get(key);
}

const CHARACTERS = isConfigured()
  ? allCharacters().catch((err) => { console.warn('characters failed', err); return []; })
  : Promise.resolve([]);

const pick = (key) => (LEAGUES.some((l) => l.key === key) ? key : 'pro');
let current = pick(new URLSearchParams(window.location.search).get('league'));

renderSwitch();
draw();

if (isConfigured()) {
  (async () => {
    const user = await currentUser();
    chrome.refresh(user, user ? await ensureProfile() : null);
  })().catch((err) => console.warn('sign-in state failed', err));
}

function renderSwitch() {
  const box = $('#switch');
  clear(box);
  for (const league of LEAGUES) {
    const on = league.key === current;
    box.append(el('button', {
      type: 'button', role: 'tab', 'aria-selected': on ? 'true' : 'false',
      onclick: () => {
        if (league.key === current) return;
        current = league.key;
        // The league goes in the address, so a link from Home lands on the right one and a
        // shared link shows what the sender was looking at.
        window.history.replaceState(null, '', `?league=${current}`);
        renderSwitch();
        draw();
      },
    }, LEAGUE_LABELS[league.key] || league.key));
  }
}

async function draw() {
  const key = current;
  renderSiteLinks(key);
  let stats;
  let characters;
  try {
    [stats, characters] = await Promise.all([statsFor(key), CHARACTERS]);
  } catch (err) {
    showNote($('#notices'), 'bad', errorText(err));
    return;
  }
  if (key !== current) return;      // somebody clicked another league while this one loaded

  const standings = $('#standings');
  const leaders = $('#leaders');
  clear(standings);
  clear(leaders);
  $('#standings-note').textContent = '';
  if (!stats) {
    standings.append(note(null, 'This league has not published its numbers yet. They appear '
      + 'after the next Sim Week.'));
    return;
  }
  const ours = oursIn(stats, characters, key);
  renderStandings(stats, ours, key);
  renderLeaders(stats, ours, key);
  renderPlayoffs(key, stats).catch((err) => console.warn('playoffs failed', err));
}

/* The postseason: one chip per series, round by round, each opening that series' page - every
   game, the box scores and the MVPs. Shown only once the publish has written the series file for
   the season on screen. */
async function renderPlayoffs(key, stats) {
  const card = $('#playoffs-card');
  const box = $('#playoffs');
  card.hidden = true;
  clear(box);
  const year = String((stats && stats.season) || '').match(/(\d{4})/);
  if (!year) return;
  const data = await freshJSON(`leagues/${key}/series-${year[1]}.json`);
  if (key !== current || !data || !(data.series || []).length) return;
  const rounds = new Map();
  for (const s of data.series) {
    if (!rounds.has(s.round)) rounds.set(s.round, { name: s.round_name, list: [] });
    rounds.get(s.round).list.push(s);
  }
  const seed = (t) => (t.seed ? `#${t.seed} ` : '');
  for (const [, round] of [...rounds].sort((x, y) => x[0] - y[0])) {
    box.append(el('h4', { class: 'cv-subhead' }, round.name),
      el('div', { class: 'cv-series-list' }, round.list.map((s) => {
        const [a, b] = s.teams;
        const href = `series.html?league=${key}&season=${data.season}`
          + `&a=${encodeURIComponent(a.name)}&b=${encodeURIComponent(b.name)}`;
        return el('a', { class: 'cv-series-chip', href },
          el('span', { class: s.winner === a.name ? 'is-won' : null }, `${seed(a)}${a.name} ${a.wins}`),
          el('span', { class: 'cv-muted' }, ' – '),
          el('span', { class: s.winner === b.name ? 'is-won' : null }, `${b.wins} ${seed(b)}${b.name}`));
      })));
  }
  $('#playoffs-note').textContent = data.champion
    ? `${data.season} champion: ${data.champion} · tap a series for every game`
    : `${data.season} · tap a series for every game`;
  card.hidden = false;
}

/** Row -> our character, for every character of ours with a line in this league's stats. */
function oursIn(stats, characters, key) {
  const players = stats.players || [];
  const out = new Map();
  for (const c of characters) {
    if (c.league !== key || !(c.status === 'active' || c.status === 'declared')) continue;
    // by the game's own id first: a renamed character still matches, where his name does not
    const id = (c.league_player_ids || {})[key];
    const page = id === undefined || id === null ? null : `player${id}`;
    const name = `${c.first_name} ${c.last_name}`;
    const row = (page && players.find((r) => r.page === page)) || players.find((r) => r.name === name);
    if (row) out.set(row, c);
  }
  return out;
}

/* WHERE A NAME GOES WHEN YOU CLICK IT. A team opens its roster on the league's own site; one of
   ours opens his career page; anybody else opens his page in the league export. Built off the
   configured league URL (config.js leagueSites), never a hardcoded path. A name we cannot place
   stays plain text rather than linking somewhere wrong. */
function leagueBase(key) {
  const site = leagueSites()[key];
  return site && site.ready ? site.url.replace(/[^/]*$/, '') : null;
}

function teamLink(key, name, roster) {
  const base = leagueBase(key);
  return base && roster ? el('a', { href: `${base}rosters/${roster}.htm` }, name) : name;
}

function playerLink(key, row, character) {
  if (character && character.id) {
    return el('a', { href: `career.html?id=${encodeURIComponent(character.id)}` }, row.name);
  }
  const base = leagueBase(key);
  return base && row.page ? el('a', { href: `${base}players/${row.page}.htm` }, row.name) : row.name;
}

function renderStandings(stats, ours, key) {
  const league = LEAGUES.find((l) => l.key === key);
  const onTeam = new Map();
  for (const [row, c] of ours) {
    if (!onTeam.has(row.team)) onTeam.set(row.team, []);
    onTeam.get(row.team).push(c.first_name);
  }
  const table = [...(stats.table || [])].sort((a, b) => (Number(b.pct) || 0) - (Number(a.pct) || 0)
    || b.w - a.w || String(a.name).localeCompare(String(b.name)));
  const lead = table[0];
  const played = Math.max(0, ...table.map((t) => Number(t.games) || 0));
  const pct = (v) => (Number(v) || 0).toFixed(3).replace(/^0/, '');

  $('#standings-note').textContent = [
    stats.season, league ? `${played} of ${league.games} games` : '',
  ].filter(Boolean).join(' · ');

  const rows = table.map((t, i) => {
    const names = onTeam.get(t.name) || [];
    const gb = i === 0 ? '—' : (((lead.w - t.w) + (t.l - lead.l)) / 2).toFixed(1);
    return el('tr', { class: names.length ? 'is-ours' : null },
      el('td', { class: 'cv-muted' }, String(i + 1)),
      el('th', { scope: 'row' }, teamLink(key, t.name, t.roster)),
      el('td', { class: 'cv-right' }, el('b', {}, String(t.w))),
      el('td', { class: 'cv-right' }, String(t.l)),
      el('td', { class: 'cv-right cv-muted' }, pct(t.pct)),
      el('td', { class: 'cv-right cv-muted' }, gb),
      el('td', { class: 'cv-ours-names' }, names.join(', ')));
  });
  $('#standings').append(el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, '#'), el('th', {}, 'Team'), el('th', { class: 'cv-right' }, 'W'),
        el('th', { class: 'cv-right' }, 'L'), el('th', { class: 'cv-right' }, 'Pct'),
        el('th', { class: 'cv-right' }, 'GB'), el('th', {}, 'Ours'))),
      el('tbody', {}, ...rows))),
  el('p', { class: 'cv-hint' }, 'Ordered by record, not by the game’s own bracket - it seeds by '
    + 'conference and breaks ties by its own rules. '
    + (stats.export_date ? `From the league export of ${stats.export_date}.` : '')));
}

function renderLeaders(stats, ours, key) {
  const rosterOf = new Map((stats.table || []).map((t) => [t.name, t.roster]));
  const teamGames = stats.teams || {};
  const qualified = (stats.players || []).filter((r) => {
    const games = Number(r.G) || 0;
    const team = Number(teamGames[r.team]) || 0;
    return games && team && games >= team * MIN_SHARE;
  });
  const box = $('#leaders');
  for (const board of BOARDS) {
    const top = qualified.map((r) => ({ r, v: (Number(r[board.stat]) || 0) / Number(r.G) }))
      .sort((a, b) => b.v - a.v || String(a.r.name).localeCompare(String(b.r.name)))
      .slice(0, 5);
    const card = el('section', { class: 'cv-card' },
      el('div', { class: 'cv-card-head' }, el('h2', {}, board.title)));
    if (!top.length) {
      card.append(el('p', { class: 'cv-muted' },
        `Nobody has played ${Math.round(MIN_SHARE * 100)}% of his team’s games yet.`));
    } else {
      card.append(el('ol', { class: 'cv-leader-list' }, top.map(({ r, v }, i) => {
        const mine = ours.has(r);
        return el('li', { class: mine ? 'is-ours' : null },
          el('span', {}, String(i + 1)),
          el('span', { class: 'cv-leader-name' }, playerLink(key, r, ours.get(r)),
            mine ? el('span', { class: 'cv-ours-badge' }, 'ours') : null),
          el('span', { class: 'cv-leader-team' }, teamLink(key, r.team, rosterOf.get(r.team))),
          el('b', {}, v.toFixed(1)));
      })));
    }
    box.append(card);
  }
}

function renderSiteLinks(key) {
  const site = leagueSites()[key];
  const box = $('#sitelinks');
  clear(box);
  if (!site || !site.ready) {
    box.append(el('p', { class: 'cv-muted' }, 'This league site is not published yet.'));
    return;
  }
  // Derived from the configured league URL by swapping its last segment - config.js's
  // leagueSites is the single place those URLs are set.
  box.append(el('a', { class: 'cv-btn cv-small', href: site.url },
    `${LEAGUE_LABELS[key] || key} league site`));
  for (const [page, label] of SITE_PAGES) {
    box.append(el('a', { class: 'cv-btn cv-small cv-ghost', href: site.url.replace(/[^/]*$/, page) }, label));
  }
}
