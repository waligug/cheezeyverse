/* The hub. Public: it renders for a signed-out visitor, because the league sites are
   public too and the whole point is being able to show somebody the universe. */

import {
  isConfigured, leagueSites, config, signIn, signOut, currentUser, ensureProfile,
  recentCharacters, characterCounts, errorText,
  oauthErrorFromUrl,
} from './supabase.js';
import {
  el, $, clear, renderChrome, renderFooter, setupNeededNote, showNote, statusPill,
  describeCharacter, fmtDate,
} from './ui.js';

const LEAGUES = [
  { key: 'prep', name: 'Cheezeyverse Prep', sub: 'Ages 14 to 17 · 30 games' },
  { key: 'college', name: 'Cheezeyverse College', sub: 'Ages 18 to 21 · 32 games' },
  { key: 'pro', name: 'The Cheezeyverse', sub: 'The pros · 58 games' },
];

const chrome = renderChrome({
  active: 'index.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});

const cfg = config();
if (cfg.tagline) $('#tagline').textContent = cfg.tagline;
document.title = cfg.universeName || 'The Cheezeyverse';

renderLeagues();
renderFooter();

/* An OAuth failure comes back in the URL, not as an exception - see oauthErrorFromUrl().
   Read it before anything else so the page can say what happened instead of just looking
   signed out. This runs even when the Supabase client is never constructed. */
const cvOauthError = oauthErrorFromUrl();
if (cvOauthError) showNote($('#notices'), 'bad', `Discord sign-in failed: ${cvOauthError}`);

if (!isConfigured()) {
  clear($('#recent'));
  $('#notices').append(setupNeededNote());
} else {
  boot().catch((err) => showNote($('#notices'), 'bad', errorText(err)));
}

function renderLeagues() {
  const sites = leagueSites();
  const box = $('#leagues');
  clear(box);
  for (const league of LEAGUES) {
    const site = sites[league.key];
    const tile = site.ready
      ? el('a', { class: 'cv-league cv-cheese', href: site.url })
      : el('span', { class: 'cv-league cv-cheese is-placeholder',
        title: 'Add this URL to site/config.js once the league site is published' });
    tile.append(el('b', {}, league.name), el('span', {}, site.ready ? league.sub : 'not published yet'));
    box.append(tile);
  }
}

async function boot() {
  const user = await currentUser();
  const profile = user ? await ensureProfile() : null;
  chrome.refresh(user, profile);

  const [rows, counts] = await Promise.all([recentCharacters(12), characterCounts()]);

  const census = $('#census');
  const live = counts.live;
  census.textContent = live
    ? `${live} live character${live === 1 ? '' : 's'} · `
      + LEAGUES.map((l) => `${counts.byLeague[l.key] || 0} ${l.key}`).join(' · ')
    : '';

  const box = $('#recent');
  clear(box);
  if (!rows.length) {
    box.append(el('p', {}, 'Nobody yet. ',
      el('a', { href: 'create.html' }, 'Be the first.')));
    return;
  }
  // Heights on this list are computed for the current season, not the stored
  // fourteen-year-old column, or everyone stays 14 forever on the public page.
  const seasonNow = (cfg && cfg.current_season) || null;
  const list = el('ul', { class: 'cv-roster' });
  for (const c of rows) {
    const owner = (c.owner_profile && c.owner_profile.display_name) || 'someone';
    list.append(el('li', {},
      el('b', {},
        el('a', { href: `career.html?id=${encodeURIComponent(c.id)}` },
          `${c.first_name} ${c.last_name}`)),
      statusPill(c.status),
      el('span', { class: 'cv-muted' },
        `${describeCharacter(c, seasonNow)} · ${owner} · ${fmtDate(c.created_at)}`)));
  }
  box.append(list);
}
