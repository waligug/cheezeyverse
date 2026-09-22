/* The hub. Public: it renders for a signed-out visitor, because the league sites are
   public too and the whole point is being able to show somebody the universe. */

import {
  isConfigured, leagueSites, config, signIn, signOut, currentUser, ensureProfile,
  recentCharacters, characterCounts, errorText, allCharacters, LEAGUE_LABELS,
  oauthErrorFromUrl,
} from './supabase.js';
import {
  el, $, clear, renderChrome, renderFooter, setupNeededNote, showNote, statusPill,
  describeCharacter, fmtDate, note, freshJSON,
} from './ui.js';

const LEAGUES = [
  { key: 'prep', name: 'Cheezeyverse Prep', sub: 'Ages 14 to 17 · 30 games' },
  { key: 'college', name: 'Cheezeyverse College', sub: 'Ages 18 to 21 · 32 games' },
  { key: 'pro', name: 'The Cheezeyverse', sub: 'The pros · 58 games' },
];

const chrome = renderChrome({
  active: 'index.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});

const cfg = config();
if (cfg.tagline) $('#tagline').textContent = cfg.tagline;
document.title = cfg.universeName || 'The Cheezeyverse';

renderLeagues();
renderFooter();

/* An OAuth failure comes back in the URL, not as an exception - see oauthErrorFromUrl().
   Read it before anything else so the page can say what happened instead of just looking
   signed out. This runs even when the Supabase client is never constructed. */
const cvOauthError = oauthErrorFromUrl();
if (cvOauthError) showNote($('#notices'), 'bad', `Discord sign-in failed: ${cvOauthError}`);

if (!isConfigured()) {
  clear($('#recent'));
  $('#notices').append(setupNeededNote());
} else {
  boot().catch((err) => showNote($('#notices'), 'bad', errorText(err)));
}

function renderLeagues() {
  const sites = leagueSites();
  const box = $('#leagues');
  clear(box);
  for (const league of LEAGUES) {
    const site = sites[league.key];
    const tile = site.ready
      ? el('a', { class: 'cv-league cv-cheese', href: site.url })
      : el('span', { class: 'cv-league cv-cheese is-placeholder',
        title: 'Add this URL to site/config.js once the league site is published' });
    tile.append(el('b', {}, league.name), el('span', {}, site.ready ? league.sub : 'not published yet'));
    // The bracket link sits BESIDE the tile, not inside it: a tile is itself an <a>, and an <a>
    // within an <a> is invalid and renders unpredictably. The two go in a wrapper so the grid
    // still sees one item per league.
    //
    // The href is derived from the configured league URL by swapping its last segment, never
    // assembled from a hardcoded path - config.js's leagueSites is the single place those URLs
    // are set, and a second opinion about where a league lives is how the two drift apart the
    // day somebody moves one.
    const cell = el('div', { class: 'cv-league-cell' }, tile);
    if (site.ready) {
      cell.append(el('a', { class: 'cv-league-sub',
        href: site.url.replace(/[^/]*$/, 'playoffs.htm') }, 'Playoff bracket'));
    }
    box.append(cell);
  }
}


/* ------------------------------------------------------------------------- at a glance

   Who is winning, and how the real players are doing. Reads the stats.json the publish step
   writes beside each league's pages; the browser never parses the game's HTML, because that
   export has four traps in it that are already handled and tested in Python and a second
   implementation in JavaScript is how the two start disagreeing.

   Only real players, by Nate's decision: the four hundred the game invented are not why
   anybody opens this page. */

const GLANCE = new Map();

function glanceStats(league) {
  if (!GLANCE.has(league)) {
    GLANCE.set(league, freshJSON(`leagues/${league}/stats.json`));
  }
  return GLANCE.get(league);
}

/** "player53" for a character in this league - his id in the game, which survives a rename. */
function pageOf(character, league) {
  const id = (character.league_player_ids || {})[league];
  return id === undefined || id === null ? null : `player${id}`;
}

function glanceRow(label, value) {
  return el('td', { title: label }, value);
}

async function renderGlance() {
  const box = $('#glance');
  const [stats, characters] = await Promise.all([
    glanceStats(current), isConfigured() ? allCharacters() : Promise.resolve([]),
  ]);
  clear(box);

  const tabs = el('div', { class: 'cv-tabs' });
  for (const l of LEAGUES) {
    tabs.append(el('button', {
      class: `cv-tab${l.key === current ? ' is-on' : ''}`, type: 'button',
      onclick: () => { current = l.key; renderGlance(); renderMvp(); },
    }, LEAGUE_LABELS[l.key] || l.key));
  }
  box.append(tabs);

  if (!stats) {
    box.append(note(null, 'This league has not published its numbers yet. The panel fills in '
      + 'after the next Sim Week.'));
    return;
  }

  // ---- the top of the table
  box.append(el('h3', { class: 'cv-subhead' }, 'Top eight by record'));
  const seeds = el('table', { class: 'cv-table' },
    el('thead', {}, el('tr', {}, el('th', {}, '#'), el('th', {}, 'Team'),
      el('th', {}, 'W'), el('th', {}, 'L'), el('th', {}, 'Pct'))),
    el('tbody', {}, ...(stats.seeds || []).map((t) => el('tr', {},
      el('td', {}, String(t.seed)), el('th', {}, t.name),
      el('td', {}, String(t.w)), el('td', {}, String(t.l)),
      el('td', {}, t.pct.toFixed(3).replace(/^0/, ''))))));
  box.append(el('div', { class: 'cv-scroll' }, seeds));
  box.append(el('p', { class: 'cv-hint' },
    'Ordered by record, not by the game’s own bracket - it seeds by conference and breaks '
    + 'ties by its own rules.'));

  // ---- our people
  const mine = characters.filter((c) => c.league === current && c.status !== 'retired');
  box.append(el('h3', { class: 'cv-subhead' }, 'Our players'));
  if (!mine.length) {
    box.append(el('p', { class: 'cv-muted' },
      `Nobody is in ${LEAGUE_LABELS[current] || current} yet. Everybody starts in Prep and is `
      + 'promoted when he outgrows it.'));
    return;
  }

  const byPage = new Map((stats.players || []).map((r) => [r.page, r]));
  const byName = new Map((stats.players || []).map((r) => [r.name, r]));
  const rows = [];
  for (const c of mine) {
    const name = `${c.first_name} ${c.last_name}`;
    // by the game's own id first: a renamed character still matches, where his name does not
    const row = byPage.get(pageOf(c, current)) || byName.get(name) || null;
    const games = row ? Number(row.G) || 0 : 0;
    const per = (v) => (games ? (Number(v) / games).toFixed(1) : '-');
    rows.push(el('tr', {},
      el('th', {}, el('a', { href: `career.html?id=${encodeURIComponent(c.id)}` }, name)),
      el('td', {}, row ? row.team : '-'),
      glanceRow('games', row ? String(games) : '-'),
      ...['PTS', 'REB', 'AST', 'STL', 'BLK'].map((k) => glanceRow(k, row ? per(row[k]) : '-')),
      // an em dash, not 0: a bench player with no attempt at all has no percentage, and a zero
      // would sort him below somebody who genuinely missed everything
      glanceRow('true shooting', row && row.ts !== null && row.ts !== undefined
        ? row.ts.toFixed(3).replace(/^0/, '') : '—')));
  }
  // Nine columns will not fit a phone - measured at 464px inside a 387px viewport, which
  // pushed the whole PAGE sideways. cv-scroll keeps the overflow inside the table's own box,
  // which is what the career page already does with its wide tables. Dropping columns instead
  // would mean deciding on everybody's behalf that steals do not matter on a small screen.
  box.append(el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Player'), el('th', {}, 'Team'), el('th', {}, 'G'),
        el('th', {}, 'PTS'), el('th', {}, 'REB'), el('th', {}, 'AST'), el('th', {}, 'STL'),
        el('th', {}, 'BLK'), el('th', {}, 'TS'))),
      el('tbody', {}, ...rows))));
  box.append(el('p', { class: 'cv-hint' }, 'Per game. '
    + (stats.export_date ? `From the league export of ${stats.export_date}.` : '')));
}


/* ---------------------------------------------------------------------------- MVP race

   WHERE THE FORMULA COMES FROM. FBPB3 hands out its own awards, but only at the end of a
   season and only in the HTML - there is no in-progress MVP anywhere in the export, and no
   published formula to copy. So this is the plain box-score EFFICIENCY every basketball
   reference has used for decades:

       EFF = PTS + REB + AST + STL + BLK - (FGA - FGM) - (FTA - FTM)

   TURNOVERS BELONG IN THAT FORMULA AND ARE NOT HERE, because the league export does not carry
   them - stats.json's player rows have no TOV column. Everybody is missing the same term, so
   the ORDER is fair; the numbers are just a little kinder than the real thing. Worth knowing
   before anybody compares one of these to a figure off a real stats site.

   AND IT IS WEIGHTED BY WINNING, because an MVP race always is: nobody votes a 30-point night
   on a last-place team above the same night on a contender. A winless team's man keeps three
   quarters of his number and an undefeated team's man gets a quarter more.

   THE GAMES QUALIFIER IS THE PART THAT MATTERS MOST. Without it the leader is whoever had one
   huge game in November, which is not a race, it is a rounding error - so a player has to have
   played 60% of his team's games, the same shape of rule the real awards use. Early in a
   season that can leave the board short, and saying so is better than quietly listing nobody. */

const MVP_MIN_SHARE = 0.6;

function efficiency(row) {
  const n = (v) => Number(v) || 0;
  return n(row.PTS) + n(row.REB) + n(row.AST) + n(row.STL) + n(row.BLK)
    - (n(row.FGA) - n(row.FGM)) - (n(row.FTA) - n(row.FTM));
}

function mvpBoard(stats) {
  const played = stats.teams || {};                    // team -> games the team has played
  const pct = new Map((stats.table || []).map((r) => [r.name, Number(r.pct) || 0]));
  const board = [];
  for (const row of stats.players || []) {
    const games = Number(row.G) || 0;
    const teamGames = Number(played[row.team]) || 0;
    if (!games || !teamGames || games < teamGames * MVP_MIN_SHARE) continue;
    const per = efficiency(row) / games;
    board.push({
      name: row.name, team: row.team, games, per,
      score: per * (0.75 + 0.5 * (pct.get(row.team) || 0)),
    });
  }
  board.sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
  return board;
}

async function renderMvp() {
  const box = $('#mvp');
  if (!box) return;
  const stats = await glanceStats(current);
  clear(box);
  box.append(el('p', { class: 'cv-muted' }, LEAGUE_LABELS[current] || current));

  if (!stats) {
    box.append(note(null, 'This league has not published its numbers yet.'));
    return;
  }
  const board = mvpBoard(stats);
  if (!board.length) {
    box.append(note(null, `Nobody has played ${Math.round(MVP_MIN_SHARE * 100)}% of his team’s `
      + 'games yet, so there is no race to call.'));
    return;
  }

  const rows = board.slice(0, 5).map((p, i) => el('tr', {},
    el('th', {}, String(i + 1)),
    el('td', {}, p.name),
    el('td', { class: 'cv-muted' }, p.team),
    el('td', { title: 'efficiency per game' }, p.per.toFixed(1)),
    el('td', { title: 'weighted by the team’s record' }, p.score.toFixed(1))));

  box.append(el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {}, el('th', {}, '#'), el('th', {}, 'Player'),
        el('th', {}, 'Team'), el('th', {}, 'EFF'), el('th', {}, 'Score'))),
      el('tbody', {}, ...rows))));
  box.append(el('p', { class: 'cv-hint' },
    'EFF is points, rebounds, assists, steals and blocks, less missed shots and free throws, '
    + 'per game. Score weights it by the team’s record. Turnovers are not in the league export, '
    + 'so they are missing for everybody alike.'));
}

let current = 'prep';

async function boot() {
  const user = await currentUser();
  const profile = user ? await ensureProfile() : null;
  chrome.refresh(user, profile);

  // Its own try: a panel that cannot load must not cost the page its roll call.
  // Its own try as well: the race is the least important thing on the page and must not be
  // able to take the rest of it down.
  renderMvp().catch((err) => {
    console.warn('mvp race failed', err);
    const box = $('#mvp');
    if (box) { clear(box); box.append(note(null, 'The MVP race did not load.')); }
  });

  renderGlance().catch((err) => {
    console.warn('at-a-glance failed', err);
    const box = $('#glance');
    if (box) { clear(box); box.append(note(null, 'The league numbers did not load.')); }
  });

  const [rows, counts] = await Promise.all([recentCharacters(12), characterCounts()]);

  const census = $('#census');
  const live = counts.live;
  census.textContent = live
    ? `${live} live character${live === 1 ? '' : 's'} · `
      + LEAGUES.map((l) => `${counts.byLeague[l.key] || 0} ${l.key}`).join(' · ')
    : '';

  const box = $('#recent');
  clear(box);
  if (!rows.length) {
    box.append(el('p', {}, 'Nobody yet. ',
      el('a', { href: 'create.html' }, 'Be the first.')));
    return;
  }
  // Heights on this list are computed for the current season, not the stored
  // fourteen-year-old column, or everyone stays 14 forever on the public page.
  const seasonNow = (cfg && cfg.current_season) || null;
  const list = el('ul', { class: 'cv-roster' });
  for (const c of rows) {
    const owner = (c.owner_profile && c.owner_profile.display_name) || 'someone';
    list.append(el('li', {},
      el('b', {},
        el('a', { href: `career.html?id=${encodeURIComponent(c.id)}` },
          `${c.first_name} ${c.last_name}`)),
      statusPill(c.status),
      el('span', { class: 'cv-muted' },
        `${describeCharacter(c, seasonNow)} · ${owner} · ${fmtDate(c.created_at)}`)));
  }
  box.append(list);
}
