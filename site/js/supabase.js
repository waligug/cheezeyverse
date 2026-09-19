/* =====================================================================================
 * Supabase client, Discord sign-in, and every query the site makes.
 *
 * The client is created from window.CV_CONFIG (site/config.js), which is loaded as a
 * classic script before any module, so it is always there by the time this runs. The key
 * in that file is the *anon* key: public by design, and harmless because every table is
 * behind the Row Level Security policies in supabase/schema.sql.
 *
 * Nothing in the browser writes points, status or claimed_slot. Spending happens by
 * inserting an `upgrade_requests` row, which the database prices itself and the local
 * commissioner app applies. See the trust model comment at the top of schema.sql.
 * ===================================================================================== */

import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm';

const CFG = (typeof window !== 'undefined' && window.CV_CONFIG) || {};

/**
 * The character columns the anon/authenticated roles are granted. `claimed_slot` is
 * deliberately not among them, so `select('*')` would be a permission error - always ask
 * for this list instead.
 */
export const CHARACTER_COLUMNS = [
  'id', 'owner', 'first_name', 'last_name', 'position', 'height_inches', 'archetype',
  'league', 'team_abbrev', 'status', 'game_dob', 'ratings', 'potentials',
  'points_available', 'points_spent', 'created_at',
  // the creation redesign: everything the quiz produced, plus the height model's cache
  'jersey_preference', 'hometown', 'build', 'career_goal', 'traits', 'quiz_answers',
  'growth_bias', 'height_seed', 'expected_adult_height',
  // the thread between levels, written by the commissioner: without these the career page
  // cannot name a team he has left or link to his page on that league's site
  'college_years', 'declared', 'level_history', 'league_player_ids',
  'draft_round', 'draft_pick', 'draft_season',
  // how the career ended, both null while he is still playing
  'retired_season', 'retired_reason',
].join(',');

export const LEAGUE_LABELS = { prep: 'Prep', college: 'College', pro: 'Pro' };

export class ConfigError extends Error {}

function looksLikePlaceholder(value) {
  const s = String(value || '');
  return !s || /YOUR-|PASTE-|example\.(com|org)|YOUR_/i.test(s);
}

/** True once site/config.js has real values in it. */
export function isConfigured() {
  return !looksLikePlaceholder(CFG.supabaseUrl) && !looksLikePlaceholder(CFG.supabaseAnonKey);
}

export function config() { return CFG; }

/** Which league sites actually have a URL yet. */
export function leagueSites() {
  const out = {};
  for (const key of ['prep', 'college', 'pro']) {
    const url = (CFG.leagueSites || {})[key] || '';
    out[key] = { url, ready: !looksLikePlaceholder(url) };
  }
  return out;
}

let _client = null;

export function client() {
  if (!isConfigured()) {
    throw new ConfigError(
      'site/config.js still has its placeholder values. Put your Supabase project URL and '
      + 'anon key in there and reload.',
    );
  }
  if (!_client) {
    _client = createClient(CFG.supabaseUrl, CFG.supabaseAnonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return _client;
}

/* ------------------------------------------------------------------------------- auth */

/** Discord OAuth through Supabase Auth. Comes back to whatever page called it. */
export async function signIn(returnTo) {
  const redirectTo = returnTo || window.location.href.split('#')[0];
  const { error } = await client().auth.signInWithOAuth({
    provider: 'discord',
    options: { redirectTo },
  });
  if (error) throw error;
}

export async function signOut() {
  await client().auth.signOut();
  window.location.reload();
}

export async function session() {
  if (!isConfigured()) return null;
  const { data } = await client().auth.getSession();
  return data.session || null;
}

export async function currentUser() {
  const s = await session();
  return s ? s.user : null;
}

export function onAuthChange(fn) {
  if (!isConfigured()) return () => {};
  const { data } = client().auth.onAuthStateChange((_event, s) => fn(s ? s.user : null));
  return () => data.subscription.unsubscribe();
}

/**
 * Make sure this user has a profiles row, and say so loudly if he has not.
 *
 * `cv_ensure_profile()` is the ONLY way a profile row can be created from the browser, by
 * design: schema.sql revokes insert on `profiles` from authenticated and has no insert policy,
 * so the security-definer RPC is the whole path. The second query here is a SELECT - it reads,
 * it does not create - so if the RPC fails there is no fallback, only a diagnosis.
 *
 * That matters more than it looks. `characters.owner` references `profiles(id)`, and nothing
 * else on the way notices a missing profile: the header still shows the right Discord name from
 * user_metadata, the character list comes back empty, and the create form opens happily. The
 * first sign of trouble was a raw foreign-key violation AFTER answering all fourteen questions.
 * Returning null quietly is how that happens, so this throws instead.
 */
export async function ensureProfile() {
  const user = await currentUser();
  if (!user) return null;
  const { data, error } = await client().rpc('cv_ensure_profile');
  if (!error && data) return Array.isArray(data) ? data[0] : data;
  const { data: rows, error: readError } = await client()
    .from('profiles').select('id,display_name,discord_username,is_admin').eq('id', user.id).limit(1);
  if (rows && rows[0]) return rows[0];
  const why = (error && error.message) || (readError && readError.message) || 'no row came back';
  console.error('cv_ensure_profile failed:', error || readError);
  throw new Error(
    `Signed in, but your player profile could not be set up (${why}). `
    + 'Nothing you create would save. Tell the commissioner before going further.');
}

/**
 * What the OAuth round trip said when it came back, or null.
 *
 * supabase-js defaults to the implicit flow, so a failure returns in the URL FRAGMENT
 * (`#error=...&error_description=...`) rather than as a thrown error. The client parses that
 * fragment during initialize(), throws internally, stores the result on a promise that
 * `getSession()` awaits and then discards - so the page simply renders signed-out with no
 * explanation and the only evidence is the address bar.
 *
 * Worse, `signInWithOAuth` never returns an error at all: it builds the authorize URL locally
 * and calls location.assign, so every `if (error) throw error` around the sign-in button is
 * unreachable. Reading the URL is the only way the site can tell anybody what went wrong.
 */
export function oauthErrorFromUrl() {
  const read = (qs) => qs.get('error_description') || qs.get('error');
  const found = read(new URLSearchParams(location.hash.replace(/^#/, '')))
    || read(new URLSearchParams(location.search));
  if (!found) return null;
  try {
    return decodeURIComponent(found.replace(/\+/g, ' '));
  } catch {
    return found;
  }
}


export function displayNameOf(user, profile) {
  if (profile && profile.display_name) return profile.display_name;
  const meta = (user && user.user_metadata) || {};
  return meta.custom_claims?.global_name || meta.full_name || meta.name
    || meta.user_name || meta.preferred_username || 'Cheezeyverse GM';
}

/* --------------------------------------------------------------------------- settings */

const SETTING_DEFAULTS = {
  max_characters: 2,
  starting_points: 20,
  points_per_week: 1,
  // Income scales with level because the cost curve does: a step costs 1 under 50, 2 from
  // 50-69, 3 from 70-84, and characters sit in those bands at prep, college and pro. Without
  // per-level rates a promotion quietly halves what a season buys. See commissioner/points.py,
  // which is where the rule actually lives; these are only for display.
  points_per_week_prep: 1,
  points_per_week_college: 2,
  points_per_week_pro: 3,
  auto_approve: false,
  current_season: 2026,
  current_week: 0,
};

/** Points a simmed week is worth at `league`, falling back to the flat rate. Mirrors
 *  commissioner/points.py per_week(); display only, since only the commissioner grants them. */
export function pointsPerWeek(settings, league) {
  const s = settings || {};
  for (const key of [league ? `points_per_week_${league}` : null, 'points_per_week']) {
    if (!key || s[key] === undefined || s[key] === null) continue;
    const n = Number(s[key]);
    if (Number.isFinite(n) && n >= 0) return n;
  }
  return 1;
}

let _settings = null;

/** The settings table as a plain object, with the documented defaults filled in. */
export async function settings(force = false) {
  if (_settings && !force) return _settings;
  const out = { ...SETTING_DEFAULTS };
  try {
    const { data, error } = await client().from('settings').select('key,value');
    if (!error && data) for (const row of data) out[row.key] = row.value;
  } catch (err) {
    // an unreachable project should not stop the form rendering with its defaults
    console.warn('settings unavailable, using defaults', err);
  }
  _settings = out;
  return out;
}

/* ------------------------------------------------------------------------- characters */

export async function recentCharacters(limit = 12) {
  const { data, error } = await client()
    .from('characters')
    .select(`${CHARACTER_COLUMNS},owner_profile:profiles(display_name)`)
    .order('created_at', { ascending: false })
    .limit(limit);
  if (error) throw error;
  return data || [];
}

/**
 * Every character in the universe, for the roll call. Public, like the league sites.
 *
 * `league_player_ids` is what makes each name a link into the generated league site, so it is
 * selected explicitly - CHARACTER_COLUMNS leaves it out, and its absence reads as "never
 * placed" rather than as "not asked for".
 */
export async function allCharacters() {
  const { data, error } = await client()
    .from('characters')
    .select(`${CHARACTER_COLUMNS},league_player_ids,owner_profile:profiles(display_name)`)
    .order('created_at', { ascending: true });
  if (error) throw error;
  return data || [];
}

export async function characterCounts() {
  const { data, error } = await client().from('characters').select('id,status,league');
  if (error) throw error;
  const rows = data || [];
  return {
    total: rows.length,
    live: rows.filter((c) => c.status !== 'retired').length,
    byLeague: rows.reduce((acc, c) => {
      if (c.status === 'active' || c.status === 'declared') acc[c.league] = (acc[c.league] || 0) + 1;
      return acc;
    }, {}),
  };
}

/**
 * One character by id, for career.html. Public: `characters_read` in schema.sql grants SELECT to
 * anon, so anybody can look at anybody's career - the same posture the league sites have. His
 * points history is not public and is fetched separately (see requestsFor / ledgerFor, which RLS
 * limits to the owner).
 *
 * Returns null rather than throwing when the id does not exist, because a mistyped link is a
 * normal thing for a page to handle and not an error worth a red box.
 */
export async function characterById(characterId) {
  if (!characterId) return null;
  const { data, error } = await client()
    .from('characters')
    // The snapshot embed lives here and NOT in CHARACTER_COLUMNS: that list is also used by the
    // front page's roll call and by the insert that creates a character, and either would pull
    // hundreds of history rows it has no use for.
    .select(`${CHARACTER_COLUMNS},owner_profile:profiles(display_name),`
      + 'rating_snapshots(season,week,ratings,potentials,height_inches,league,taken_at)')
    .eq('id', characterId)
    .limit(1);
  if (error) throw error;
  return (data && data[0]) || null;
}

export async function myCharacters() {
  const user = await currentUser();
  if (!user) return [];
  const { data, error } = await client()
    .from('characters')
    .select(CHARACTER_COLUMNS)
    .eq('owner', user.id)
    .order('created_at', { ascending: true });
  if (error) throw error;
  return data || [];
}

/**
 * Insert a new character with status 'pending'. The database trigger pins owner, status,
 * points_available and the save-file plumbing, so the only things that actually travel
 * from this form are the identity fields, the quiz and the sheet the quiz derived.
 *
 * `points_spent` is always 0 now: creation does not spend, so the whole opening budget
 * arrives banked and gets spent on me.html.
 *
 * `archetype` is kept as the *class he looked like on the day he was made*. It is a
 * snapshot for the record - nothing reads it for a rule. Every live readout calls
 * rules.classify() on the current sheet instead.
 *
 * `height_seed` is not sent: it is derived from the character's id, which the database
 * generates during this insert, and the commissioner caches it on the row afterwards.
 */
export async function createCharacter(build) {
  const user = await currentUser();
  if (!user) throw new Error('Sign in with Discord first.');
  const d = build.derived;
  const row = {
    owner: user.id,
    first_name: build.firstName.trim(),
    last_name: build.lastName.trim(),
    position: build.position,
    height_inches: Number(build.heightInches),
    archetype: d.klass.id,
    ratings: d.ratings,
    potentials: d.potentials,
    points_spent: 0,
    league: 'prep',
    jersey_preference: Number(build.jersey),
    hometown: String(build.hometown || '').trim(),
    build: build.build,
    career_goal: build.goal,
    traits: d.traits,
    // the fourteen answers plus the summer, which is a question in everything but name.
    // `build` and `career_goal` have columns of their own, so they are not repeated here.
    quiz_answers: { ...build.answers, summer: build.summer },
    growth_bias: d.bias,
    expected_adult_height: Number(d.expectedAdultHeight),
  };
  const { data, error } = await client()
    .from('characters').insert(row).select(CHARACTER_COLUMNS).single();
  if (error) throw error;
  return data;
}

export async function deletePendingCharacter(characterId) {
  const { error } = await client().from('characters').delete().eq('id', characterId);
  if (error) throw error;
}

export async function declareForDraft(characterId) {
  const { data, error } = await client().rpc('declare_for_draft', { p_character: characterId });
  if (error) throw error;
  return data;
}

/* -------------------------------------------------------------------- upgrade requests */

export async function requestsFor(characterIds) {
  const ids = [].concat(characterIds || []);
  if (!ids.length) return [];
  const { data, error } = await client()
    .from('upgrade_requests')
    .select('id,character_id,rating,delta,kind,cost,status,requested_at,applied_at,note')
    .in('character_id', ids)
    .order('requested_at', { ascending: false });
  if (error) throw error;
  return data || [];
}

/**
 * File a spend. `cost` is deliberately not sent: the BEFORE INSERT trigger computes it
 * from the character's real ratings plus whatever is already queued, and would overwrite
 * anything we passed anyway.
 */
export async function requestUpgrade({ characterId, rating, delta, kind = 'rating', note = null }) {
  const { data, error } = await client()
    .from('upgrade_requests')
    .insert({ character_id: characterId, rating, delta: Number(delta), kind, note })
    .select('id,character_id,rating,delta,kind,cost,status,requested_at')
    .single();
  if (error) throw error;
  return data;
}

export async function cancelRequest(requestId) {
  const { error } = await client().from('upgrade_requests').delete().eq('id', requestId);
  if (error) throw error;
}

export async function ledgerFor(characterIds) {
  const ids = [].concat(characterIds || []);
  if (!ids.length) return [];
  const { data, error } = await client()
    .from('point_ledger')
    .select('id,character_id,amount,reason,created_at')
    .in('character_id', ids)
    .order('created_at', { ascending: false })
    .limit(200);
  if (error) throw error;
  return data || [];
}

/** Supabase errors are objects, not Errors. Turn one into something worth showing. */
export function errorText(err) {
  if (!err) return 'Something went wrong.';
  if (err instanceof ConfigError) return err.message;
  const msg = err.message || err.error_description || err.hint || String(err);
  // postgres RAISE EXCEPTION text arrives with this prefix from PostgREST
  return String(msg).replace(/^ERROR:\s*/i, '');
}
