/* The GOAT board: its own page since 2026-09-26, moved off All time so the calculator has room.
   The board on the left, the formula beside it and always open; the scoring itself lives in
   goat-score.js, shared with the Home page's top five. Public, like the league sites. */

import { $, el, clear, freshJSON, renderChrome, renderFooter, showNote } from './ui.js';
import {
  client, isConfigured, signIn, signOut, currentUser, ensureProfile, errorText, LEAGUE_LABELS,
} from './supabase.js';
import { renderGoatBoard, loadWeights } from './goat-score.js';

const LEAGUES = ['prep', 'college', 'pro'];

const chrome = renderChrome({
  active: 'goat-board.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

const WEIGHTS = loadWeights();       // one set of weights across every league
const OURS = new Set();              // lower-cased character names, for the marker
const CACHE = new Map();
const goatOf = (key) => {
  if (!CACHE.has(key)) CACHE.set(key, freshJSON(`leagues/${key}/goat.json`));
  return CACHE.get(key);
};

const pick = (key) => (LEAGUES.includes(key) ? key : 'pro');
let LEAGUE = pick(new URLSearchParams(window.location.search).get('league'));
let ONLY_OURS = false;
let DATA = null;

$('#only-ours').addEventListener('change', (e) => { ONLY_OURS = e.target.checked; draw(); });

/* On a narrow screen the formula sits above the board, and open it pushed the board a screen
   down - every slider you moved re-ranked a list you could not see. So there it starts shut
   behind an Adjust button (the button only shows at that width; see .cv-formula-toggle). */
$('#formula-toggle').addEventListener('click', (e) => {
  const open = $('#formula-card').classList.toggle('is-open');
  e.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
  e.currentTarget.textContent = open ? 'Hide' : 'Adjust';
});
renderSwitch();

if (isConfigured()) {
  (async () => {
    const user = await currentUser();
    chrome.refresh(user, user ? await ensureProfile() : null);
  })().catch((err) => console.warn('sign-in state failed', err));
}

markOurs().then(load).catch((err) => showNote($('#notices'), 'bad', errorText(err)));

function renderSwitch() {
  const box = $('#switch');
  clear(box);
  for (const key of LEAGUES) {
    box.append(el('button', {
      type: 'button', role: 'tab', 'aria-selected': key === LEAGUE ? 'true' : 'false',
      onclick: () => {
        if (key === LEAGUE) return;
        LEAGUE = key;
        window.history.replaceState(null, '', `?league=${key}`);
        renderSwitch();
        load();
      },
    }, LEAGUE_LABELS[key] || key));
  }
}

async function load() {
  const wanted = LEAGUE;
  $('#goat').replaceChildren(el('p', { class: 'cv-spinner' }, 'Working out the greats...'));
  const data = await goatOf(wanted);
  if (wanted !== LEAGUE) return;        // another league was picked while this one loaded
  DATA = data;
  draw();
}

function draw() {
  renderGoatBoard($('#goat'), DATA, {
    weights: WEIGHTS,
    onlyOurs: ONLY_OURS,
    isOurs: (name) => OURS.has(String(name || '').toLowerCase()),
    formulaHost: $('#formula'),
  });
}

/* Our own characters, matched by name because that is what the archive carries. Never fatal:
   signed out or Supabase unreachable simply means nobody is highlighted. */
async function markOurs() {
  try {
    if (!isConfigured()) return;
    const db = client();
    if (!db) return;
    const { data } = await db.from('characters').select('first_name,last_name');
    for (const c of data || []) OURS.add(`${c.first_name} ${c.last_name}`.trim().toLowerCase());
  } catch (err) {
    /* a page about history does not need to know who is logged in */
  }
}
