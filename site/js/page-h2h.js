/* h2h.html - pick two characters, see how they do against each other.
 *
 * WHAT "AGAINST EACH OTHER" MEANS HERE. Only games both of them actually played: the days that
 * appear in BOTH their game lists. Their teams meeting is not enough - one of them can be out,
 * and a night he did not play tells you nothing about how he does against the other man.
 *
 * PERCENTAGES ARE SUMMED, NEVER AVERAGED. Shooting 1-for-1 and 0-for-10 is 9% over the two
 * games, not the 50% you get by averaging 100% and 0%. Every percentage here divides total
 * makes by total attempts, and says so.
 *
 * Reads leagues/<key>/games.json, written by the commissioner from FBPB3's own MDB export -
 * the only place per-game stats exist, since the game saves no box scores.
 */

import {
  isConfigured, settings, errorText, signIn, signOut, currentUser,
  ensureProfile, oauthErrorFromUrl, LEAGUE_LABELS,
} from './supabase.js';
import {
  $, el, clear, renderChrome, renderFooter, setupNeededNote, showNote, note,
} from './ui.js';

const chrome = renderChrome({
  active: 'h2h.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const LEAGUES = ['prep', 'college', 'pro'];
const DATA = new Map();
let people = [];          // [{name, league, games}]
let left = null;
let right = null;

function leagueGames(league) {
  if (!DATA.has(league)) {
    DATA.set(league, fetch(`leagues/${league}/games.json`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null));
  }
  return DATA.get(league);
}

/** Sum a list of game lines into one total. */
function total(games) {
  const out = { n: games.length, min: 0, pts: 0, reb: 0, oreb: 0, ast: 0, stl: 0, blk: 0,
                to: 0, pf: 0, fgm: 0, fga: 0, ftm: 0, fta: 0, tpm: 0, tpa: 0, pm: 0, won: 0 };
  for (const g of games) {
    for (const k of Object.keys(out)) {
      if (k !== 'n' && k !== 'won') out[k] += Number(g[k]) || 0;
    }
    if (g.won) out.won += 1;
  }
  return out;
}

/** makes/attempts as a percentage, or an em dash when nobody shot. Summed, never averaged. */
function pct(makes, attempts) {
  if (!attempts) return '—';
  return (makes / attempts).toFixed(3).replace(/^0/, '');
}

/** The same true-shooting formula the at-a-glance panel uses: PTS / (2 * (FGA + 0.44 * FTA)). */
function trueShooting(t) {
  const attempts = t.fga + 0.44 * t.fta;
  if (attempts <= 0) return '—';
  return (t.pts / (2 * attempts)).toFixed(3).replace(/^0/, '');
}

const per = (v, n) => (n ? (v / n).toFixed(1) : '0.0');

function picker(id, value, onchange) {
  const sel = el('select', { class: 'cv-select', id, onchange: (e) => onchange(e.target.value) });
  sel.append(el('option', { value: '' }, 'Pick a player...'));
  for (const p of people) {
    sel.append(el('option', { value: p.name, selected: p.name === value || null },
      `${p.name} (${LEAGUE_LABELS[p.league] || p.league})`));
  }
  return sel;
}

function renderPickers() {
  const box = $('#pickers');
  clear(box);
  if (!people.length) {
    box.append(note(null, 'No game-by-game data published yet. It is written by the '
      + 'commissioner after each Sim Week, from the game’s own export.'));
    return;
  }
  box.append(el('div', { class: 'cv-fields' },
    el('label', {}, el('span', {}, 'Player one'), picker('one', left, (v) => { left = v; draw(); })),
    el('label', {}, el('span', {}, 'Player two'), picker('two', right, (v) => { right = v; draw(); }))));
}

function statRow(label, a, b, better) {
  // the better number is marked, because a table of twenty numbers with no emphasis is a table
  // nobody reads twice
  const aWins = better === 'a';
  const bWins = better === 'b';
  return el('tr', {},
    el('td', { class: aWins ? 'cv-h2h-win' : null }, a),
    el('th', {}, label),
    el('td', { class: bWins ? 'cv-h2h-win' : null }, b));
}

function compare(label, av, bv, digits = 1, higherIsBetter = true) {
  const a = Number(av);
  const b = Number(bv);
  let better = null;
  if (Number.isFinite(a) && Number.isFinite(b) && a !== b) {
    better = (a > b) === higherIsBetter ? 'a' : 'b';
  }
  return statRow(label, Number.isFinite(a) ? a.toFixed(digits) : av,
                 Number.isFinite(b) ? b.toFixed(digits) : bv, better);
}

function draw() {
  const box = $('#result');
  clear(box);
  if (!left || !right) return;
  if (left === right) {
    box.append(note(null, 'Pick two different players.'));
    return;
  }
  const one = people.find((p) => p.name === left);
  const two = people.find((p) => p.name === right);

  if (one.league !== two.league) {
    box.append(note(null, `${one.name} is in ${LEAGUE_LABELS[one.league] || one.league} and `
      + `${two.name} is in ${LEAGUE_LABELS[two.league] || two.league}. The three leagues are `
      + 'separate saves and never play each other, so there is nothing to compare.'));
    return;
  }

  // the days BOTH of them played. Their teams meeting is not enough: a night one of them sat
  // out says nothing about how he does against the other.
  const mine = new Map(one.games.map((g) => [g.day, g]));
  const days = two.games.filter((g) => mine.has(g.day)).map((g) => g.day).sort((x, y) => x - y);

  if (!days.length) {
    const met = one.games.some((g) => g.opp === two.team);
    box.append(note(null, met
      ? `${one.name} and ${two.name} have not been on the floor at the same time yet - their `
        + 'teams have met, but not with both of them playing.'
      : `${one.name} and ${two.name} have not met yet. The schedule will bring them round.`));
    return;
  }

  const aGames = one.games.filter((g) => days.includes(g.day));
  const bGames = two.games.filter((g) => days.includes(g.day));
  const a = total(aGames);
  const b = total(bGames);

  const card = el('section', { class: 'cv-card' });
  card.append(el('div', { class: 'cv-card-head' },
    el('h2', {}, `${one.name} v ${two.name}`),
    el('span', { class: 'cv-muted' },
      `${days.length} meeting${days.length === 1 ? '' : 's'}`)));

  // the headline: who has won more of them
  card.append(el('p', { class: 'cv-readout cv-cheese' },
    el('b', {}, a.won === b.won ? `All square, ${a.won}-${b.won}`
      : `${a.won > b.won ? one.name : two.name} leads ${Math.max(a.won, b.won)}-${Math.min(a.won, b.won)}`),
    el('span', {}, 'in the games they have both played in')));

  const rows = [
    compare('Minutes', per(a.min, a.n), per(b.min, b.n)),
    compare('Points', per(a.pts, a.n), per(b.pts, b.n)),
    compare('Rebounds', per(a.reb, a.n), per(b.reb, b.n)),
    compare('Assists', per(a.ast, a.n), per(b.ast, b.n)),
    compare('Steals', per(a.stl, a.n), per(b.stl, b.n)),
    compare('Blocks', per(a.blk, a.n), per(b.blk, b.n)),
    compare('Turnovers', per(a.to, a.n), per(b.to, b.n), 1, false),
    statRow('FG%', `${pct(a.fgm, a.fga)} (${a.fgm}/${a.fga})`,
            `${pct(b.fgm, b.fga)} (${b.fgm}/${b.fga})`, null),
    statRow('3P%', `${pct(a.tpm, a.tpa)} (${a.tpm}/${a.tpa})`,
            `${pct(b.tpm, b.tpa)} (${b.tpm}/${b.tpa})`, null),
    statRow('FT%', `${pct(a.ftm, a.fta)} (${a.ftm}/${a.fta})`,
            `${pct(b.ftm, b.fta)} (${b.ftm}/${b.fta})`, null),
    statRow('TS%', trueShooting(a), trueShooting(b), null),
    compare('Plus/minus', a.pm / a.n, b.pm / b.n),
  ];

  card.append(el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table cv-h2h' },
      el('thead', {}, el('tr', {},
        el('th', {}, one.name), el('th', {}, ''), el('th', {}, two.name))),
      el('tbody', {}, ...rows))));

  card.append(el('p', { class: 'cv-hint' },
    'Per game, over those meetings only. Percentages are total makes over total attempts, not '
    + 'an average of each night’s percentage - 1-for-1 and 0-for-10 is 9%, not 50%.'));

  // the games themselves, so the averages are checkable rather than asserted
  const list = el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'Day'), el('th', {}, 'Result'),
        el('th', {}, one.name), el('th', {}, two.name))),
      el('tbody', {}, ...days.map((day) => {
        const ga = aGames.find((g) => g.day === day);
        const gb = bGames.find((g) => g.day === day);
        return el('tr', {},
          el('td', {}, String(day)),
          // the TEAM won, not the man. "Dodger Manson by 2" reads as though he beat Chris
          // personally, when what happened is the Generals beat the Berries.
          el('th', {}, `${ga.won ? ga.team : gb.team} by `
            + `${Math.abs(ga.score[0] - ga.score[1])}`),
          el('td', {}, `${ga.pts} pts, ${ga.reb} reb, ${ga.ast} ast`),
          el('td', {}, `${gb.pts} pts, ${gb.reb} reb, ${gb.ast} ast`));
      }))));
  card.append(el('h3', { class: 'cv-subhead' }, 'The games'));
  card.append(list);
  box.append(card);
}

async function boot() {
  const user = await currentUser();
  const profile = user ? await ensureProfile() : null;
  chrome.refresh(user, profile);
  await settings();

  const loaded = await Promise.all(LEAGUES.map(leagueGames));
  people = [];
  loaded.forEach((data, i) => {
    if (!data) return;
    for (const c of data.characters || []) {
      if (c.games && c.games.length) people.push({ ...c, league: LEAGUES[i] });
    }
  });
  people.sort((x, y) => x.name.localeCompare(y.name));

  const note0 = $('#league-note');
  if (note0) {
    note0.textContent = people.length
      ? `${people.length} player${people.length === 1 ? '' : 's'} with games on record`
      : '';
  }
  renderPickers();

  // ?one=Chris+Zimmer&two=Dodger+Manson makes a comparison linkable, which is the whole point
  // of an argument settler
  const params = new URLSearchParams(window.location.search);
  left = params.get('one') || null;
  right = params.get('two') || null;
  if (left || right) {
    renderPickers();
    draw();
  }
}

const oauthError = oauthErrorFromUrl();
if (oauthError) showNote($('#notices'), 'bad', oauthError);

if (!isConfigured()) {
  $('#notices').append(setupNeededNote());
}
boot().catch((err) => showNote($('#notices'), 'bad', errorText(err)));
