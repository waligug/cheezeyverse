/* me.html - your own players: ratings, banked points, the spend form, request history,
   and the declare button.

   Spending here does not change a rating. It files upgrade_requests rows, which the
   database prices itself and the local commissioner app applies to the save file during
   a Sim Week. Until then the cost is "reserved": it is subtracted from what you can spend
   again, so you cannot queue more than you have. */

import {
  RATINGS, POTENTIAL_RATINGS, RATING_LABELS, RATING_MAX, START_AGE, GROWTH_END_AGE,
  nextPointCost, hasPotential, isLocked, describeCurve, biasedUpgradeCost, biasFor,
  classify, growthCurve, formatHeight, expectedAdultHeight, declareWarning,
  ratingCeiling, potentialCeiling,
} from './rules.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, settings,
  myCharacters, requestsFor, ledgerFor, requestUpgrade, cancelRequest,
  declareForDraft, deletePendingCharacter, LEAGUE_LABELS, errorText,
  oauthErrorFromUrl, pointsPerWeek,
} from './supabase.js';
import { $, classLine, clear, describeCharacter, el, fmtDate, freshJSON, goalLine, money, note, positionLine, renderChrome, renderFooter, renderMeter, renderSheet, renderTraitBars, setupNeededNote, showNote, statusPill, tabs } from './ui.js';

const chrome = renderChrome({
  active: 'me.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

$('#signin').addEventListener('click', () => signIn().catch(
  (e) => showNote($('#notices'), 'bad', errorText(e))));

/** rendering state, one entry per character id */
const drafts = new Map();

/* An OAuth failure comes back in the URL, not as an exception - see oauthErrorFromUrl().
   Read it before anything else so the page can say what happened instead of just looking
   signed out. This runs even when the Supabase client is never constructed. */
const cvOauthError = oauthErrorFromUrl();
if (cvOauthError) showNote($('#notices'), 'bad', `Discord sign-in failed: ${cvOauthError}`);

if (!isConfigured()) {
  $('#notices').append(setupNeededNote());
} else {
  boot().catch((err) => {
    // The spinner lives inside #characters and is only ever cleared by a successful load(),
    // so without this a failure leaves "Loading your players..." turning for ever beside the
    // error message.
    const spinner = $('#loading');
    if (spinner) spinner.hidden = true;
    showNote($('#notices'), 'bad', errorText(err));
  });
}

async function boot() {
  const user = await currentUser();
  const profile = user ? await ensureProfile() : null;
  chrome.refresh(user, profile);
  if (!user) { $('#gate').hidden = false; return; }
  $('#loading').hidden = false;
  await load();
}

/**
 * Re-read everything WITHOUT throwing the reader back to the top of the page.
 *
 * `load()` clears #characters and rebuilds it, so for a moment the document is short and the
 * browser clamps the scroll position upward - and filing a spend then also called
 * window.scrollTo({top: 0}) on purpose, to put the confirmation banner in view. Between them,
 * every point request bounced you to the top of your own page, which on a phone means scrolling
 * back down past two other characters to carry on where you were.
 *
 * The position is restored AFTER the rebuild rather than held during it, because the new
 * content has to exist before the page is tall enough to scroll back to. If the page really did
 * get shorter the browser clamps it, which is the correct outcome.
 */
async function loadKeepingPlace() {
  const y = window.scrollY;
  await load();
  // The two-argument form, not {behavior}: it is instant everywhere and cannot be turned into
  // an animation by a stylesheet's scroll-behavior, which is the whole point of not moving.
  if (y) window.scrollTo(0, y);
}


async function load() {
  const cfg = await settings(true);
  const characters = await myCharacters();
  const ids = characters.map((c) => c.id);
  const [requests, ledger] = await Promise.all([requestsFor(ids), ledgerFor(ids)]);

  const box = $('#characters');
  clear(box);

  // Income scales with level, so a single number is wrong once anybody is promoted - and
  // saying so is the point: it tells a friend in prep that the climb is worth something.
  const rates = ['prep', 'college', 'pro'].map((k) => [k, pointsPerWeek(cfg, k)]);
  const flat = rates.every(([, n]) => n === rates[0][1]);
  const income = flat
    ? `${rates[0][1]} point${rates[0][1] === 1 ? '' : 's'} per simulated week`
    : rates.map(([k, n]) => `${n} a week in ${LEAGUE_LABELS[k] || k}`).join(', ');
  // The cost curve used to be dumped into this sentence. On a phone that made the first thing
  // you read a wall of numbers about prices, before you had even seen your player. It belongs
  // next to the buttons that charge them, which is the Spend tab.
  $('#intro').textContent = `You get ${income}, `
    + `and you can hold up to ${cfg.max_characters} live characters.`;

  if (!characters.length) {
    box.append(el('div', { class: 'cv-card' },
      el('h2', {}, 'No players yet'),
      el('p', {}, 'Make one and he starts at 14 in the Prep league.'),
      el('p', { class: 'cv-actions' },
        el('a', { class: 'cv-btn cv-big', href: 'create.html' }, 'Create your player'))));
    return;
  }

  for (const character of characters) {
    box.append(renderCharacter(
      character,
      requests.filter((r) => r.character_id === character.id),
      ledger.filter((l) => l.character_id === character.id),
      currentAge(character, cfg.current_season),
      cfg.current_season,
    ));
  }
}

/* ------------------------------------------------------------------------ one player */

function reservedCost(requests) {
  return requests
    .filter((r) => r.status === 'pending' || r.status === 'approved')
    .reduce((sum, r) => sum + Number(r.cost || 0), 0);
}

function draftFor(character) {
  let draft = drafts.get(character.id);
  if (!draft || draft.stamp !== stampOf(character)) {
    draft = {
      stamp: stampOf(character),
      ratings: { ...(character.ratings || {}) },
      potentials: { ...(character.potentials || {}) },
    };
    drafts.set(character.id, draft);
  }
  return draft;
}

function stampOf(c) {
  // growth_bias belongs in here: it is what prices a queued step, so a draft cached
  // against an old bias would quote the wrong number after the commissioner corrects one.
  return `${c.points_available}:${JSON.stringify(c.ratings)}:${JSON.stringify(c.potentials)}`
    + `:${JSON.stringify(c.growth_bias)}`;
}

/* `currentSeason` is a parameter rather than a closed-over `cfg`: this function is declared at
   module level, so the `const cfg` inside load() is not in scope here and reading it threw a
   ReferenceError that killed every card before it rendered. */
/* -------------------------------------------------------------------------- the season

   Counting stats and where they place, read from the file the publish step writes beside each
   league's pages. The browser deliberately does NOT parse the game's HTML: the export has four
   traps in it - an undefeated team whose percentage reads 1.000, a Season Totals header that
   repeats STL so BLK is the seventeenth number, 3PM/3PA in that header that make "find the
   numbers" read every column one place out, and draft-pool pages carrying season lines earned
   in another league. All four are handled and tested in commissioner/seasonbonus.py, and
   re-implementing them here in JavaScript is how the two copies start disagreeing.

   Everything here is page-local rather than in ui.js, on purpose. ui.js is shared by every page
   and carries its own ten-minute cache entry, so a NEW named export is a hard failure for any
   visitor holding the old copy: a missing named import does not read as undefined, it refuses
   to load the module and blanks the page. Adding to the page's own file cannot skew that way. */

const STATS = new Map();

function leagueStats(league) {
  if (!league) return Promise.resolve(null);
  if (!STATS.has(league)) {
    STATS.set(league, freshJSON(`leagues/${league}/stats.json`));
  }
  return STATS.get(league);
}

/** Fuller is better: rank 1 of 184 fills the bar, last empties it. */
function rankFill(rank, count) {
  if (!(count > 1)) return 100;
  return Math.max(0, Math.min(100, ((count - rank) / (count - 1)) * 100));
}

function seasonPanel(character) {
  const box = el('div', {}, el('p', { class: 'cv-muted' }, 'Looking up his season...'));
  leagueStats(character.league).then((stats) => {
    clear(box);
    const name = `${character.first_name} ${character.last_name}`;
    const row = stats && (stats.players || []).find((p) => p.name === name);
    if (!row) {
      box.append(note(null, stats
        ? 'No season line for him yet. A player gets one once he has played a game, and the '
          + 'file is rewritten every time the commissioner publishes.'
        : 'The league has not published its stats yet. This fills in after the next Sim Week.'));
      return;
    }

    const games = Number(row.G) || 0;
    const per = (v) => (games ? (Number(v) / games).toFixed(1) : '0.0');
    box.append(el('p', { class: 'cv-muted' },
      `${games} game${games === 1 ? '' : 's'} for ${row.team}`
      + (row.MIN ? ` - ${per(row.MIN)} minutes a game` : '')));

    // the counting stats, total and per game
    const table = el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, ''), ...stats.categories.map((c) => el('th', {}, c)))),
      el('tbody', {},
        el('tr', {}, el('th', {}, 'total'),
          ...stats.categories.map((c) => el('td', {}, String(row[c] ?? 0)))),
        el('tr', {}, el('th', {}, 'per game'),
          ...stats.categories.map((c) => el('td', {}, per(row[c] ?? 0))))));
    box.append(table);

    // where that puts him, against the same two lines the season bonus pays on
    box.append(el('h4', { class: 'cv-subhead' }, `Where that puts him in ${stats.count} players`));
    const bars = el('div', { class: 'cv-traits cv-ranks' });
    for (const cat of stats.categories) {
      const rank = Number((row.rank || {})[cat]) || stats.count;
      const bar = el('span', { class: 'cv-bar' },
        el('i', { style: `width:${rankFill(rank, stats.count)}%` }));
      // the two thresholds the bonus actually pays on, so the bar explains the payout
      for (const mark of [25, stats.elite_rank]) {
        if (mark < stats.count) {
          bar.append(el('u', {
            style: `left:${rankFill(mark, stats.count)}%`,
            title: mark === 25 ? 'top 25 pays a point' : 'the elite line',
          }));
        }
      }
      bars.append(el('div', { class: 'cv-trait' },
        el('span', { class: 'cv-trait-name' }, cat),
        bar,
        el('span', { class: 'cv-trait-val' }, ordinal(rank)),
        el('span', { class: 'cv-trait-blurb' },
          `${row[cat] ?? 0} against ${stats.elite[cat]} for the league's fifth best`)));
    }
    box.append(bars);
    box.append(el('p', { class: 'cv-hint' },
      'The two ticks on each bar are the lines the end-of-season bonus pays on: the top 25, '
      + `and the ${ordinal(stats.elite_rank)} best total in the league.`));
    if (stats.export_date) {
      box.append(el('p', { class: 'cv-muted' }, `From the league export of ${stats.export_date}.`));
    }
  });
  return box;
}

function ordinal(n) {
  const v = Number(n) || 0;
  const tens = v % 100;
  if (tens >= 11 && tens <= 13) return `${v}th`;
  return `${v}${['th', 'st', 'nd', 'rd'][v % 10] || 'th'}`;
}

function renderCharacter(character, requests, ledger, age, currentSeason) {
  const card = el('section', { class: 'cv-card' });
  const klass = classify(character.ratings || {}, character.position);
  const bias = character.growth_bias || {};
  const reserved = reservedCost(requests);

  card.append(el('div', { class: 'cv-card-head' },
    el('h2', {},
      (character.jersey_preference === null || character.jersey_preference === undefined
        ? '' : `#${character.jersey_preference} `)
      + `${character.first_name} ${character.last_name}`),
    statusPill(character.status),
    el('span', { class: 'cv-pill' }, LEAGUE_LABELS[character.league] || character.league)));

  card.append(el('p', { class: 'cv-muted' },
    `${describeCharacter(character, currentSeason)}`
    + (character.hometown ? ` · ${character.hometown}` : '')
    + (character.game_dob ? ` · born ${fmtDate(character.game_dob)} in game` : '')));

  card.append(el('p', { class: 'cv-actions' },
    el('a', { class: 'cv-btn cv-small cv-ghost', href: `career.html?id=${encodeURIComponent(character.id)}` },
      'His whole career'),
    el('span', { class: 'cv-muted' },
      'prep, college and pro on one page, with the growth and the spending')));

  /* ---- who he is, folded away ---------------------------------------------------------
     This is the nicest writing on the site and on a phone it was in the way: about 600px of
     class readout, position note, career goal and height projection sat between the card's
     header and the first thing you can press. It does not change week to week, so it does not
     need to be open week to week - but the headline does the summarising, so what you give up
     by leaving it shut is nothing.

     <details> rather than a button: it opens without JavaScript, the browser handles the
     keyboard and screen-reader behaviour, and find-in-page still reaches the text inside. */
  const goal = goalLine(character.career_goal);
  const about = el('details', { class: 'cv-fold' },
    el('summary', {}, classLine(klass)),
    el('div', { class: 'cv-fold-body' },
      el('p', {}, klass.blurb),
      el('p', { class: 'cv-muted' }, `Second closest: ${klass.runnerUp.label}. `
        + 'It is a label, not a cage - spend differently and it changes.'),
      el('p', { class: 'cv-muted' }, positionLine(character)),
      goal ? el('p', {}, el('b', {}, 'What he wants: '), goal) : null,
      heightBlock(character, age)));
  card.append(about);

  if (character.status === 'pending') {
    card.append(note(null, 'Waiting for a roster spot. The commissioner drops pending '
      + 'players into the Prep save at the next sim; you can still spend points now.'));
  }

  const body = el('div', { class: 'cv-stack' });
  card.append(body);

  /* ---- spend ---- */
  const meter = el('div', { class: 'cv-meter cv-cheese' });
  const sheet = el('div', {});
  const problems = el('div', {});
  const actions = el('div', { class: 'cv-actions' });

  const draft = draftFor(character);
  const base = { ratings: character.ratings || {}, potentials: character.potentials || {} };

  const queuedCost = () => {
    let total = 0;
    for (const r of RATINGS) {
      const from = Number(base.ratings[r] ?? 0);
      const to = Number(draft.ratings[r] ?? from);
      if (to > from) total += biasedUpgradeCost(from, to - from, 'rating', biasFor(bias, r));
    }
    for (const r of POTENTIAL_RATINGS) {
      const from = Number(base.potentials[r] ?? 0);
      const to = Number(draft.potentials[r] ?? from);
      if (to > from) total += biasedUpgradeCost(from, to - from, 'potential', biasFor(bias, r));
    }
    return total;
  };

  const freePoints = () => Number(character.points_available || 0) - reserved - queuedCost();

  function step(rating, kind, dir) {
    if (isLocked(rating)) return;
    const bag = kind === 'potential' ? draft.potentials : draft.ratings;
    const floor = Number((kind === 'potential' ? base.potentials : base.ratings)[rating] ?? 0);
    const value = Number(bag[rating] ?? floor);
    if (dir > 0) {
      // The shared rule, not a second copy of it: a potential-bearing rating is capped by its
      // potential up to POTENTIAL_MAX, and a potential by POTENTIAL_MAX itself.
      const ceiling = kind === 'potential'
        ? potentialCeiling()
        : ratingCeiling(rating, draft.potentials, draft.traits);
      if (value >= ceiling) return;
      if (nextPointCost(value, kind, biasFor(bias, rating)) > freePoints()) return;
      bag[rating] = value + 1;
    } else {
      if (value <= floor) return;
      if (kind === 'potential' && value - 1 < Number(draft.ratings[rating])) {
        showNote(problems, 'bad',
          `Take ${RATING_LABELS[rating]} itself back down first - a rating cannot sit above its potential.`);
        return;
      }
      bag[rating] = value - 1;
    }
    drawSpend();
  }

  function drawSpend() {
    const queued = queuedCost();
    // budget/spent/label are the OLD renderMeter's argument names, sent alongside the new
    // ones on purpose. ui.js is shared by every page and carries its own 10-minute cache
    // entry, so somebody who was just on the roll call has ui.js cached while me.html and
    // page-me.js come down fresh - a new caller meeting an old renderMeter, which read
    // undefined for both numbers and rendered "undefined POINTS LEFT undefined SPENT".
    // Caught on the live site. Costs three keys; remove them once nobody can still be
    // holding the September 19 ui.js, which in practice is the next time this file changes.
    renderMeter(meter, {
      free: freePoints(), reserved, queued,
      budget: freePoints(), spent: queued,
      label: reserved ? `${reserved} already asked for` : klass.label,
    });
    renderSheet(sheet, {
      base,
      bias,
      ratings: draft.ratings,
      potentials: draft.potentials,
      budget: freePoints(),
      showPotentials: true,
    }, step);
    clear(actions);
    const send = el('button', {
      class: 'cv-btn', type: 'button', disabled: queued === 0,
      onclick: () => sendRequests(character, base, draft, send, problems),
    }, queued ? `Ask for these ${queued} point${queued === 1 ? '' : 's'}` : 'Nothing queued yet');
    const undo = el('button', {
      class: 'cv-btn cv-ghost', type: 'button', disabled: queued === 0,
      onclick: () => { drafts.delete(character.id); draftFor(character); load(); },
    }, 'Undo');
    actions.append(send, undo);
  }

  /* ---- the card's sections, behind tabs ----------------------------------------------
     Measured on a 390px screen before this: the page was 11,571px tall for two characters,
     about thirty phone screens, because every section of every card was open at once and the
     spend sheet alone was 87% of each card. All of it is worth having; all of it at once is
     not. Spend is first because it is the only part anybody comes here to DO. */
  let spendPanel = null;
  if (character.status === 'retired') {
    body.append(note(null, 'Retired. His sheet is frozen.'));
  } else {
    spendPanel = el('div', {},
      meter,
      el('p', { class: 'cv-hint' },
        `What a step costs: ${describeCurve()}.`),
      el('p', { class: 'cv-hint' },
        'Nothing changes until the commissioner applies it during a Sim Week. '
        + 'Queued points are held back so you cannot promise the same point twice.'),
      sheet, problems, actions);
  }

  let traitsPanel = null;
  if (character.traits && Object.keys(character.traits).length) {
    const traits = el('div', { class: 'cv-traits' });
    renderTraitBars(traits, character.traits);
    traitsPanel = el('div', {}, el('p', { class: 'cv-hint' },
      'What the quiz decided about him. These do not change.'), traits);
  }

  const sections = [
    { label: 'Spend', node: spendPanel },
    { label: 'Season', node: character.status === 'pending' ? null : seasonPanel(character) },
    { label: 'Him', node: traitsPanel },
    { label: 'Requests', node: el('div', {}, requestTable(requests)),
      badge: requests.filter((r) => r.status === 'pending' || r.status === 'approved').length || null },
    { label: 'Points', node: el('div', {}, ledgerTable(ledger, character)) },
    { label: 'Money', node: moneyPanel(character) },
  ].filter((x) => x.node);

  body.append(tabs(sections), ...sections.map((x) => x.node));
  // after the sheet is in the document: drawSpend measures what it renders into
  if (spendPanel) drawSpend();

  /* ---- big buttons ---- */
  const footer = el('div', { class: 'cv-actions' });
  if (character.status === 'active' && character.league !== 'pro') {
    const target = character.league === 'prep' ? 'the college draft' : 'the pro draft';
    footer.append(el('button', {
      class: 'cv-btn', type: 'button',
      onclick: (e) => declare(character, target, e.currentTarget, problems),
    }, `Declare for ${target}`));
    footer.append(el('span', { class: 'cv-muted' },
      'One way. He leaves this league at the next offseason.'));
  }
  if (character.status === 'pending') {
    footer.append(el('button', {
      class: 'cv-btn cv-ghost', type: 'button',
      onclick: (e) => scrap(character, e.currentTarget),
    }, 'Delete and start over'));
  }
  if (footer.childNodes.length) body.append(footer);

  return card;
}

/**
 * How old he is in game years. `height_inches` on the row holds his current recorded height.
 *
 * A pending character has no game_dob yet, so he is fourteen.
 */
function currentAge(character, currentSeason) {
  if (!character.game_dob) return START_AGE;
  const born = new Date(character.game_dob).getFullYear();
  const age = Number(currentSeason) - born;
  if (!Number.isFinite(age)) return START_AGE;
  return Math.max(START_AGE, Math.min(GROWTH_END_AGE, age));
}

/**
 * Growth SO FAR, and where it is heading.
 *
 * The curve is recomputed from the character's id, his height at fourteen and his
 * `height_genes` trait - exactly the three inputs commissioner/growth.py uses when it
 * writes the inches into league.dat, so this is a readout of the real thing rather than a
 * second opinion.
 *
 * Only the offseasons that have HAPPENED are shown. The rest of the curve exists and is
 * already decided, but printing it would hand back the one number the whole creation
 * redesign is built to withhold: nobody knows how tall a fifteen year old is going to be.
 * What is shown for the future is the stored expectation, which is a pure function of his
 * height at fourteen and his genes and was never a secret.
 */
function heightBlock(character, age) {
  return el('div', {}, el('p', {}, el('b', {}, `${formatHeight(character.height_inches)} now.`)),
    el('p', { class: 'cv-muted' },
      'Recorded height, updated after offseason growth. Future growth is not added to this number.'));
}

function requestTable(requests) {
  if (!requests.length) {
    return el('p', { class: 'cv-muted' }, 'Nothing asked for yet.');
  }
  const rows = requests.map((r) => el('tr', {},
    el('td', {}, RATING_LABELS[r.rating] || r.rating),
    el('td', {}, r.kind === 'potential' ? 'potential' : 'rating'),
    el('td', { class: 'cv-right' }, `+${r.delta}`),
    el('td', { class: 'cv-right' }, String(r.cost)),
    el('td', {}, el('span', { class: `cv-pill is-${pillFor(r.status)}` }, r.status)),
    el('td', {}, fmtDate(r.applied_at || r.requested_at)),
    el('td', {}, r.status === 'pending'
      ? el('button', {
        class: 'cv-btn cv-small cv-ghost', type: 'button',
        onclick: (e) => cancel(r, e.currentTarget),
      }, 'Cancel')
      : (r.note || ''))));

  return el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, 'Rating'), el('th', {}, 'Kind'), el('th', { class: 'cv-right' }, 'Points'),
        el('th', { class: 'cv-right' }, 'Cost'), el('th', {}, 'Status'), el('th', {}, 'When'),
        el('th', {}, ''))),
      el('tbody', {}, rows)));
}

function pillFor(status) {
  return { pending: 'pending', approved: 'active', applied: 'active', rejected: 'retired' }[status]
    || 'pending';
}

/**
 * What the GAME pays him, and what that is worth in skill points.
 *
 * READ, NEVER COMPUTED. The band is a percentile against his own league's salaries and
 * `points.payout_band` is the one implementation of that rule; the commissioner works it out
 * during the weekly sync and parks it on the open level_history row. Deriving it again here
 * would be a second copy that drifts the first time somebody tunes a band, and the page would
 * confidently promise a payout he does not get.
 *
 * Returns null when there is nothing honest to show, so the tab simply does not appear.
 */
function financesOf(character) {
  const history = character.level_history || [];
  for (let i = history.length - 1; i >= 0; i -= 1) {
    const row = history[i];
    if (row && typeof row === 'object' && row.to_season == null && row.finances) return row.finances;
  }
  return null;
}


function moneyPanel(character) {
  const f = financesOf(character);
  if (!f) return null;
  const rows = [];
  rows.push(el('tr', {}, el('td', {}, 'Salary'),
    el('td', { class: 'cv-right' }, f.salary ? money(f.salary) + ' a year' : 'no contract')));
  rows.push(el('tr', {}, el('td', {}, 'Years left'),
    el('td', { class: 'cv-right' }, String(f.years_left ?? 0))));
  if (f.band) {
    rows.push(el('tr', {}, el('td', {}, 'Where that sits'),
      el('td', { class: 'cv-right' }, f.band)));
  }
  if (f.payout != null) {
    rows.push(el('tr', {}, el('td', {}, 'Pays him next offseason'),
      el('td', { class: 'cv-right' }, '+' + f.payout + ' skill points')));
  }
  const table = el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' }, el('tbody', {}, rows)));

  const notes = [];
  if (!f.scale) {
    // THE HONEST EMPTY STATE. With Finances off every contract in the league is the same
    // placeholder, so there is no distribution to rank anybody against. Saying "league minimum"
    // here would be inventing a verdict out of one repeated number.
    notes.push(el('p', { class: 'cv-hint' },
      'His league has finances switched off, so every contract is the same placeholder and '
      + 'nobody can be ranked yet. When they are switched on, the game decides what he is '
      + 'worth and this is what pays him.'));
  } else {
    notes.push(el('p', { class: 'cv-hint' },
      'Paid once a season, on top of the points he earns every week. It is worked out from '
      + 'where his salary sits among everyone else in his league, not from a fixed amount - so '
      + "it moves as the league's money moves."));
    if (f.league_median) {
      notes.push(el('p', { class: 'cv-muted' },
        'The middle of his league earns ' + money(f.league_median) + ' a year.'));
    }
  }
  if (f.years_left === 1) {
    notes.push(el('p', { class: 'cv-hint' },
      'His deal is up at the end of this season. He goes into free agency with everybody '
      + 'else and gets paid what the league thinks he is worth - which is the point of '
      + 'a short deal, not a problem with it.'));
  }
  if (!f.salary) {
    notes.push(el('p', { class: 'cv-hint' },
      'He has no contract on file. That is worth fixing before finances go on.'));
  }
  return el('div', {}, table, ...notes);
}

function ledgerTable(ledger, character) {
  const head = el('p', { class: 'cv-muted' },
    `${character.points_available} available · ${character.points_spent} spent all time`);
  if (!ledger.length) return el('div', {}, head);
  const rows = ledger.slice(0, 25).map((l) => el('tr', {},
    el('td', {}, fmtDate(l.created_at)),
    el('td', {}, l.reason),
    el('td', { class: 'cv-right' }, (l.amount > 0 ? '+' : '') + l.amount)));
  return el('div', {}, head, el('div', { class: 'cv-scroll' },
    el('table', { class: 'cv-table' },
      el('thead', {}, el('tr', {}, el('th', {}, 'When'), el('th', {}, 'What'),
        el('th', { class: 'cv-right' }, 'Points'))),
      el('tbody', {}, rows))));
}

/* --------------------------------------------------------------------------- actions */

async function sendRequests(character, base, draft, button, problems) {
  button.disabled = true;
  button.textContent = 'Sending...';
  clear(problems);
  const wanted = [];
  for (const r of RATINGS) {
    const from = Number(base.ratings[r] ?? 0);
    const to = Number(draft.ratings[r] ?? from);
    if (to > from) wanted.push({ rating: r, delta: to - from, kind: 'rating' });
  }
  for (const r of POTENTIAL_RATINGS) {
    const from = Number(base.potentials[r] ?? 0);
    const to = Number(draft.potentials[r] ?? from);
    if (to > from) wanted.push({ rating: r, delta: to - from, kind: 'potential' });
  }
  // potentials first: raising a potential is what makes room for the rating underneath it
  wanted.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'potential' ? -1 : 1));

  const sent = [];
  try {
    for (const w of wanted) {
      // one at a time: the database prices each request against the ones already queued
      /* eslint-disable no-await-in-loop */
      sent.push(await requestUpgrade({ characterId: character.id, ...w }));
    }
    drafts.delete(character.id);
    showNote($('#notices'), 'good',
      `${sent.length} request${sent.length === 1 ? '' : 's'} filed for `
      + `${character.first_name} ${character.last_name}. `
      + 'The commissioner applies them at the next Sim Week.');
    // The banner goes up BEFORE the rebuild and the reload comes last, because load() replaces
    // every card - `button` is a detached node afterwards and writing to it shows nobody
    // anything. What the reader actually sees in place is the card itself coming back with the
    // points spent and the new rows under Requests, which is the confirmation that matters.
    await loadKeepingPlace();
  } catch (err) {
    showNote(problems, 'bad', errorText(err)
      + (sent.length ? ` (${sent.length} went through before this one)` : ''));
    button.disabled = false;
    button.textContent = 'Try again';
    if (sent.length) await loadKeepingPlace();
  }
}

async function cancel(request, button) {
  button.disabled = true;
  try {
    await cancelRequest(request.id);
    await loadKeepingPlace();
  } catch (err) {
    showNote($('#notices'), 'bad', errorText(err));
    button.disabled = false;
  }
}

async function declare(character, target, button, problems) {
  // Never let someone spend three years of a career on a confirm dialog that does not say
  // what it costs.
  const warn = declareWarning(character, character.college_years);
  const name = `${character.first_name} ${character.last_name}`;
  if (!window.confirm(`Declare ${name} for ${target}?

${warn.headline}

${warn.detail}

`
    + 'This cannot be undone.')) return;
  button.disabled = true;
  try {
    await declareForDraft(character.id);
    await load();
    showNote($('#notices'), 'good',
      `${character.first_name} ${character.last_name} has declared for ${target}.`);
  } catch (err) {
    showNote(problems, 'bad', errorText(err));
    button.disabled = false;
  }
}

async function scrap(character, button) {
  if (!window.confirm(`Delete ${character.first_name} ${character.last_name}? `
    + 'He has not been put on a roster yet, so nothing is lost but the points you spent on him.')) return;
  button.disabled = true;
  try {
    await deletePendingCharacter(character.id);
    drafts.delete(character.id);
    await load();
  } catch (err) {
    showNote($('#notices'), 'bad', errorText(err));
    button.disabled = false;
  }
}
