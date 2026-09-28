/* One playoff series: the result and the series MVP, every game with its MVP and a full box
   score, and what everybody averaged - shooting splits and efficiency included.

   Reached from the bracket (the win counts on the league site's playoffs.htm are links here) and
   from the Leagues page. Reads leagues/<league>/series-<season>.json, which the publish step
   builds from an archive of every playoff line (commissioner/publish/series.py): the game's own
   box pages only reach back about a month, and its database forgets the whole postseason at the
   rollover. Public, like the league sites. */

import { $, el, clear, renderChrome, renderFooter, freshJSON, showNote, note } from './ui.js';
import {
  isConfigured, allCharacters, leagueSites, signIn, signOut, currentUser, ensureProfile,
  errorText, LEAGUE_LABELS,
} from './supabase.js';

const LEAGUES = ['prep', 'college', 'pro'];
const params = new URLSearchParams(window.location.search);
const LEAGUE = LEAGUES.includes(params.get('league')) ? params.get('league') : 'pro';
const SEASON = parseInt(params.get('season'), 10) || null;

const chrome = renderChrome({
  active: 'leagues.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const state = { data: null, series: null, game: null, perGame: true, ours: new Map() };

boot().catch((err) => {
  $('#loading').hidden = true;
  showNote($('#notices'), 'bad', errorText(err));
});

async function boot() {
  if (isConfigured()) {
    currentUser().then(async (user) => chrome.refresh(user, user ? await ensureProfile() : null)).catch(() => {});
  }
  const season = SEASON || await latestSeason();
  const [data, characters] = await Promise.all([
    season ? freshJSON(`leagues/${LEAGUE}/series-${season}.json`) : null,
    isConfigured() ? allCharacters().catch(() => []) : [],
  ]);
  $('#loading').hidden = true;
  const bracket = leagueBase() ? `${leagueBase()}playoffs.htm` : null;
  if (bracket) $('#bracket-link').href = bracket;

  if (!data || !(data.series || []).length) {
    $('#title').textContent = `${LEAGUE_LABELS[LEAGUE] || LEAGUE} playoffs${season ? ` ${season}` : ''}`;
    $('#notices').append(note(null, 'There is no game-by-game record for this postseason. Series '
      + 'pages exist from the 2037 playoffs on; earlier brackets are on the league site.'));
    return;
  }
  // our characters, by their id in this league's save
  for (const c of characters || []) {
    const id = (c.league_player_ids || {})[LEAGUE];
    if (id !== undefined && id !== null) state.ours.set(Number(id), c);
  }
  state.data = data;
  state.series = pickSeries(data.series) || data.series[data.series.length - 1];
  const games = state.series.games;
  state.game = games.length ? games[games.length - 1].n : null;
  render();
}

async function latestSeason() {
  const stats = await freshJSON(`leagues/${LEAGUE}/stats.json`);
  const m = stats && String(stats.season || '').match(/(\d{4})/);
  return m ? Number(m[1]) : null;
}

function pickSeries(all) {
  const id = params.get('id');
  if (id) return all.find((s) => s.id === id.toLowerCase());
  const want = [params.get('a'), params.get('b')].filter(Boolean).map((t) => t.toLowerCase()).sort();
  if (want.length !== 2) return null;
  return all.find((s) => s.teams.map((t) => t.name.toLowerCase()).sort().join('~') === want.join('~'));
}

/* ------------------------------------------------------------------------------ helpers */

function leagueBase() {
  const site = leagueSites()[LEAGUE];
  return site && site.ready ? site.url.replace(/[^/]*$/, '') : null;
}

const num = (v) => Number(v) || 0;
/** .468, or — when there were no attempts: a zero would claim he missed them all. */
const pct = (made, tried) => (num(tried) ? (num(made) / num(tried)).toFixed(3).replace(/^0/, '') : '—');
const tsOf = (p) => { const d = 2 * (num(p.fga) + 0.44 * num(p.fta)); return d ? (num(p.pts) / d).toFixed(3).replace(/^0/, '') : '—'; };
const efgOf = (p) => (num(p.fga) ? ((num(p.fgm) + 0.5 * num(p.tpm)) / num(p.fga)).toFixed(3).replace(/^0/, '') : '—');
const signed = (v) => (v > 0 ? `+${v}` : String(v));
const split = (rate, madeTried) => el('span', { class: 'cv-split-cell' }, rate,
  el('small', { title: 'made-attempted' }, madeTried));

function seriesUrl(s) {
  const [a, b] = s.teams.map((t) => encodeURIComponent(t.name));
  return `series.html?league=${LEAGUE}&season=${state.data.season}&a=${a}&b=${b}`;
}

function playerName(id, name) {
  const ours = state.ours.get(Number(id));
  if (ours) {
    return el('span', {}, el('a', { href: `career.html?id=${encodeURIComponent(ours.id)}` }, name),
      el('span', { class: 'cv-ours-badge' }, 'ours'));
  }
  const base = leagueBase();
  return base ? el('a', { href: `${base}players/player${id}.htm` }, name) : name;
}

function seedOf(team) { return team.seed ? `#${team.seed} ` : ''; }

/* ------------------------------------------------------------------------------- render */

function render() {
  const { data, series } = state;
  const [a, b] = series.teams;
  const league = LEAGUE_LABELS[LEAGUE] || LEAGUE;
  document.title = `${a.name} vs ${b.name} · ${league} ${data.season} playoffs · The Cheezeyverse`;
  $('#kicker').textContent = `${league} · ${data.season} playoffs · ${series.round_name}`;
  $('#title').textContent = `${a.name} ${a.wins}–${b.wins} ${b.name}`;
  $('#lede').textContent = resultLine(series);
  renderHero();
  renderGames();
  renderBox();
  renderMode();
  renderStats();
  renderOthers();
  for (const id of ['games-card', 'stats-card', 'others-card']) $(`#${id}`).hidden = false;
}

function resultLine(series) {
  const [a, b] = series.teams;
  const played = series.games.length;
  if (series.winner) {
    const w = series.teams.find((t) => t.name === series.winner);
    const l = series.teams.find((t) => t.name !== series.winner);
    if (played === 1) {
      const g = series.games[0];
      return `${w.name} won the only game, ${Math.max(g.hs, g.as)}–${Math.min(g.hs, g.as)}.`;
    }
    return `${w.name} won the series ${w.wins}–${l.wins}${played >= 5 ? `, in ${played}` : ''}.`
      + (series.length ? ` Best of ${series.length}.` : '');
  }
  if (!played) return 'Not started yet.';
  if (a.wins === b.wins) return `Series tied ${a.wins}–${b.wins}.`;
  const lead = a.wins > b.wins ? a : b;
  const trail = lead === a ? b : a;
  return `${lead.name} lead ${lead.wins}–${trail.wins}.` + (series.length ? ` Best of ${series.length}.` : '');
}

function renderHero() {
  const { series } = state;
  const box = $('#hero');
  clear(box);
  const teams = el('div', { class: 'cv-series-teams' }, series.teams.map((t) => el('div', {
    class: `cv-series-team${series.winner === t.name ? ' is-winner' : ''}`,
  }, el('span', { class: 'cv-series-seed' }, t.seed ? `#${t.seed}` : ''),
  el('span', { class: 'cv-series-name' }, t.name),
  series.winner === t.name ? el('span', { class: 'cv-series-won' }, 'Won') : null,
  el('b', { class: 'cv-series-wins' }, String(t.wins)))));

  let side;
  const mvp = series.mvp;
  if (mvp) {
    const gp = num(mvp.gp) || 1;
    const per = (k) => (num(mvp[k]) / gp).toFixed(1);
    side = el('div', { class: 'cv-series-mvp' },
      el('div', { class: 'cv-kicker' }, 'Series MVP'),
      el('h2', {}, playerName(mvp.id, mvp.name)),
      el('p', {}, `${mvp.team} · ${gp} game${gp === 1 ? '' : 's'}`),
      el('div', { class: 'cv-feature-tiles' },
        [[per('pts'), 'PTS'], [per('reb'), 'REB'], [per('ast'), 'AST'], [tsOf(mvp), 'TS']]
          .map(([n, k]) => el('div', {}, el('b', {}, n), el('span', {}, k)))),
      el('p', { class: 'cv-feature-game' },
        `${pct(mvp.fgm, mvp.fga)} FG · ${pct(mvp.tpm, mvp.tpa)} 3P · ${pct(mvp.ftm, mvp.fta)} FT · `
        + `${(num(mvp.gmsc) / gp).toFixed(1)} Game Score a night`));
  } else {
    side = el('div', { class: 'cv-series-mvp' }, el('div', { class: 'cv-kicker' }, 'Series MVP'),
      el('p', {}, 'Named when the series is over.'));
  }
  box.append(el('div', { class: 'cv-feature cv-series-hero' }, teams, side));
}

function gameLine(g) {
  const winHome = g.hs > g.as;
  return [
    { name: g.away, pts: g.as, won: !winHome, at: false },
    { name: g.home, pts: g.hs, won: winHome, at: true },
  ];
}

function renderGames() {
  const box = $('#games');
  clear(box);
  for (const g of state.series.games) {
    const on = g.n === state.game;
    const rows = gameLine(g).map((r) => el('div', { class: `cv-game-row${r.won ? ' is-won' : ''}` },
      el('span', {}, r.at ? `@ ${r.name}` : r.name), el('b', {}, String(r.pts))));
    const mvp = g.mvp ? el('p', { class: 'cv-game-mvp' }, el('span', {}, 'MVP '),
      `${g.mvp.name} · ${g.mvp.pts} pts, ${g.mvp.reb} reb, ${g.mvp.ast} ast`) : null;
    box.append(el('button', {
      type: 'button', class: `cv-game-card${on ? ' is-on' : ''}`, 'aria-pressed': on ? 'true' : 'false',
      onclick: () => { state.game = g.n; renderGames(); renderBox(); },
    }, el('div', { class: 'cv-game-head' }, el('b', {}, state.series.games.length > 1 ? `Game ${g.n}` : 'The game'),
      el('span', {}, `Day ${g.day}`)), ...rows, mvp));
  }
}

const BOX_COLS = [
  ['MIN', (l) => l.min], ['PTS', (l) => l.pts], ['REB', (l) => l.reb], ['AST', (l) => l.ast],
  ['STL', (l) => l.stl], ['BLK', (l) => l.blk], ['TO', (l) => l.to], ['PF', (l) => l.pf],
  ['FG', (l) => `${l.fgm}-${l.fga}`], ['3P', (l) => `${l.tpm}-${l.tpa}`], ['FT', (l) => `${l.ftm}-${l.fta}`],
  ['+/-', (l) => signed(num(l.pm))], ['GmSc', (l) => num(l.gmsc).toFixed(1)],
];

function renderBox() {
  const box = $('#box');
  clear(box);
  const g = state.series.games.find((x) => x.n === state.game);
  if (!g) return;
  const base = leagueBase();
  const head = el('div', { class: 'cv-box-head' },
    el('h3', {}, `${state.series.games.length > 1 ? `Game ${g.n}: ` : ''}${g.away} ${g.as}, @ ${g.home} ${g.hs}`),
    base && g.box ? el('a', { href: `${base}boxes/box${g.box}.htm` }, 'Quarter-by-quarter on the league site') : null);
  box.append(head);
  for (const team of [g.away, g.home]) {
    const lines = g.lines.filter((l) => l.team === team)
      .sort((x, y) => (y.start - x.start) || (num(y.min) - num(x.min)));
    const totals = { fgm: 0, fga: 0, tpm: 0, tpa: 0, ftm: 0, fta: 0 };
    for (const l of lines) for (const k of Object.keys(totals)) totals[k] += num(l[k]);
    const sum = (k) => lines.reduce((s, l) => s + num(l[k]), 0);
    const rows = lines.map((l) => el('tr', { class: state.ours.has(Number(l.id)) ? 'cv-ours' : null },
      el('th', { scope: 'row' }, playerName(l.id, l.name),
        l.start ? el('span', { class: 'cv-starter', title: 'started' }, 'S') : null,
        g.mvp && g.mvp.id === l.id ? el('span', { class: 'cv-mvp-tag' }, 'MVP') : null),
      ...BOX_COLS.map(([, f]) => el('td', { class: 'cv-right' }, String(f(l))))));
    rows.push(el('tr', { class: 'cv-total' }, el('th', { scope: 'row' }, 'Team'),
      ...['min', 'pts', 'reb', 'ast', 'stl', 'blk', 'to', 'pf'].map((k) => el('td', { class: 'cv-right' }, String(sum(k)))),
      el('td', { class: 'cv-right' }, `${totals.fgm}-${totals.fga}`),
      el('td', { class: 'cv-right' }, `${totals.tpm}-${totals.tpa}`),
      el('td', { class: 'cv-right' }, `${totals.ftm}-${totals.fta}`),
      el('td', {}, ''), el('td', {}, '')));
    rows.push(el('tr', { class: 'cv-total cv-splits' }, el('th', { scope: 'row' }, 'Shooting'),
      el('td', { colspan: '8' }, ''),
      el('td', { class: 'cv-right' }, pct(totals.fgm, totals.fga)),
      el('td', { class: 'cv-right' }, pct(totals.tpm, totals.tpa)),
      el('td', { class: 'cv-right' }, pct(totals.ftm, totals.fta)),
      el('td', {}, ''), el('td', {}, '')));
    box.append(el('h4', { class: 'cv-subhead' }, `${team} ${team === g.home ? g.hs : g.as}`),
      el('div', { class: 'cv-scroll' }, el('table', { class: 'cv-table cv-tight cv-box-table' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Player'), ...BOX_COLS.map(([h]) => el('th', { class: 'cv-right' }, h)))),
        el('tbody', {}, rows))));
  }
}

function renderMode() {
  const box = $('#mode');
  clear(box);
  for (const [perGame, words] of [[true, 'Per game'], [false, 'Totals']]) {
    box.append(el('button', {
      type: 'button', role: 'tab', 'aria-selected': state.perGame === perGame ? 'true' : 'false',
      onclick: () => { state.perGame = perGame; renderMode(); renderStats(); },
    }, words));
  }
}

function renderStats() {
  const box = $('#stats');
  clear(box);
  const pg = state.perGame;
  const show = (p, k, digits = 1) => (pg ? (num(p[k]) / Math.max(1, num(p.gp))).toFixed(digits) : String(num(p[k])));
  const cols = [
    ['GP', (p) => String(p.gp)], ['MIN', (p) => show(p, 'min')], ['PTS', (p) => show(p, 'pts')],
    ['REB', (p) => show(p, 'reb')], ['AST', (p) => show(p, 'ast')], ['STL', (p) => show(p, 'stl')],
    ['BLK', (p) => show(p, 'blk')], ['TO', (p) => show(p, 'to')],
    // the percentage on top and the makes-attempts under it: one column per shot type instead of
    // two keeps the table inside the card on a laptop
    ['FG%', (p) => split(pct(p.fgm, p.fga), `${show(p, 'fgm')}-${show(p, 'fga')}`)],
    ['3P%', (p) => split(pct(p.tpm, p.tpa), `${show(p, 'tpm')}-${show(p, 'tpa')}`)],
    ['FT%', (p) => split(pct(p.ftm, p.fta), `${show(p, 'ftm')}-${show(p, 'fta')}`)],
    ['TS', tsOf], ['eFG', efgOf], ['+/-', (p) => (pg ? (num(p.pm) / Math.max(1, num(p.gp))).toFixed(1) : signed(num(p.pm)))],
    ['GmSc', (p) => show(p, 'gmsc')],
  ];
  const games = state.series.games.length;
  for (const team of state.series.teams) {
    const players = state.series.players[team.name] || [];
    const total = { gp: games, ...Object.fromEntries(['min', 'pts', 'reb', 'ast', 'stl', 'blk', 'to', 'fgm', 'fga',
      'tpm', 'tpa', 'ftm', 'fta', 'pm', 'gmsc'].map((k) => [k, players.reduce((s, p) => s + num(p[k]), 0)])) };
    total.pm = Math.round(total.pm / 5);     // five men on the floor share every point of margin
    const rows = players.map((p) => el('tr', {
      class: [state.ours.has(Number(p.id)) ? 'cv-ours' : '', state.series.mvp && state.series.mvp.id === p.id ? 'is-mvp' : ''].join(' ').trim() || null,
    }, el('th', { scope: 'row' }, playerName(p.id, p.name),
      state.series.mvp && state.series.mvp.id === p.id ? el('span', { class: 'cv-mvp-tag' }, 'MVP') : null),
    ...cols.map(([, f]) => el('td', { class: 'cv-right' }, f(p)))));
    rows.push(el('tr', { class: 'cv-total' }, el('th', { scope: 'row' }, 'Team'),
      ...cols.map(([h, f]) => el('td', { class: 'cv-right' }, h === 'GP' ? String(games) : f(total)))));
    box.append(el('h4', { class: 'cv-subhead' }, `${seedOf(team)}${team.name}`
      + (state.series.winner === team.name ? ' · won the series' : '')),
    el('div', { class: 'cv-scroll' }, el('table', { class: 'cv-table cv-tight cv-box-table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Player'), ...cols.map(([h]) => el('th', { class: 'cv-right' }, h)))),
      el('tbody', {}, rows))));
  }
}

function renderOthers() {
  const box = $('#others');
  clear(box);
  const rounds = new Map();
  for (const s of state.data.series) {
    if (!rounds.has(s.round)) rounds.set(s.round, { name: s.round_name, list: [] });
    rounds.get(s.round).list.push(s);
  }
  for (const [, round] of [...rounds].sort((x, y) => x[0] - y[0])) {
    box.append(el('h4', { class: 'cv-subhead' }, round.name),
      el('div', { class: 'cv-series-list' }, round.list.map((s) => {
        const [a, b] = s.teams;
        const on = s === state.series;
        return el('a', { class: `cv-series-chip${on ? ' is-on' : ''}`, href: seriesUrl(s), 'aria-current': on ? 'page' : null },
          el('span', { class: s.winner === a.name ? 'is-won' : null }, `${seedOf(a)}${a.name} ${a.wins}`),
          el('span', { class: 'cv-muted' }, ' – '),
          el('span', { class: s.winner === b.name ? 'is-won' : null }, `${b.wins} ${seedOf(b)}${b.name}`));
      })));
  }
  if (state.data.champion) {
    box.append(el('p', { class: 'cv-hint' }, `${state.data.season} champion: `, el('b', {}, state.data.champion), '.'));
  }
}
