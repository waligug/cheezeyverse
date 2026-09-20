/* create.html - answer fourteen questions and find out who the kid turns out to be.

   There is no stat allocation here any more. The quiz (plus the build, the career goal and
   the summer) derives a trait vector, and rules.deriveCharacter() turns that into the
   starting sheet, the potentials, the tendencies, the growth bias, the emergent class and
   the projected adult height. Everything on the review card is recomputed from scratch on
   every keystroke, so what you see is exactly what gets inserted.

   The 20 opening skill points still exist - they are simply banked rather than spent. The
   database grants them at insert (points_spent = 0), and me.html is where they go. */

import {
  POSITIONS, POSITION_LABELS, HEIGHT_RANGES, HEIGHT_DEFAULTS, ADULT_HEIGHT_RANGES,
  QUIZ, CAREER_GOALS, SUMMER_WORK, BUILDS, careerGoal,
  deriveCharacter, quizProgress, validateBuild, canCreateAnother,
  formatHeight, describeCurve, GOAL_DISCOUNT, START_AGE, GROWTH_END_AGE, buildWeight,
} from './rules.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, settings,
  myCharacters, createCharacter, errorText,
  oauthErrorFromUrl,
} from './supabase.js';
import {
  $, el, clear, renderChrome, renderFooter, setupNeededNote, showNote, note,
  renderDerivedSheet, renderScoutingSheet, renderTraitBars, classLine,
} from './ui.js';

const chrome = renderChrome({
  active: 'create.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

function blankState() {
  return {
    firstName: '', lastName: '', hometown: '',
    jersey: null,
    position: 'SG',
    heightInches: HEIGHT_DEFAULTS.SG,
    build: 'solid',
    // set from height and build until he drags the slider, then his
    weightLbs: buildWeight(HEIGHT_DEFAULTS.SG, 'solid'),
    weightTouched: false,
    answers: {},
    goal: null,
    summer: null,
  };
}

let state = blankState();
let startingPoints = 20;
let busy = false;

$('#curve').textContent = describeCurve();
$('#signin').addEventListener('click', () => signIn().catch(
  (e) => showNote($('#notices'), 'bad', errorText(e))));

// Declared before first use: `preview()` runs immediately below, and a `let` further down the
// file would still be in its temporal dead zone at that point.
let previewOnly = false;

// Same reason, and this file had already written the rule down: boot() and preview() run at the
// top of the module and both reach syncWeightRange(), so a `const` declared further down is
// still uninitialised when they get there. Function declarations hoist; const does not.
// How far either side of the usual weight the slider goes - about the distance from "wiry" to
// "heavy" twice over, so it is a real choice without stopping being a fourteen-year-old.
const WEIGHT_SPREAD = 25;

/* An OAuth failure comes back in the URL, not as an exception - see oauthErrorFromUrl().
   Read it before anything else so the page can say what happened instead of just looking
   signed out. This runs even when the Supabase client is never constructed. */
const cvOauthError = oauthErrorFromUrl();
if (cvOauthError) showNote($('#notices'), 'bad', `Discord sign-in failed: ${cvOauthError}`);

if (!isConfigured()) {
  // Preview mode. Without Supabase there is nobody to save a character for, but the whole point
  // of this page is the questions - so build the form anyway and let people play with it. Only
  // the final save is blocked, and the button says so rather than failing when it is pressed.
  $('#notices').append(setupNeededNote());
  preview();
} else {
  boot().catch((err) => showNote($('#notices'), 'bad', errorText(err)));
}

function preview() {
  previewOnly = true;
  $('#gate').hidden = true;
  $('#form').hidden = false;
  $('#intro').textContent = `He turns ${START_AGE} this season and he is not good yet. That is `
    + `the idea. You do not hand him stats - you answer questions about him, and the answers `
    + `decide what he is. Nothing is saved until the site is connected to Supabase.`;
  buildPositionPicker();
  renderBuilds();
  renderQuiz();
  renderGoals();
  renderSummer();
  wireForm();
  syncHeightRange();
  draw();
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
  $('#intro').textContent = `He turns ${START_AGE} this season and he is not good yet. That is `
    + `the idea. You do not hand him stats - you answer questions about him, and the answers `
    + `decide what he is. His ${startingPoints} opening points are banked for later.`;

  buildPositionPicker();
  renderBuilds();
  renderQuiz();
  renderGoals();
  renderSummer();
  wireForm();
  syncHeightRange();
  draw();
}

/* ------------------------------------------------------------------------------ form */

function buildPositionPicker() {
  const sel = $('#position');
  clear(sel);
  for (const p of POSITIONS) sel.append(el('option', { value: p }, `${p} – ${POSITION_LABELS[p]}`));
  sel.value = state.position;
}

function wireForm() {
  $('#first').addEventListener('input', (e) => { state.firstName = e.target.value; draw(); });
  $('#last').addEventListener('input', (e) => { state.lastName = e.target.value; draw(); });
  $('#hometown').addEventListener('input', (e) => { state.hometown = e.target.value; draw(); });
  $('#jersey').addEventListener('input', (e) => {
    const n = e.target.value === '' ? null : Number(e.target.value);
    state.jersey = Number.isFinite(n) ? Math.floor(n) : null;
    draw();
  });

  $('#position').addEventListener('change', (e) => {
    state.position = e.target.value;
    syncHeightRange();
    draw();
  });

  $('#height').addEventListener('input', (e) => {
    state.heightInches = Number(e.target.value);
    syncHeightRead();
    syncWeightRange();
    draw();
  });

  $('#weight').addEventListener('input', (e) => {
    // From here the number is HIS. Height and build stop moving it, exactly as the day box on
    // the commissioner panel stops re-suggesting once somebody types - the alternative is a
    // slider that silently undoes the choice the person just made with it.
    state.weightTouched = true;
    state.weightLbs = Number(e.target.value);
    syncWeightRead();
    draw();
  });

  $('#reset').addEventListener('click', () => {
    state = blankState();
    $('#first').value = '';
    $('#last').value = '';
    $('#hometown').value = '';
    $('#jersey').value = '';
    $('#position').value = state.position;
    renderBuilds();
    renderQuiz();
    renderGoals();
    renderSummer();
    syncHeightRange();
    draw();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  $('#form').addEventListener('submit', submit);
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
  syncHeightRead();

  syncWeightRange();

  const [aLo, aHi] = ADULT_HEIGHT_RANGES[state.position];
  $('#position-note').textContent = 'This is what he is listed at. The coach picks the '
    + 'lineups and will happily play him somewhere else. A grown '
    + `${POSITION_LABELS[state.position]} is usually ${formatHeight(aLo)} to ${formatHeight(aHi)}.`;
}

function syncHeightRead() {
  const [lo, hi] = HEIGHT_RANGES[state.position];
  $('#height-read').textContent =
    `${formatHeight(state.heightInches)} (${state.heightInches} in) at ${START_AGE} · `
    + `a fourteen year old is ${formatHeight(lo)} to ${formatHeight(hi)}. He is not done growing.`;
}

/* WEIGHT USED TO BE DERIVED AND NEVER STORED - buildWeight(height, build), recomputed by the
 * commissioner every time it wrote him into league.dat. That is still the DEFAULT, and it still
 * follows height and build around, so somebody who does not care never has to think about it.
 *
 * Once he drags it, it is his: `weightTouched` stops the default from overwriting the choice.
 * The band is the derived weight plus or minus 25 lbs, which at these heights is roughly the
 * distance between "wiry" and "heavy" twice over - wide enough to be a real choice, narrow
 * enough that it stays a fourteen-year-old. */
function syncWeightRange() {
  const base = buildWeight(state.heightInches, state.build);
  const slider = $('#weight');
  slider.min = String(base - WEIGHT_SPREAD);
  slider.max = String(base + WEIGHT_SPREAD);
  if (!state.weightTouched) {
    state.weightLbs = base;
  } else {
    // he moved it, then changed height or build: keep his choice, but it must stay in the band
    state.weightLbs = Math.min(base + WEIGHT_SPREAD, Math.max(base - WEIGHT_SPREAD, state.weightLbs));
  }
  slider.value = String(state.weightLbs);
  syncWeightRead();
}

function syncWeightRead() {
  const base = buildWeight(state.heightInches, state.build);
  const diff = state.weightLbs - base;
  const how = diff === 0 ? 'usual for that height and build'
    : `${Math.abs(diff)} lbs ${diff > 0 ? 'heavier' : 'lighter'} than usual for that build`;
  $('#weight-read').textContent = `${state.weightLbs} lbs at ${START_AGE} · ${how}.`;
}

/* ---------------------------------------------------------------------------- choices */

/**
 * Repaint which card in a radio group looks selected, WITHOUT rebuilding the group.
 *
 * Picking an answer used to call the group's render function again, which cleared the
 * container and built every card from scratch - all fourteen questions, on every single
 * click. That destroys the radio you just clicked, so focus is lost, and the page collapses
 * to a shorter height for an instant while it rebuilds. The browser clamps the scroll
 * position to the shorter page, and when it expands again you are somewhere near the top.
 * The page appeared to teleport upwards every time you answered anything.
 *
 * Nothing about the cards actually changes except which one is highlighted, and the browser
 * already tracks the checked state for a named radio group. So only the class needs moving.
 */
function syncChoiceGroup(name) {
  const inputs = document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`);
  for (const input of inputs) {
    const card = input.closest('.cv-choice');
    if (card) card.classList.toggle('is-on', input.checked);
  }
}


function choiceCard(name, value, checked, title, blurb, onPick) {
  return el('label', { class: `cv-choice${checked ? ' is-on' : ''}` },
    el('input', {
      type: 'radio', name, value, checked, onchange: () => onPick(value),
    }),
    el('b', {}, title),
    blurb ? el('span', {}, blurb) : null);
}

function renderBuilds() {
  const box = $('#builds');
  clear(box);
  for (const b of BUILDS) {
    box.append(choiceCard('build', b.id, state.build === b.id, b.label, b.blurb, (v) => {
      state.build = v;
      syncChoiceGroup('build');
      syncWeightRange();
      draw();
    }));
  }
}

function renderQuiz() {
  const box = $('#quiz');
  clear(box);
  QUIZ.forEach((q, i) => {
    const section = el('section', { class: 'cv-question' },
      el('h3', {}, `${i + 1}. ${q.prompt}`));
    const choices = el('div', { class: 'cv-choices' });
    for (const a of q.answers) {
      choices.append(choiceCard(`q-${q.id}`, a.id, state.answers[q.id] === a.id, a.text, null,
        (v) => {
          state.answers[q.id] = v;
          syncChoiceGroup(`q-${q.id}`);
          draw();
        }));
    }
    section.append(choices);
    box.append(section);
  });
}

function renderGoals() {
  const box = $('#goals');
  clear(box);
  for (const g of CAREER_GOALS) {
    box.append(choiceCard('goal', g.id, state.goal === g.id, g.label, g.line, (v) => {
      state.goal = v;
      syncChoiceGroup('goal');
      draw();
    }));
  }
}

function renderSummer() {
  const box = $('#summer');
  clear(box);
  for (const s of SUMMER_WORK) {
    box.append(choiceCard('summer', s.id, state.summer === s.id, s.label, null, (v) => {
      state.summer = v;
      syncChoiceGroup('summer');
      draw();
    }));
  }
}

/* ----------------------------------------------------------------------------- review */

function draw() {
  const progress = quizProgress(state.answers);
  $('#quiz-progress').textContent = progress.complete
    ? 'all fourteen answered'
    : `${progress.done} of ${progress.total} answered`;

  const goal = careerGoal(state.goal);
  $('#goal-note').textContent = goal
    ? `Every point of ${goal.discount.join(', ')} costs him ${GOAL_DISCOUNT}% less, forever. `
      + 'That is the whole mechanical effect - the rest of it is who he is.'
    : 'Pick one. It shows on his page and it makes four ratings slightly cheaper for life.';

  renderReview();
  refreshProblems();
}

function renderReview() {
  const box = $('#review');
  clear(box);

  const progress = quizProgress(state.answers);
  const derived = deriveCharacter(state);
  const name = `${state.firstName || 'He'} ${state.lastName || ''}`.trim();

  function possessive(who) {
    if (!state.firstName && !state.lastName) return 'The';
    return who.endsWith('s') ? `${who}'` : `${who}'s`;
  }

  if (!progress.complete) {
    box.append(note(null,
      `${progress.total - progress.done} question`
      + `${progress.total - progress.done === 1 ? '' : 's'} left. This is what he looks like `
      + 'so far, and it moves with every answer.'));
  }

  /* The class, live - but only once the answers are actually shaping it. Naming a type before a
     single question is answered reads as though the quiz is decoration. */
  const CLASS_AFTER = 3;
  if (progress.done < CLASS_AFTER) {
    box.append(el('div', { class: 'cv-readout cv-cheese' },
      el('b', {}, 'No read yet'),
      el('span', {}, `Answer ${CLASS_AFTER - progress.done} more question`
        + `${CLASS_AFTER - progress.done === 1 ? '' : 's'} and the scouts will call him something.`),
      el('span', {}, 'Whatever they land on is a label, not a cage - it has no caps and it '
        + 'changes as he does.')));
  } else {
    box.append(el('div', { class: 'cv-readout cv-cheese' },
      el('b', {}, classLine(derived.klass)),
      el('span', {}, derived.klass.blurb),
      el('span', {}, `Second closest: ${derived.klass.runnerUp.label}. `
        + 'This is a label, not a cage - it has no caps and it changes as he does.')));
  }

  /* height: a band, not a number, until he is signed */
  const o = derived.outlook;
  box.append(el('p', {},
    el('b', {}, `${formatHeight(state.heightInches)} now, and somewhere around `
      + `${formatHeight(o.low)} to ${formatHeight(o.high)} when he is done.`),
    ' ',
    `A lucky one gets past ${formatHeight(o.ceiling)}; an unlucky one stops at `
    + `${formatHeight(o.floor)}. He grows every offseason until he is ${GROWTH_END_AGE}, `
    + 'less each year, and there is no ceiling on it - just less and less of it.'));

  box.append(el('p', { class: 'cv-muted' },
    `about ${derived.weightLbs} lbs · ${state.hometown || 'hometown not set'}`
    + (state.jersey === null ? '' : ` · #${state.jersey}`)));

  /* traits, in words */
  box.append(el('h3', {}, 'What the answers say about him'));
  const traits = el('div', { class: 'cv-traits' });
  renderTraitBars(traits, derived.traits, { numbers: false });
  box.append(traits);

  /* the sheet, as a scout would give it: bands and opinions, no numbers */
  // `name` falls back to the pronoun "He" when the fields are empty, which reads as
  // "He scouting report"; and a real name needs its possessive.
  box.append(el('h3', {}, possessive(name) + ' scouting report'));
  box.append(el('p', { class: 'cv-hint' },
    'He is fourteen, so all of this is bad in absolute terms and that is the point. '
    + 'Where the band is narrow, the answers were emphatic and there is not much doubt; '
    + 'where it is wide, the quiz said very little about that part of his game and the '
    + 'roll gets to decide.'));
  const sheet = el('div', {});
  renderScoutingSheet(sheet, derived);
  box.append(sheet);
}

/** The reveal: the only place on this page that shows a number. */
function renderSigned(created, derived) {
  const box = $('#signed');
  clear(box);
  box.hidden = false;

  const card = el('section', { class: 'cv-card' });
  card.append(el('div', { class: 'cv-card-head' },
    el('h2', {}, `#${created.jersey_preference} ${created.first_name} ${created.last_name}`),
    el('span', { class: 'cv-pill' }, 'signed')));
  card.append(el('div', { class: 'cv-readout cv-cheese' },
    el('b', {}, classLine(derived.klass)),
    el('span', {}, derived.klass.blurb)));
  card.append(el('p', {},
    `${formatHeight(created.height_inches)} and about ${derived.weightLbs} lbs, from `
    + `${created.hometown}. He is expected to finish around `
    + `${formatHeight(derived.expectedAdultHeight)}; his own curve is on `,
    el('a', { href: 'me.html' }, 'his page'), '.'));

  card.append(el('h3', {}, 'The numbers, now that he is yours'));
  const sheet = el('div', {});
  renderDerivedSheet(sheet, derived);
  card.append(sheet);

  const traits = el('div', { class: 'cv-traits' });
  renderTraitBars(traits, derived.traits, { numbers: true });
  card.append(el('h3', {}, 'And what the quiz made him'), traits);

  card.append(el('p', { class: 'cv-actions' },
    el('a', { class: 'cv-btn cv-big', href: 'me.html' }, 'Go to my players')));
  box.append(card);
}

function refreshProblems() {
  const derived = deriveCharacter(state);
  const result = validateBuild({
    ...state, ratings: derived.ratings, potentials: derived.potentials,
  });
  const box = $('#problems');
  clear(box);
  $('#submit').disabled = busy || !result.ok;
  if (previewOnly) {
    $('#submit').disabled = true;
    $('#submit').textContent = 'Preview only - connect Supabase to sign him';
  }
  if (busy) return result;
  if (!result.ok) {
    box.append(note('bad', 'Not ready yet:', result.errors.slice(0, 6)));
  } else {
    box.append(note('good',
      'Ready. Signing him rolls his numbers once and for all - the same name, hometown and '
      + `number always roll the same kid, so this is not a slot machine. He arrives with `
      + `${startingPoints} points banked and nothing spent.`));
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
  $('#submit').textContent = 'Rolling...';
  try {
    const derived = deriveCharacter(state);
    const created = await createCharacter({ ...state, derived });
    clear($('#problems'));
    $('#form').hidden = true;
    showNote($('#notices'), 'good',
      `${created.first_name} ${created.last_name} is in - a ${derived.klass.label} on paper, `
      + 'for now. He is pending until the commissioner puts him on a Prep roster, usually at '
      + 'the next sim.');
    renderSigned(created, derived);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    showNote($('#problems'), 'bad', errorText(err));
  } finally {
    busy = false;
    $('#submit').textContent = 'Sign him and roll the numbers';
    $('#submit').disabled = false;
  }
}
