import {
  $, el, clear, renderChrome, renderFooter, freshJSON, showNote,
} from './ui.js';
import {
  isConfigured, signIn, signOut, currentUser, ensureProfile, errorText,
} from './supabase.js';
import { STORY_LABELS, storyCard } from './story-ui.js';

const chrome = renderChrome({
  active: 'stories.html',
  onSignIn: () => signIn().catch((e) => showNote($('#notices'), 'bad', errorText(e))),
  onSignOut: () => signOut(),
});
renderFooter();

let data = { events: [], players: {} };

function option(value, label) { return el('option', { value }, label); }

function fillFilters() {
  const people = Object.entries(data.players || {}).sort((a, b) => a[1].name.localeCompare(b[1].name));
  for (const [id, person] of people) $('#player').append(option(id, person.name));
  const types = [...new Set(data.events.map((event) => event.type))];
  for (const type of Object.keys(STORY_LABELS).filter((key) => types.includes(key))) {
    $('#type').append(option(type, STORY_LABELS[type]));
  }
  const seasons = [...new Set(data.events.map((event) => event.season).filter(Boolean))].sort((a, b) => b - a);
  for (const season of seasons) $('#season').append(option(String(season), `Season ${season}`));
}

function draw() {
  const player = $('#player').value;
  const type = $('#type').value;
  const season = $('#season').value;
  const events = data.events.filter((event) => (!player || event.character_ids.includes(player))
    && (!type || event.type === type) && (!season || String(event.season) === season));
  $('#count').textContent = `${events.length} ${events.length === 1 ? 'story' : 'stories'}`;
  const root = $('#stories');
  clear(root);
  if (!events.length) {
    root.append(el('section', { class: 'cv-card' }, el('h2', {}, 'Nothing in that slice yet'),
      el('p', {}, 'The feed grows after every published sim as games, honours and transactions enter the record.')));
    return;
  }
  events.forEach((event) => root.append(storyCard(event, data.players)));
}

async function boot() {
  if (isConfigured()) {
    currentUser().then(async (user) => chrome.refresh(user, user ? await ensureProfile() : null)).catch(() => {});
  }
  const loaded = await freshJSON('data/stories.json');
  $('#loading').hidden = true;
  if (!loaded) {
    showNote($('#notices'), 'bad', 'The story feed has not been published yet.');
    return;
  }
  data = loaded;
  fillFilters();
  const wanted = new URLSearchParams(window.location.search).get('player');
  if (wanted && data.players && data.players[wanted]) $('#player').value = wanted;
  ['player', 'type', 'season'].forEach((id) => $(`#${id}`).addEventListener('change', draw));
  draw();
}

boot().catch((err) => {
  $('#loading').hidden = true;
  showNote($('#notices'), 'bad', errorText(err));
});
