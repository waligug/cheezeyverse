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
  $, el, clear, renderChrome, renderFooter, setupNeededNote, showNote, note, freshJSON,
} from './ui.js';

const chrome = renderChrome({
  active: 'h2h.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const LEAGUES = ['prep', 'college', 'pro'];
// A line archived before games.json carried a season was played in the universe's first and
// only one. Kept in step with UNSTAMPED_SEASON in commissioner/gamesarchive.py.
const UNSTAMPED_SEASON = 2026;
const DATA = new Map();
let people = [];          // pickable: [{id, name, league, games}], he has games on record
let absent = [];          // on record but with nothing to compare, and the reason why
let left = null;
let right = null;

function leagueGames(league) {
  if (!DATA.has(league)) {
    DATA.set(league, freshJSON(`leagues/${league}/games.json`));
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

/* WHY A CHARACTER HAS NO GAMES. Two different things, and they must not be run together.
 *
 * `since_source: "unknown"` means the commissioner could not work out which day he arrived, so
 * it could not tell his games from those of the dormant filler whose roster slot he was renamed
 * into - the history stays under the one player id. Rather than hand him a stranger's season it
 * publishes none of them. That is a gap in the record somebody can go and fix.
 *
 * An arrival day with no games after it is just a man who has not played yet. Nothing is wrong.
 *
 * Silence was the old behaviour for both: he simply was not in the dropdown, with no way to tell
 * whether he had never played or whether his games had been withheld. */
function whyAbsent(p) {
  if (p.since_source === 'unknown') {
    return `${p.name} — the day he joined was never recorded, so his games cannot be told apart `
      + 'from those of the player whose slot he took. None are shown, rather than credit him '
      + 'with somebody else’s nights.';
  }
  if (p.since_day) {
    return `${p.name} — joined on day ${p.since_day} and has not played since.`;
  }
  return `${p.name} — no games on record yet.`;
}

function picker(id, value, onchange) {
  const sel = el('select', { class: 'cv-select', id, onchange: (e) => onchange(e.target.value) });
  sel.append(el('option', { value: '' }, 'Pick a player...'));
  for (const p of people) {
    // the character id, not the display name. Two characters can share a name - they are named
    // by seven different people who have never coordinated - and a rename would break every
    // link anybody had shared.
    sel.append(el('option', { value: p.id, selected: p.id === value || null },
      `${p.name} (${LEAGUE_LABELS[p.league] || p.league})`));
  }
  return sel;
}

function renderPickers() {
  const box = $('#pickers');
  clear(box);
  if (!people.length) {
    // "nothing published yet" and "published, but none of it could be attributed" look identical
    // on an empty page and are not the same problem at all.
    box.append(absent.length
      ? note(null, 'Nobody has games that can be compared yet:', absent.map(whyAbsent))
      : note(null, 'No game-by-game data published yet. It is written by the '
        + 'commissioner after each Sim Week, from the game’s own export.'));
    return;
  }
  box.append(el('div', { class: 'cv-fields' },
    el('label', {}, el('span', {}, 'Player one'), picker('one', left, (v) => { left = v; draw(); })),
    el('label', {}, el('span', {}, 'Player two'), picker('two', right, (v) => { right = v; draw(); }))));
  if (absent.length) {
    box.append(note(null, absent.length === 1 ? 'One player is not in the lists:'
      : `${absent.length} players are not in the lists:`, absent.map(whyAbsent)));
  }
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
  // Keep the address bar holding the current pair. With names in the URL somebody could type
  // one; ids are opaque, so if the page does not write the link there is no way to share a
  // comparison - and sharing it is the entire point of a head-to-head table.
  try {
    const url = new URL(window.location.href);
    if (left && right) {
      url.searchParams.set('one', left);
      url.searchParams.set('two', right);
    } else {
      url.searchParams.delete('one');
      url.searchParams.delete('two');
    }
    window.history.replaceState(null, '', url);
  } catch (err) { /* a browser that dislikes replaceState must not lose the page */ }
  if (!left || !right) return;
  if (left === right) {
    box.append(note(null, 'Pick two different players.'));
    return;
  }
  const one = people.find((p) => p.id === left);
  const two = people.find((p) => p.id === right);
  // A shared link can name somebody with no games - or nobody at all. Name WHICH of the two, and
  // why: "not on record" and "on record with his games withheld" are different problems, and only
  // one of them is anybody's to fix. The old text guessed at both in one sentence.
  if (!one || !two) {
    const why = [[left, one], [right, two]].filter(([, found]) => !found).map(([id]) => {
      const known = absent.find((p) => p.id === id);
      return known ? whyAbsent(known)
        : `${id} — not on record. He may have retired, or the link may be an old one.`;
    });
    box.append(note(null, 'That link points at somebody with no games to compare. Pick two from '
      + 'the lists above.', why));
    return;
  }

  if (one.league !== two.league) {
    box.append(note(null, `${one.name} is in ${LEAGUE_LABELS[one.league] || one.league} and `
      + `${two.name} is in ${LEAGUE_LABELS[two.league] || two.league}. The three leagues are `
      + 'separate saves and never play each other, so there is nothing to compare.'));
    return;
  }

  // A MEETING IS BOTH OF THESE: they played on the same day, AND they played EACH OTHER. The
  // first version checked only the day, so any two characters who both had a game that night
  // counted as having met - and since prep plays most nights, that made almost every night a
  // meeting. Live, it showed Chris and Tim "4 meetings, Tim leads 2-1" when they have never
  // met once, attributing to Tim a game Chris lost to the Tulips. 51 meetings shown against 15
  // real. Teammates would have "met" every single night.
  //
  // The comment that was here said "their teams meeting is not enough", which was true and was
  // describing a refinement of a check that had never been written.
  //
  // AND IN THE SAME SEASON. games.json now carries several: the game's own export holds one
  // season and is wiped by the rollover, so the history is archived per season and republished
  // together. Day numbers restart at 1 every year, so day 5 of 2026 and day 5 of 2027 are
  // different nights - keyed on the day alone they would look like one game, and two players
  // who never met could be credited with a meeting in a season one of them did not play.
  const nightOf = (g) => `${g.season ?? UNSTAMPED_SEASON}:${g.day}`;
  const mine = new Map(one.games.map((g) => [nightOf(g), g]));
  const met = (g) => {
    const m = mine.get(nightOf(g));
    return !!m && m.opp === g.team && g.opp === m.team;
  };
  const played = new Set(two.games.filter(met).map(nightOf));
  // Which seasons those meetings fall in. The games table names the season only when there is
  // more than one, so a universe that has played a single year does not carry a column of the
  // same number repeated down it.
  const seasons = new Set([...played].map((n) => n.split(':')[0]));

  if (!played.size) {
    // Checked BOTH ways round. Asking only whether one man's opponents include the other's
    // current team gave different answers depending on which picker you put him in - and it is
    // his CURRENT team, so a character who has moved reads as never having played his old one.
    const teamsMet = one.games.some((g) => two.games.some((h) => nightOf(h) === nightOf(g)
      && g.opp === h.team && h.opp === g.team));
    box.append(note(null, teamsMet
      ? `${one.name} and ${two.name} have not been on the floor at the same time yet - their `
        + 'teams have met, but not with both of them playing.'
      : `${one.name} and ${two.name} have not met yet. The schedule will bring them round.`));
    return;
  }

  const aGames = one.games.filter((g) => played.has(nightOf(g)));
  const bGames = two.games.filter((g) => played.has(nightOf(g)));
  const a = total(aGames);
  const b = total(bGames);

  const card = el('section', { class: 'cv-card' });
  card.append(el('div', { class: 'cv-card-head' },
    el('h2', {}, `${one.name} v ${two.name}`),
    el('span', { class: 'cv-muted' },
      `${played.size} meeting${played.size === 1 ? '' : 's'}`)));

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
    + 'an average of each night’s percentage - 1-for-1 and 0-for-10 is 9%, not 50%. '
    + 'A game either of them missed is not counted at all, so this record can differ from '
    + 'how often their teams have met.'));

  // the games themselves, so the averages are checkable rather than asserted
  const list = el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'When'), el('th', {}, 'Result'),
        el('th', {}, one.name), el('th', {}, two.name))),
      // sorted by season first, then day - a plain day sort would interleave the years
      el('tbody', {}, ...aGames.slice().sort((x, y) => (x.season ?? UNSTAMPED_SEASON)
        - (y.season ?? UNSTAMPED_SEASON) || x.day - y.day).map((ga) => {
        const gb = bGames.find((g) => nightOf(g) === nightOf(ga));
        return el('tr', {},
          // the season only when there is more than one to tell apart
          el('td', {}, seasons.size > 1 ? `${ga.season ?? UNSTAMPED_SEASON}, day ${ga.day}`
            : `Day ${ga.day}`),
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
  absent = [];
  loaded.forEach((data, i) => {
    if (!data) return;
    for (const c of data.characters || []) {
      // A man with no games must NOT go in the pickable list. Picked, he would fall through to
      // the empty-days branch and be told "they have not met yet - the schedule will bring them
      // round", which for an unattributed character is simply false: no schedule will produce
      // games that were deliberately withheld. He goes in `absent` and gets the real reason.
      const where = (c.games && c.games.length) ? people : absent;
      where.push({ ...c, league: LEAGUES[i] });
    }
  });
  people.sort((x, y) => x.name.localeCompare(y.name));
  absent.sort((x, y) => x.name.localeCompare(y.name));

  const note0 = $('#league-note');
  if (note0) {
    note0.textContent = people.length
      ? `${people.length} player${people.length === 1 ? '' : 's'} with games on record`
      : '';
  }
  renderPickers();

  // ?one=<id>&two=<id> makes a comparison linkable, which is the whole point of an argument
  // settler. Ids rather than names, so a rename does not break every link already shared.
  const params = new URLSearchParams(window.location.search);
  // both lists: a name-style link to somebody with no games should still resolve to him, so the
  // page can say what is wrong with him instead of echoing the raw name back as a stranger
  const everyone = [...people, ...absent];
  const byId = new Map(everyone.map((p) => [p.id, p]));
  const resolve = (v) => {
    if (!v) return null;
    if (byId.has(v)) return v;
    // old links used names; honour them rather than showing a stranger an error
    const match = everyone.find((p) => p.name === v);
    return match ? match.id : v;
  };
  left = resolve(params.get('one'));
  right = resolve(params.get('two'));
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
