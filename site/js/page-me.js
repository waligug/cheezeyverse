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
} from './rules.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, settings,
  myCharacters, requestsFor, ledgerFor, requestUpgrade, cancelRequest,
  declareForDraft, deletePendingCharacter, LEAGUE_LABELS, errorText,
  oauthErrorFromUrl, pointsPerWeek,
} from './supabase.js';
import {
  $, el, clear, renderChrome, renderFooter, setupNeededNote, showNote, note,
  statusPill, renderSheet, renderMeter, describeCharacter, fmtDate,
  renderTraitBars, classLine, positionLine, goalLine, tabs,
} from './ui.js';

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
      const ceiling = kind === 'potential'
        ? RATING_MAX
        : Math.min(RATING_MAX,
          hasPotential(rating) ? Number(draft.potentials[rating]) : RATING_MAX);
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
    renderMeter(meter, { free: freePoints(), reserved, queued });
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
    { label: 'Him', node: traitsPanel },
    { label: 'Requests', node: el('div', {}, requestTable(requests)),
      badge: requests.filter((r) => r.status === 'pending' || r.status === 'approved').length || null },
    { label: 'Points', node: el('div', {}, ledgerTable(ledger, character)) },
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
 * How old he is in game years. `height_inches` on the row is his height AT FOURTEEN and
 * never moves; the commissioner writes the grown inches into the save file, and this page
 * recomputes them from the same model. So the age is what decides how much of the curve
 * has actually happened.
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
  const genes = Number((character.traits || {}).height_genes);
  if (!Number.isFinite(genes)) {
    return el('p', { class: 'cv-muted' }, formatHeight(character.height_inches));
  }
  const start = Number(character.height_inches);
  const expected = Number(character.expected_adult_height) || expectedAdultHeight(start, genes);
  const sofar = growthCurve(character.id, start, genes).filter((p) => p.age <= age);
  const now = sofar[sofar.length - 1];

  const line = el('p', {},
    el('b', {}, age <= START_AGE
      ? `${formatHeight(start)}, and he is ${START_AGE}.`
      : `${formatHeight(now.inches)} at ${age}, up from ${formatHeight(start)} at ${START_AGE}.`),
    ` He is expected to finish somewhere around ${formatHeight(expected)}`
    + `, but that is an expectation and not a ceiling - he keeps growing a little every `
    + `offseason until he is ${GROWTH_END_AGE}, less each year, and there is nothing in the `
    + 'model that says stop. You find out how tall he is when he gets there.');

  const strip = el('div', { class: 'cv-growth' },
    sofar.map((p) => el('span', { class: 'cv-growth-step', title: `age ${p.age}` },
      el('b', {}, formatHeight(p.inches)),
      el('em', {}, String(p.age)))));

  return el('div', {}, line, strip);
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
    await load();
    showNote($('#notices'), 'good',
      `${sent.length} request${sent.length === 1 ? '' : 's'} filed for `
      + `${character.first_name} ${character.last_name}. The commissioner applies them at the next Sim Week.`);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    showNote(problems, 'bad', errorText(err)
      + (sent.length ? ` (${sent.length} went through before this one)` : ''));
    button.disabled = false;
    button.textContent = 'Try again';
    if (sent.length) await load();
  }
}

async function cancel(request, button) {
  button.disabled = true;
  try {
    await cancelRequest(request.id);
    await load();
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
