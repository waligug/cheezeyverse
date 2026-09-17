/* me.html - your own players: ratings, banked points, the spend form, request history,
   and the declare button.

   Spending here does not change a rating. It files upgrade_requests rows, which the
   database prices itself and the local commissioner app applies to the save file during
   a Sim Week. Until then the cost is "reserved": it is subtracted from what you can spend
   again, so you cannot queue more than you have. */

import {
  RATINGS, POTENTIAL_RATINGS, RATING_LABELS, ARCHETYPES,
  capsFor, nextPointCost, hasPotential, isLocked, describeCurve, upgradeCost,
} from './rules.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, settings,
  myCharacters, requestsFor, ledgerFor, requestUpgrade, cancelRequest,
  declareForDraft, deletePendingCharacter, LEAGUE_LABELS, errorText,
} from './supabase.js';
import {
  $, el, clear, renderChrome, renderFooter, setupNeededNote, showNote, note,
  statusPill, renderSheet, renderMeter, describeCharacter, fmtDate,
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

if (!isConfigured()) {
  $('#notices').append(setupNeededNote());
} else {
  boot().catch((err) => showNote($('#notices'), 'bad', errorText(err)));
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

  $('#intro').textContent = `You get ${cfg.points_per_week} point per simulated week, `
    + `and you can hold up to ${cfg.max_characters} live characters. ${describeCurve()}.`;

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
  return `${c.points_available}:${JSON.stringify(c.ratings)}:${JSON.stringify(c.potentials)}`;
}

function renderCharacter(character, requests, ledger) {
  const card = el('section', { class: 'cv-card' });
  const arch = ARCHETYPES[character.archetype];
  const reserved = reservedCost(requests);

  card.append(el('div', { class: 'cv-card-head' },
    el('h2', {}, `${character.first_name} ${character.last_name}`),
    statusPill(character.status),
    el('span', { class: 'cv-pill' }, LEAGUE_LABELS[character.league] || character.league)));

  card.append(el('p', { class: 'cv-muted' },
    `${describeCharacter(character)}`
    + (character.game_dob ? ` · born ${fmtDate(character.game_dob)} in game` : '')));

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
  const caps = capsFor(character.archetype);
  const base = { ratings: character.ratings || {}, potentials: character.potentials || {} };

  const queuedCost = () => {
    let total = 0;
    for (const r of RATINGS) {
      const from = Number(base.ratings[r] ?? 0);
      const to = Number(draft.ratings[r] ?? from);
      if (to > from) total += upgradeCost(from, to - from, 'rating');
    }
    for (const r of POTENTIAL_RATINGS) {
      const from = Number(base.potentials[r] ?? 0);
      const to = Number(draft.potentials[r] ?? from);
      if (to > from) total += upgradeCost(from, to - from, 'potential');
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
        ? caps[rating]
        : Math.min(caps[rating], hasPotential(rating) ? Number(draft.potentials[rating]) : caps[rating]);
      if (value >= ceiling) return;
      if (nextPointCost(value, kind) > freePoints()) return;
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
    renderMeter(meter, {
      budget: freePoints(),
      spent: queued,
      label: reserved ? `${reserved} already waiting on the commissioner` : (arch ? arch.label : ''),
    });
    renderSheet(sheet, {
      archetype: character.archetype,
      caps,
      base,
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

  if (character.status === 'retired') {
    body.append(note(null, 'Retired. His sheet is frozen.'));
  } else {
    body.append(
      el('h3', {}, 'Spend points'),
      meter,
      el('p', { class: 'cv-hint' },
        'Nothing changes until the commissioner applies it during a Sim Week. '
        + 'Queued points are held back so you cannot promise the same point twice.'),
      sheet, problems, actions,
    );
    drawSpend();
  }

  /* ---- history ---- */
  body.append(el('h3', {}, 'Requests'), requestTable(requests));
  body.append(el('h3', {}, 'Points'), ledgerTable(ledger, character));

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
  if (!window.confirm(`Declare ${character.first_name} ${character.last_name} for ${target}? `
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
