import { el } from './ui.js';

export const STORY_LABELS = {
  career_high: 'Career highs', hot_streak: 'Hot streak', cold_streak: 'Cold streak',
  disaster: 'Ugly tape', trade: 'Trade', award: 'Award', award_race: 'MVP watch',
  playoff: 'Playoffs', rivalry: 'Rivalry',
};

const LEAGUES = { prep: 'Prep', college: 'College', pro: 'Pro' };

export function storyCard(event, players, { compact = false } = {}) {
  const card = el('article', { class: `cv-story is-${event.tone || 'neutral'}` });
  const meta = [];
  if (event.league) meta.push(LEAGUES[event.league] || event.league);
  if (event.season) meta.push(`Season ${event.season}`);
  if (event.day) meta.push(`Day ${event.day}`);
  if (event.date) meta.push(event.date);
  card.append(el('div', { class: 'cv-story-kicker' },
    el('span', { class: `cv-story-kind is-${event.type}` }, STORY_LABELS[event.type] || event.type),
    el('span', { class: 'cv-muted' }, meta.join(' · '))));
  card.append(el('h3', {}, event.title), el('p', {}, event.detail));
  if (event.game) card.append(el('p', { class: 'cv-hint' }, `Scoring-high game: ${event.game}.`));
  if (!compact && event.character_ids && event.character_ids.length) {
    const people = el('p', { class: 'cv-story-people' });
    event.character_ids.forEach((id, i) => {
      if (i) people.append(' · ');
      const person = players[id];
      people.append(person
        ? el('a', { href: `career.html?id=${encodeURIComponent(id)}` }, person.name)
        : el('span', {}, event.player || 'Player'));
    });
    card.append(people);
  }
  return card;
}
