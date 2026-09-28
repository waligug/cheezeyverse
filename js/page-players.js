/* players.html - the roll call: every real person's player, in one place.

   Four hundred players per league were invented by the game. These are the handful that
   belong to someone, and on a roster page they are one row among fifteen identical ones -
   which is why the generated league pages badge them too. This page is the other half of
   that: not "find mine on this roster" but "who is actually in this universe".

   Public on purpose. A visitor who has never signed in should be able to see what the thing
   is before being asked for a Discord account, the same way the league sites are public. */

import {
  isConfigured, signIn, signOut, currentUser, allCharacters, settings, errorText,
  oauthErrorFromUrl, leagueSites,
} from './supabase.js';
import {
  el, $, clear, renderChrome, renderFooter, setupNeededNote, showNote, statusPill, fmtDate,
} from './ui.js';
import { RATINGS, formatHeight, POSITION_LABELS, classify } from './rules.js';

const LEVELS = [
  { key: 'all', label: 'Everyone' },
  { key: 'prep', label: 'Prep' },
  { key: 'college', label: 'College' },
  { key: 'pro', label: 'Pro' },
];

const chrome = renderChrome({
  active: 'players.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

/* An OAuth failure comes back in the URL, not as an exception - see oauthErrorFromUrl().
   Read it before anything else so the page can say what happened instead of just looking
   signed out. */
const cvOauthError = oauthErrorFromUrl();
if (cvOauthError) showNote($('#notices'), 'bad', `Discord sign-in failed: ${cvOauthError}`);

let everyone = [];
let filter = 'all';
let me = null;

if (!isConfigured()) {
  $('#notices').append(setupNeededNote());
  $('#loading')?.remove();
} else {
  boot().catch((err) => {
    $('#loading')?.remove();
    showNote($('#notices'), 'bad', errorText(err));
  });
}

async function boot() {
  const user = await currentUser();
  me = user ? user.id : null;
  chrome.refresh(user, null);
  everyone = await allCharacters();
  $('#loading')?.remove();
  renderFilters();
  render();
}

function countFor(key) {
  return key === 'all'
    ? everyone.length
    : everyone.filter((c) => c.league === key).length;
}

function renderFilters() {
  const box = $('#filters');
  clear(box);
  for (const lvl of LEVELS) {
    const n = countFor(lvl.key);
    box.append(el('button', {
      type: 'button',
      class: `cv-tab${filter === lvl.key ? ' is-on' : ''}`,
      onclick: () => { filter = lvl.key; renderFilters(); render(); },
    }, `${lvl.label} (${n})`));
  }
}

/* The live sheet, averaged over the twelve ratings that are abilities rather than habits.
   Not a grade and not shown as one - it is only here so the list can be ordered by something
   more useful than the order people happened to sign up in. */
function strengthOf(c) {
  const r = c.ratings || {};
  const vals = RATINGS.filter((k) => k !== '3pUsage' && k !== 'Fouling')
    .map((k) => Number(r[k]) || 0);
  if (!vals.length) return 0;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function leagueLink(c) {
  const id = (c.league_player_ids || {})[c.league];
  if (id == null) return null;
  // leagueSites() returns {key: {url, ready}}, not {key: url} - the extra flag is how the
  // hub knows whether a league site has ever been published.
  const site = (leagueSites() || {})[c.league] || {};
  const base = site.url || `leagues/${c.league}/index.htm`;
  return `${String(base).replace(/index\.htm$/, '')}players/player${id}.htm`;
}

function row(c) {
  const mine = me && c.owner === me;
  const owner = c.owner_profile?.display_name || 'somebody';
  const klass = c.ratings && Object.keys(c.ratings).length
    ? classify(c.ratings, c.position) : null;
  const href = leagueLink(c);

  const name = href
    ? el('a', { class: 'cv-rollname', href, target: '_blank', rel: 'noopener' },
      `${c.first_name} ${c.last_name}`)
    : el('span', { class: 'cv-rollname' }, `${c.first_name} ${c.last_name}`);

  return el('div', { class: `cv-roll${mine ? ' is-mine' : ''}` },
    el('div', { class: 'cv-roll-main' },
      name,
      mine ? el('span', { class: 'cv-chip cv-chip-mine' }, 'yours') : null,
      statusPill(c.status)),
    el('div', { class: 'cv-roll-meta' },
      el('span', {}, `${POSITION_LABELS[c.position] || c.position}`),
      el('span', {}, formatHeight(c.height_inches)),
      klass ? el('span', {}, klass.label) : null,
      el('span', { class: 'cv-muted' }, `${c.league}`),
      c.team_abbrev ? el('span', {}, c.team_abbrev) : null),
    el('div', { class: 'cv-roll-owner cv-muted' },
      `${owner} · joined ${fmtDate(c.created_at)}`));
}

function render() {
  const box = $('#roll');
  clear(box);
  const rows = (filter === 'all' ? everyone : everyone.filter((c) => c.league === filter))
    .slice()
    .sort((a, b) => {
      // Placed players first: somebody still waiting for a roster spot has no stats to
      // compare against anybody, and burying the active ones under them reads as broken.
      const placed = (x) => (x.status === 'active' || x.status === 'declared' ? 0 : 1);
      return placed(a) - placed(b) || strengthOf(b) - strengthOf(a);
    });

  if (!rows.length) {
    box.append(el('p', { class: 'cv-muted' },
      filter === 'all'
        ? 'Nobody has made a player yet. Be the first.'
        : `Nobody is in ${filter} yet.`));
    return;
  }
  for (const c of rows) box.append(row(c));
}
