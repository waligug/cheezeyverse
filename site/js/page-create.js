/* create.html - build a 14 year old and spend the opening points.

   The sheet starts from rules.startingSheet(position, archetype) and every +/- goes
   through rules.upgradeCost, so the running total on screen is the same arithmetic the
   database will do when the row lands. The insert carries the finished sheet; the trigger
   in schema.sql pins owner, status and the points, so nothing here is trusted. */

import {
  POSITIONS, POSITION_LABELS, ARCHETYPES, HEIGHT_RANGES, HEIGHT_DEFAULTS,
  archetypesForPosition, startingSheet, capsFor, sheetCost, nextPointCost,
  validateBuild, canCreateAnother, formatHeight, describeCurve, isLocked, hasPotential,
} from './rules.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, settings,
  myCharacters, createCharacter, errorText,
} from './supabase.js';
import {
  $, el, clear, renderChrome, renderFooter, setupNeededNote, showNote, note,
  renderSheet, renderMeter,
} from './ui.js';

const chrome = renderChrome({
  active: 'create.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const state = {
  firstName: '', lastName: '',
  position: 'SG',
  archetype: 'sharpshooter',
  heightInches: HEIGHT_DEFAULTS.SG,
  ratings: {}, potentials: {},
};
let startingPoints = 20;
let start = null;
let caps = null;
let busy = false;

$('#curve').textContent = describeCurve();
$('#signin').addEventListener('click', () => signIn().catch(
  (e) => showNote($('#notices'), 'bad', errorText(e))));

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

  const cfg = await settings();
  startingPoints = Number(cfg.starting_points) || 20;
  const maxCharacters = Number(cfg.max_characters) || 2;

  const mine = await myCharacters();
  const room = canCreateAnother(mine, maxCharacters);
  if (!room.ok) {
    $('#notices').append(note('bad',
      `You already have ${room.live} live character${room.live === 1 ? '' : 's'} and the limit `
      + `is ${room.max}. Retire one, or ask the commissioner to raise the limit.`));
    $('#gate').hidden = true;
    return;
  }

  $('#form').hidden = false;
  $('#intro').textContent = `He turns 14 this season and he is not good yet. That is the idea. `
    + `You get ${startingPoints} points to spend now.`;

  buildPositionPicker();
  wireForm();
  resetSheet();
}

/* ------------------------------------------------------------------------------ form */

function buildPositionPicker() {
  const sel = $('#position');
  clear(sel);
  for (const p of POSITIONS) sel.append(el('option', { value: p }, `${p} – ${POSITION_LABELS[p]}`));
  sel.value = state.position;
}

function wireForm() {
  $('#first').addEventListener('input', (e) => { state.firstName = e.target.value; refreshProblems(); });
  $('#last').addEventListener('input', (e) => { state.lastName = e.target.value; refreshProblems(); });

  $('#position').addEventListener('change', (e) => {
    state.position = e.target.value;
    const allowed = archetypesForPosition(state.position);
    if (!allowed.includes(state.archetype)) state.archetype = allowed[0];
    syncHeightRange();
    resetSheet();
  });

  $('#height').addEventListener('input', (e) => {
    state.heightInches = Number(e.target.value);
    $('#height-read').textContent = `${formatHeight(state.heightInches)} (${state.heightInches} in)`;
    refreshProblems();
  });

  $('#reset').addEventListener('click', () => resetSheet());
  $('#form').addEventListener('submit', submit);

  syncHeightRange();
}

function syncHeightRange() {
  const [lo, hi] = HEIGHT_RANGES[state.position];
  const slider = $('#height');
  slider.min = String(lo);
  slider.max = String(hi);
  if (state.heightInches < lo || state.heightInches > hi) {
    state.heightInches = HEIGHT_DEFAULTS[state.position];
  }
  slider.value = String(state.heightInches);
  $('#height-read').textContent =
    `${formatHeight(state.heightInches)} (${state.heightInches} in) · `
    + `a ${POSITION_LABELS[state.position]} is ${formatHeight(lo)} to ${formatHeight(hi)}`;
}

function renderArchetypes() {
  const box = $('#archetypes');
  clear(box);
  const allowed = archetypesForPosition(state.position);
  for (const [key, arch] of Object.entries(ARCHETYPES)) {
    const ok = allowed.includes(key);
    const label = el('label', {
      class: `cv-choice${key === state.archetype ? ' is-on' : ''}${ok ? '' : ' is-off'}`,
    },
    el('input', {
      type: 'radio', name: 'archetype', value: key,
      checked: key === state.archetype, disabled: !ok,
      onchange: () => { state.archetype = key; resetSheet(); },
    }),
    el('b', {}, arch.label),
    el('span', {}, ok ? arch.blurb
      : `Not a ${POSITION_LABELS[state.position]} archetype.`));
    box.append(label);
  }
  const arch = ARCHETYPES[state.archetype];
  const top = Object.entries(arch.caps).filter(([, v]) => v >= 88)
    .map(([r]) => r).slice(0, 5);
  $('#archetype-note').textContent = top.length
    ? `A ${arch.label} can eventually reach 88+ in ${top.join(', ')}. Everything else has a lower ceiling.`
    : `A ${arch.label} has no elite ceiling anywhere; he is good at everything instead.`;
}

/* ----------------------------------------------------------------------------- sheet */

function resetSheet() {
  start = startingSheet(state.position, state.archetype);
  caps = capsFor(state.archetype);
  state.ratings = { ...start.ratings };
  state.potentials = { ...start.potentials };
  renderArchetypes();
  draw();
}

function spent() {
  return sheetCost(state.position, state.archetype, state.ratings, state.potentials);
}

function budget() { return startingPoints - spent(); }

function step(rating, kind, dir) {
  if (isLocked(rating)) return;
  const bag = kind === 'potential' ? state.potentials : state.ratings;
  const floor = kind === 'potential' ? start.potentials[rating] : start.ratings[rating];
  const value = bag[rating];

  if (dir > 0) {
    const ceiling = kind === 'potential'
      ? caps[rating]
      : Math.min(caps[rating], hasPotential(rating) ? state.potentials[rating] : caps[rating]);
    if (value >= ceiling) return;
    if (nextPointCost(value, kind) > budget()) return;
    bag[rating] = value + 1;
  } else {
    if (value <= floor) return;
    if (kind === 'potential' && value - 1 < state.ratings[rating]) {
      showNote($('#problems'), 'bad',
        `Take ${rating} itself back down first - a rating cannot sit above its potential.`);
      return;
    }
    bag[rating] = value - 1;
  }
  draw();
}

function draw() {
  renderMeter($('#meter'), {
    budget: budget(),
    spent: spent(),
    label: `${ARCHETYPES[state.archetype].label} ${state.position}`,
  });
  renderSheet($('#sheet'), {
    archetype: state.archetype,
    caps,
    base: start,
    ratings: state.ratings,
    potentials: state.potentials,
    budget: budget(),
    showPotentials: true,
  }, step);
  refreshProblems();
}

function currentBuild() {
  return {
    firstName: state.firstName,
    lastName: state.lastName,
    position: state.position,
    archetype: state.archetype,
    heightInches: state.heightInches,
    ratings: state.ratings,
    potentials: state.potentials,
  };
}

function refreshProblems() {
  const result = validateBuild(currentBuild(), { startingPoints });
  const box = $('#problems');
  clear(box);
  $('#submit').disabled = busy || !result.ok;
  if (busy) return result;
  if (!result.ok) {
    box.append(note('bad', 'Not ready yet:', result.errors.slice(0, 6)));
  } else if (result.remaining > 0) {
    box.append(note(null,
      `${result.remaining} point${result.remaining === 1 ? '' : 's'} still unspent - they carry `
      + 'over, so that is fine if you are saving for something expensive.'));
  }
  return result;
}

/* ---------------------------------------------------------------------------- submit */

async function submit(event) {
  event.preventDefault();
  const result = refreshProblems();
  if (!result.ok || busy) return;

  busy = true;
  $('#submit').disabled = true;
  $('#submit').textContent = 'Sending him in...';
  try {
    const created = await createCharacter({ ...currentBuild(), spent: result.spent });
    clear($('#problems'));
    $('#form').hidden = true;
    showNote($('#notices'), 'good',
      `${created.first_name} ${created.last_name} is in. He is pending until the commissioner `
      + 'puts him on a Prep roster - usually at the next sim.');
    $('#notices').append(el('p', { class: 'cv-actions' },
      el('a', { class: 'cv-btn', href: 'me.html' }, 'Go to my players')));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    showNote($('#problems'), 'bad', errorText(err));
  } finally {
    busy = false;
    $('#submit').textContent = 'Create this player';
    $('#submit').disabled = false;
  }
}
