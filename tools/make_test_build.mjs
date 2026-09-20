/* Build one character through the REAL site rules, and print the row the website would insert.
 *
 * The point is that nothing here re-implements anything: it imports site/js/rules.js, answers
 * the quiz the way a person would, and runs deriveCharacter - so if the browser and the
 * commissioner ever disagree about what a set of answers produces, this catches it rather than
 * a friend noticing his player is wrong.
 *
 *     node tools/make_test_build.mjs > build.json
 *     node tools/make_test_build.mjs --answers all-in
 */
import { QUIZ, QUIZ_IDS, deriveCharacter, CAREER_GOALS, SUMMER_WORK, BUILDS }
  from '../site/js/rules.js';

/* Answer every question with the option at `pick` through its list, so the same flag always
   produces the same player and a regression is visible as a diff. */
function answerAll(pick) {
  const answers = {};
  for (const q of QUIZ) {
    const opts = q.options || q.choices || [];
    if (!opts.length) { answers[q.id] = null; continue; }
    const i = Math.min(opts.length - 1, Math.max(0, pick(q, opts)));
    answers[q.id] = opts[i].id ?? opts[i].value ?? i;
  }
  return answers;
}

const which = process.argv.includes('--answers')
  ? process.argv[process.argv.indexOf('--answers') + 1] : 'middle';

const pickers = {
  middle: (q, o) => Math.floor(o.length / 2),
  first: () => 0,
  last: (q, o) => o.length - 1,
};
const answers = answerAll(pickers[which] || pickers.middle);

const state = {
  id: 'e2e-test-character',
  firstName: 'Testy',
  lastName: 'Mcslot',
  position: 'SG',
  heightInches: 72,
  jersey: 14,
  hometown: 'Wetaskiwin',
  build: BUILDS[1]?.id ?? BUILDS[0].id,
  goal: CAREER_GOALS[0].id,
  summer: SUMMER_WORK[0].id,
  answers,
};

const d = deriveCharacter(state);

/* Exactly the shape site/js/supabase.js createCharacter() inserts, minus `owner`, which is
   whoever is signed in. */
const row = {
  first_name: state.firstName,
  last_name: state.lastName,
  position: state.position,
  height_inches: Number(state.heightInches),
  archetype: d.klass.id,
  ratings: d.ratings,
  potentials: d.potentials,
  points_spent: 0,
  league: 'prep',
  jersey_preference: Number(state.jersey),
  hometown: state.hometown,
  build: state.build,
  // No slider out here, so this is where it would be sitting: the suggestion for his height
  // and build. The point is that the KEY is present - a row without it is not the row the
  // website sends, and the whole reason this file exists is that it is.
  weight_lbs: Number(d.weightLbs),
  career_goal: state.goal,
  traits: d.traits,
  quiz_answers: { ...state.answers, summer: state.summer },
  growth_bias: d.bias,
  expected_adult_height: Number(d.expectedAdultHeight),
};

console.log(JSON.stringify({
  row,
  readout: {
    klass: d.klass.label,
    questionsAnswered: QUIZ_IDS.length,
    expectedAdultHeight: d.expectedAdultHeight,
    weightLbs: d.weightLbs,
    ratingMin: Math.min(...Object.values(d.ratings)),
    ratingMax: Math.max(...Object.values(d.ratings)),
    potentialMin: Math.min(...Object.values(d.potentials)),
    potentialMax: Math.max(...Object.values(d.potentials)),
  },
}, null, 1));
