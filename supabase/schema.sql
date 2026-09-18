-- =====================================================================================
-- Cheezeyverse - Supabase schema (identity, characters, skill points)
-- =====================================================================================
--
-- CHANGED BY THE PLAYER-CREATION REDESIGN (edit in place, nothing is deployed yet)
--   * public.characters gains nine columns: jersey_preference, hometown, build,
--     career_goal, traits, quiz_answers, growth_bias, height_seed, expected_adult_height.
--     They are also written as `alter table ... add column if not exists` just below the
--     create table, so re-running this file on an existing project is still safe.
--   * `archetype` is NOT dropped. It stops being a rule and becomes a record: the class
--     label the character looked like on the day he was made. Nothing in SQL reads it, and
--     the website recomputes the live label from the ratings on every render.
--   * cv_upgrade_request_guard() now applies the character's growth bias to the finished
--     cost. The curve functions (cv_step_cost / cv_upgrade_cost) are untouched - the bias
--     is a separate, clamped multiply applied afterwards. See the note on that trigger.
--   * There is no starting spend any more. Creation always inserts points_spent = 0 and
--     the whole opening budget arrives banked.
--
-- HOW TO APPLY
--   1. Supabase dashboard -> SQL Editor -> New query.
--   2. Paste this whole file and Run. It is idempotent: every object is created with
--      `if not exists` / `create or replace`, and every policy and trigger is dropped
--      first, so re-running it after an edit is safe.
--   3. Then run `supabase/seed.sql` to install the default settings rows (the same
--      statements are repeated in the SEED section at the bottom of this file).
--   4. Make yourself an admin once you have signed in with Discord at least once:
--        update public.profiles set is_admin = true where discord_username = 'yourname';
--
-- WHICH KEY GOES WHERE
--   * anon key  -> `site/config.js`. It is public by design; the RLS below is what
--                  protects the data. Never put the service role key in `site/`.
--   * service role key -> the commissioner app only, via the `.env` file in the project
--                  root (`SUPABASE_SERVICE_KEY`, read by `commissioner/settings.py`).
--                  The service role bypasses RLS, so it is the only thing that may write
--                  `points_available`, `status`, `claimed_slot` or `point_ledger`.
--
-- THE TRUST MODEL, STATED PLAINLY
--   * Signed-in users may INSERT their own characters and their own upgrade_requests,
--     and may UPDATE nothing on a character except its first/last name. Column-level
--     grants (not RLS `with check`, which cannot see OLD) are what enforce that.
--   * A user never moves points. Creating an upgrade_request only *reserves* its cost;
--     `points_available` and the ratings themselves change when the commissioner applies
--     the request through `apply_upgrade_requests()` with the service role. That is why
--     upgrade_requests carries both `status` and `applied_at`.
--   * The cost curve is duplicated in SQL (`cv_step_cost` / `cv_upgrade_cost`) and in
--     `site/js/rules.js`. The SQL copy is authoritative: the BEFORE INSERT trigger
--     recomputes `cost` from the character's real ratings and ignores whatever the
--     client sent. `tests/test_rules.py` pins the JS copy to the same numbers.
--   * There are no archetype caps left anywhere: a rating is held by its own potential and
--     a potential by 100. The opening ratings, potentials and `growth_bias` are all
--     DERIVED IN THE BROWSER from the quiz answers by site/js/rules.js, and SQL cannot
--     re-derive them (it would need the whole quiz table). SQL therefore range-checks them
--     and clamps the bias to 85..115, and the commissioner eyeballs `pending_characters()`
--     before activating. That is the same trust posture the ratings block has always had,
--     and it is proportionate for a friends-only league - but the bias is a NEW surface, so
--     it is worth saying out loud: a determined cheat could send 85 on all eighteen and
--     make every point 15% cheaper. The clamp is what stops it being worse than that.
--
-- =====================================================================================

begin;

create extension if not exists pgcrypto;

-- -------------------------------------------------------------------------------------
-- Tables
-- -------------------------------------------------------------------------------------

-- One row per Discord user. Created automatically by the auth.users trigger below, with
-- `cv_ensure_profile()` as a belt-and-braces fallback the website calls after sign-in.
create table if not exists public.profiles (
  id               uuid primary key references auth.users(id) on delete cascade,
  discord_username text,
  display_name     text,
  is_admin         boolean     not null default false,
  created_at       timestamptz not null default now()
);

-- A character is a person's player. It starts 'pending' (no slot in any save yet), and
-- the commissioner turns it 'active' by claiming a reserve slot in the FBPB3 save.
create table if not exists public.characters (
  id               uuid primary key default gen_random_uuid(),
  owner            uuid not null references public.profiles(id) on delete cascade,
  first_name       text not null check (length(btrim(first_name)) between 1 and 20),
  last_name        text not null check (length(btrim(last_name))  between 1 and 20),
  -- The position he is LISTED at. FBPB3's AI coach builds the depth charts and will play
  -- him somewhere else whenever it suits the roster, so this is a label, not a contract,
  -- and nothing in this file gates on it.
  "position"       text not null check ("position" in ('C','PF','SF','SG','PG')),
  -- Height AT FOURTEEN. He grows from here, every offseason, by commissioner/growth.py.
  height_inches    int  not null check (height_inches between 60 and 95),
  -- The class he looked like the day he was created. A record, not a rule - see the note
  -- at the top of this file.
  archetype        text not null,
  league           text not null default 'prep'    check (league in ('prep','college','pro')),
  team_abbrev      text,
  -- Seasons FINISHED at college. It drives two things: the four-year eligibility cap, and how
  -- much of his ratings survive the move to the pros (see commissioner/offseason.py). The
  -- offseason writes it; nothing else may, which is why it is service-role only below.
  college_years    int  not null default 0 check (college_years between 0 and 8),
  -- He has asked for the draft. The offseason acts on it at the next rollover.
  declared         boolean not null default false,
  -- Each league's generated site knows a player only inside that league, so the thread between
  -- Prep, College and Pro is ours to keep. One row per level, appended by the commissioner:
  --   {"level":"prep","team_abbrev":"SAS","player_id":53,"from_season":2030,"to_season":2033,
  --    "how_it_started":"created","how_it_ended":"aged out of prep","carried":0.97}
  level_history    jsonb not null default '[]'::jsonb,
  -- His FBPB3 player id per league, which is what a direct link to a player page needs.
  league_player_ids jsonb not null default '{}'::jsonb,
  draft_round      int,
  draft_pick       int,
  draft_season     int,
  status           text not null default 'pending' check (status in ('pending','active','declared','retired')),
  game_dob         date,
  -- all 18 ratings, keyed by the names in cv_ratings()
  ratings          jsonb not null default '{}'::jsonb,
  -- the 12 that have potentials, keyed by the names in cv_potentials()
  potentials       jsonb not null default '{}'::jsonb,
  points_available int  not null default 0 check (points_available >= 0),
  points_spent     int  not null default 0 check (points_spent     >= 0),
  -- Which dormant reserve row in which save this character occupies:
  --   {"league":"prep","team":"BKI","name":"Milo Trask","dob":"3/14/2016"}
  -- matching an entry in universe/manifest.json. Service role only, and not readable by
  -- anon/authenticated (see the column grants below).
  claimed_slot     jsonb,

  -- ---- player creation: what the quiz produced ----------------------------------------
  -- The number he WANTS. He has no team at creation (the commissioner claims a dormant
  -- reserve slot for him at the next sim), so uniqueness inside a team cannot be checked
  -- here and is not checked here. The commissioner resolves collisions when it puts him on
  -- a roster; whatever ends up in league.dat is the number he actually wears.
  jersey_preference int check (jersey_preference between 0 and 99),
  hometown         text check (hometown is null or length(btrim(hometown)) between 1 and 40),
  -- one of BUILDS in site/js/rules.js: wiry / lean / solid / strong / heavy. Weight in lbs
  -- is derived from height_inches + build, not stored.
  build            text,
  -- one of CAREER_GOALS in site/js/rules.js. Flavour, plus a 5% discount on the four
  -- ratings it names, which is already folded into growth_bias below.
  career_goal      text,
  -- the eleven-name trait vector the quiz answers produced, each 0-100
  traits           jsonb not null default '{}'::jsonb,
  -- {questionId: answerId} for the fourteen questions, plus "summer". Kept so a character
  -- can be re-derived if the quiz is ever re-tuned, and because reading somebody else's
  -- answers is half the fun. `build` and `career_goal` have columns of their own.
  quiz_answers     jsonb not null default '{}'::jsonb,
  -- {rating: percent}, 85..115. Applied to the finished cost of every upgrade request.
  growth_bias      jsonb not null default '{}'::jsonb,
  -- Cache of heightSeed(id) - the 32-bit FNV-1a of this row's own id, which is what seeds
  -- the growth PRNG. It cannot be supplied at insert (the id does not exist yet), so it is
  -- service-role only: the commissioner fills it the first time it grows him, purely so
  -- the number is visible in the database. The model never depends on the column.
  height_seed      bigint,
  -- Where the model expects him to finish, in inches. Pure function of height_inches and
  -- traits->>'height_genes', so it is safe to store at creation and show on the form. It
  -- is an expectation, not a cap: growth past it is possible and never guaranteed.
  expected_adult_height int check (expected_adult_height is null
                                   or expected_adult_height between 60 and 110),

  created_at       timestamptz not null default now()
);

-- Older deployments: bring them up to date without dropping anything.
alter table public.characters add column if not exists ratings    jsonb not null default '{}'::jsonb;
alter table public.characters add column if not exists potentials jsonb not null default '{}'::jsonb;
alter table public.characters add column if not exists jersey_preference int;
alter table public.characters add column if not exists hometown          text;
alter table public.characters add column if not exists build             text;
alter table public.characters add column if not exists career_goal       text;
alter table public.characters add column if not exists traits       jsonb not null default '{}'::jsonb;
alter table public.characters add column if not exists quiz_answers jsonb not null default '{}'::jsonb;
alter table public.characters add column if not exists growth_bias  jsonb not null default '{}'::jsonb;
alter table public.characters add column if not exists height_seed  bigint;
alter table public.characters add column if not exists expected_adult_height int;

create index if not exists characters_owner_idx   on public.characters(owner);
create index if not exists characters_status_idx  on public.characters(status);
create index if not exists characters_created_idx on public.characters(created_at desc);

-- A request to spend points. `cost` is computed by the trigger, never by the client.
create table if not exists public.upgrade_requests (
  id           uuid primary key default gen_random_uuid(),
  character_id uuid not null references public.characters(id) on delete cascade,
  rating       text not null,
  delta        int  not null check (delta > 0 and delta <= 40),
  kind         text not null default 'rating' check (kind in ('rating','potential')),
  cost         int  not null default 0 check (cost >= 0),
  status       text not null default 'pending' check (status in ('pending','approved','applied','rejected')),
  requested_at timestamptz not null default now(),
  applied_at   timestamptz,
  note         text
);

create index if not exists upgrade_requests_char_idx   on public.upgrade_requests(character_id);
create index if not exists upgrade_requests_status_idx on public.upgrade_requests(status);

-- Every point a character ever received or spent. Grants are positive and spends are
-- negative, so sum(amount) for a character always equals its points_available.
create table if not exists public.point_ledger (
  id           uuid primary key default gen_random_uuid(),
  character_id uuid not null references public.characters(id) on delete cascade,
  amount       int  not null,
  reason       text not null,
  created_at   timestamptz not null default now()
);

create index if not exists point_ledger_char_idx on public.point_ledger(character_id, created_at desc);

-- One row per config key. See the SEED section at the bottom for the defaults.
create table if not exists public.settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);

-- -------------------------------------------------------------------------------------
-- Rating vocabulary + the cost curve (mirrored in site/js/rules.js)
-- -------------------------------------------------------------------------------------

-- The 18 ratings, in the order FBPB3 stores them (CONVENTIONS.md, ratings block R+0).
create or replace function public.cv_ratings() returns text[]
language sql immutable as $fn$
  select array[
    'InsideScoring','JumpShot','FtShot','3pUsage','3pShot','Handling','Passing','Quickness',
    'PostDefense','PerimeterDefense','Stealing','Blocking','OReb','DReb','Jumping','Strength',
    'Stamina','Fouling']::text[];
$fn$;

-- The 12 that have a potential, in the order FBPB3 stores them (R+168).
create or replace function public.cv_potentials() returns text[]
language sql immutable as $fn$
  select array[
    'InsideScoring','JumpShot','FtShot','3pShot','Handling','Passing',
    'OReb','DReb','PostDefense','PerimeterDefense','Stealing','Blocking']::text[];
$fn$;

-- Cost of the single step that *leaves* value v.
--   below 50 -> 1,   50-69 -> 2,   70-84 -> 3,   85 and up -> 5
create or replace function public.cv_step_cost(v int) returns int
language sql immutable as $fn$
  select case when v < 50 then 1 when v < 70 then 2 when v < 85 then 3 else 5 end;
$fn$;

-- Cost of raising a value from p_from by p_steps, charged per step at the band of the
-- value being left. 48 -> 52 is 1 + 1 + 2 + 2 = 6. A potential step costs double.
create or replace function public.cv_upgrade_cost(p_from int, p_steps int, p_kind text default 'rating')
returns int language plpgsql immutable as $fn$
declare total int := 0; i int;
begin
  if p_steps is null or p_steps <= 0 then return 0; end if;
  for i in 0 .. p_steps - 1 loop
    total := total + public.cv_step_cost(p_from + i);
  end loop;
  if p_kind = 'potential' then total := total * 2; end if;
  return total;
end;
$fn$;

-- -------------------------------------------------------------------------------------
-- Small helpers
-- -------------------------------------------------------------------------------------

-- security definer so a policy on profiles can call it without recursing into its own RLS.
create or replace function public.cv_is_admin() returns boolean
language sql stable security definer set search_path = public as $fn$
  select coalesce((select is_admin from public.profiles where id = auth.uid()), false);
$fn$;

create or replace function public.cv_setting_int(p_key text, p_default int) returns int
language sql stable security definer set search_path = public as $fn$
  select coalesce((select (value #>> '{}')::int from public.settings where key = p_key), p_default);
$fn$;

create or replace function public.cv_setting_bool(p_key text, p_default boolean) returns boolean
language sql stable security definer set search_path = public as $fn$
  select coalesce((select (value #>> '{}')::boolean from public.settings where key = p_key), p_default);
$fn$;

-- Discord puts the username under different keys depending on the account, so coalesce
-- across every plausible one rather than betting on a single key.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare meta jsonb;
begin
  meta := coalesce(new.raw_user_meta_data, '{}'::jsonb);
  insert into public.profiles (id, discord_username, display_name)
  values (
    new.id,
    coalesce(meta ->> 'user_name', meta ->> 'preferred_username', meta ->> 'name',
             meta ->> 'full_name', new.email),
    coalesce(meta -> 'custom_claims' ->> 'global_name', meta ->> 'global_name',
             meta ->> 'full_name', meta ->> 'name', meta ->> 'user_name',
             meta ->> 'preferred_username', split_part(coalesce(new.email, 'gm@cheezey'), '@', 1))
  )
  on conflict (id) do nothing;
  return new;
end;
$fn$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- The website calls this right after sign-in. The auth.users trigger above normally has
-- already done the work; this covers a project where creating that trigger was refused.
create or replace function public.cv_ensure_profile() returns public.profiles
language plpgsql security definer set search_path = public as $fn$
declare meta jsonb; pr public.profiles%rowtype;
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  meta := coalesce(auth.jwt() -> 'user_metadata', '{}'::jsonb);
  insert into public.profiles (id, discord_username, display_name)
  values (
    auth.uid(),
    coalesce(meta ->> 'user_name', meta ->> 'preferred_username', meta ->> 'name',
             meta ->> 'full_name', meta ->> 'email'),
    coalesce(meta -> 'custom_claims' ->> 'global_name', meta ->> 'global_name',
             meta ->> 'full_name', meta ->> 'name', meta ->> 'user_name',
             meta ->> 'preferred_username', 'Cheezeyverse GM')
  )
  on conflict (id) do update
    set discord_username = coalesce(excluded.discord_username, public.profiles.discord_username),
        display_name     = coalesce(public.profiles.display_name, excluded.display_name)
  returning * into pr;
  return pr;
end;
$fn$;

-- -------------------------------------------------------------------------------------
-- Guards: what a signed-in user is allowed to create
-- -------------------------------------------------------------------------------------

-- Characters are inserted by their owner straight from create.html. The trigger pins
-- everything the owner must not choose (owner, status, the plumbing columns, the point
-- budget) and sanity-checks the ratings block, the derived sheet and the growth bias.
-- The quiz itself lives in site/js/rules.js and SQL cannot re-derive it; the commissioner
-- reviews pending characters before activating them, which is where a bogus sheet is
-- caught. See THE TRUST MODEL at the top.
create or replace function public.cv_character_insert_guard() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare
  start_pts int;
  max_chars int;
  owned     int;
  r         text;
  v         int;
  p         int;
  b         int;
  bias      jsonb := '{}'::jsonb;
begin
  if auth.uid() is not null and not public.cv_is_admin() then
    new.owner := auth.uid();
  end if;
  if new.owner is null then
    raise exception 'a character needs an owner';
  end if;

  max_chars := public.cv_setting_int('max_characters', 2);
  select count(*) into owned
    from public.characters
   where owner = new.owner and status <> 'retired';
  if owned >= max_chars then
    raise exception 'that account already has % live character(s); the limit is %', owned, max_chars;
  end if;

  -- Creation does not spend any more: the quiz derives the sheet and the whole opening
  -- budget arrives banked. The check stays in case an older client is still out there.
  start_pts := public.cv_setting_int('starting_points', 20);
  if coalesce(new.points_spent, 0) < 0 or coalesce(new.points_spent, 0) > start_pts then
    raise exception 'starting spend % is outside 0..%', new.points_spent, start_pts;
  end if;
  new.points_spent     := coalesce(new.points_spent, 0);
  new.points_available := start_pts - new.points_spent;

  -- plumbing the owner does not get to pick
  new.status      := 'pending';
  new.league      := coalesce(new.league, 'prep');
  new.team_abbrev := null;
  new.claimed_slot := null;
  new.game_dob    := null;
  new.created_at  := now();
  -- derived from the row's own id, which does not exist until this insert lands. The
  -- commissioner caches it later; nothing may supply it.
  new.height_seed := null;

  -- every rating present and in range
  foreach r in array public.cv_ratings() loop
    v := coalesce((new.ratings ->> r)::int, -1);
    if v < 0 or v > 100 then
      raise exception 'rating % is missing or out of range (%)', r, v;
    end if;
  end loop;

  -- every potential present, in range, and never below its own rating
  foreach r in array public.cv_potentials() loop
    p := coalesce((new.potentials ->> r)::int, -1);
    v := coalesce((new.ratings ->> r)::int, 0);
    if p < 0 or p > 100 then
      raise exception 'potential % is missing or out of range (%)', r, p;
    end if;
    if v > p then
      raise exception '% is % but its potential is only %', r, v, p;
    end if;
  end loop;

  -- The growth bias arrives derived from the quiz and cannot be re-derived here, so it is
  -- CLAMPED rather than trusted: 85..115, neutral for anything missing or unparseable.
  -- That clamp is the whole defence, and it is what bounds how much a forged bias is
  -- worth - see THE TRUST MODEL at the top of this file.
  foreach r in array public.cv_ratings() loop
    begin
      b := coalesce((new.growth_bias ->> r)::int, 100);
    exception when others then
      b := 100;
    end;
    bias := bias || jsonb_build_object(r, least(115, greatest(85, b)));
  end loop;
  new.growth_bias := bias;

  if new.jersey_preference is not null
     and (new.jersey_preference < 0 or new.jersey_preference > 99) then
    raise exception 'jersey number % is not 0-99', new.jersey_preference;
  end if;

  return new;
end;
$fn$;

drop trigger if exists cv_character_insert_guard on public.characters;
create trigger cv_character_insert_guard
  before insert on public.characters
  for each row execute function public.cv_character_insert_guard();

-- The ledger entry for the starting points, and for whatever was spent at creation, so
-- sum(point_ledger.amount) always reconciles with points_available.
create or replace function public.cv_character_opening_ledger() returns trigger
language plpgsql security definer set search_path = public as $fn$
begin
  insert into public.point_ledger (character_id, amount, reason)
  values (new.id, new.points_available + new.points_spent, 'starting points');
  if new.points_spent > 0 then
    insert into public.point_ledger (character_id, amount, reason)
    values (new.id, -new.points_spent, 'creation spend');
  end if;
  return new;
end;
$fn$;

drop trigger if exists cv_character_opening_ledger on public.characters;
create trigger cv_character_opening_ledger
  after insert on public.characters
  for each row execute function public.cv_character_opening_ledger();

-- Upgrade requests: the client sends character_id, rating, delta and kind. Everything
-- else - cost above all - is computed here from the character's real ratings plus the
-- deltas already queued, so requests stack correctly and cannot be under-priced.
--
-- THE GROWTH BIAS. A character's traits make some ratings come a little more readily than
-- others. That is applied HERE, to the finished cost, and never inside cv_upgrade_cost():
--
--     new.cost := greatest(1, round(new.cost * bias / 100.0))
--
-- so the curve itself (1 / 2 / 3 / 5, charged at the value being left) stays exactly what
-- it has always been and the only thing that moved is a multiply on the total. site/js/
-- rules.js applies the identical line in applyBias(), and tests/test_rules.py pins the two
-- copies to the same text. `bias` was clamped to 85..115 by the insert guard.
create or replace function public.cv_upgrade_request_guard() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare
  ch           public.characters%rowtype;
  bias_pct     int;
  base         int;
  queued       int;
  effective    int;
  pot_base     int;
  pot_queued   int;
  pot_eff      int;
  ceiling      int := 100;
  reserved     int;
  has_pot      boolean;
begin
  select * into ch from public.characters where id = new.character_id;
  if not found then
    raise exception 'no such character';
  end if;
  if auth.uid() is not null and ch.owner <> auth.uid() and not public.cv_is_admin() then
    raise exception 'that is not your character';
  end if;
  if ch.status = 'retired' then
    raise exception 'retired characters cannot be upgraded';
  end if;

  if not (new.rating = any(public.cv_ratings())) then
    raise exception 'unknown rating %', new.rating;
  end if;
  has_pot := new.rating = any(public.cv_potentials());
  if new.kind = 'potential' and not has_pot then
    raise exception '% has no potential in this game', new.rating;
  end if;

  base   := coalesce((ch.ratings ->> new.rating)::int, 0);
  queued := coalesce((select sum(delta) from public.upgrade_requests
                       where character_id = new.character_id
                         and rating = new.rating and kind = 'rating'
                         and status in ('pending','approved')), 0);
  effective := base + queued;

  pot_base   := coalesce((ch.potentials ->> new.rating)::int, 100);
  pot_queued := coalesce((select sum(delta) from public.upgrade_requests
                           where character_id = new.character_id
                             and rating = new.rating and kind = 'potential'
                             and status in ('pending','approved')), 0);
  pot_eff := pot_base + pot_queued;

  if new.kind = 'rating' then
    if has_pot then
      ceiling := least(100, pot_eff);
    end if;
    if effective + new.delta > ceiling then
      raise exception '% would reach % but is capped at %', new.rating, effective + new.delta, ceiling;
    end if;
    new.cost := public.cv_upgrade_cost(effective, new.delta, 'rating');
  else
    if pot_eff + new.delta > 100 then
      raise exception 'potential % would reach %, over the 100 ceiling', new.rating, pot_eff + new.delta;
    end if;
    new.cost := public.cv_upgrade_cost(pot_eff, new.delta, 'potential');
  end if;

  -- The growth bias, on top of the finished curve cost and never inside it.
  -- `cost * bias / 100.0` is numeric, round() on numeric is half-away-from-zero (which is
  -- Math.round for positives), greatest(int, numeric) is numeric, and assigning numeric to
  -- an int column rounds a second time - harmless, because the value is already integral
  -- by then. Spelled out so nobody has to wonder whether the double round can drift.
  bias_pct := least(115, greatest(85, coalesce((ch.growth_bias ->> new.rating)::int, 100)));
  new.cost := greatest(1, round(new.cost * bias_pct / 100.0));

  -- requests that are queued but not applied have already reserved their cost
  reserved := coalesce((select sum(cost) from public.upgrade_requests
                         where character_id = new.character_id
                           and status in ('pending','approved')), 0);
  if new.cost + reserved > ch.points_available then
    raise exception 'that costs % but only % point(s) are unspent (% already queued)',
      new.cost, ch.points_available, reserved;
  end if;

  new.status       := case when public.cv_setting_bool('auto_approve', false) then 'approved' else 'pending' end;
  new.requested_at := now();
  new.applied_at   := null;
  return new;
end;
$fn$;

drop trigger if exists cv_upgrade_request_guard on public.upgrade_requests;
create trigger cv_upgrade_request_guard
  before insert on public.upgrade_requests
  for each row execute function public.cv_upgrade_request_guard();

-- -------------------------------------------------------------------------------------
-- RPCs
-- -------------------------------------------------------------------------------------
-- Everything below that moves points or status is `security definer` and revoked from
-- anon/authenticated, so only the service role (the commissioner app) can call it. The
-- one exception is declare_for_draft(), which the owner triggers from me.html.

-- Ledger row + balance in one statement pair inside one transaction. Two separate
-- PostgREST calls could not be atomic, which is why this lives in the database.
create or replace function public.grant_points(p_character uuid, p_amount int, p_reason text default 'admin grant')
returns public.characters
language plpgsql security definer set search_path = public as $fn$
declare ch public.characters%rowtype;
begin
  if p_amount is null or p_amount = 0 then
    raise exception 'amount must be non-zero';
  end if;
  insert into public.point_ledger (character_id, amount, reason)
  values (p_character, p_amount, coalesce(nullif(btrim(p_reason), ''), 'admin grant'));
  update public.characters
     set points_available = points_available + p_amount
   where id = p_character
  returning * into ch;
  if not found then
    raise exception 'no such character %', p_character;
  end if;
  return ch;
end;
$fn$;

-- Give every live character in a league its weekly point. Returns how many were paid.
create or replace function public.grant_week_points(p_league text default null, p_reason text default 'week simmed')
returns int
language plpgsql security definer set search_path = public as $fn$
declare n int := 0; c record; per int;
begin
  per := public.cv_setting_int('points_per_week', 1);
  for c in select id from public.characters
            where status in ('active','declared')
              and (p_league is null or league = p_league)
  loop
    perform public.grant_points(c.id, per, p_reason);
    n := n + 1;
  end loop;
  return n;
end;
$fn$;

-- Apply approved requests: move the rating, move the points, write the ledger, stamp the
-- request. All of it in one transaction, so a half-applied upgrade is impossible.
create or replace function public.apply_upgrade_requests(p_ids uuid[])
returns setof public.upgrade_requests
language plpgsql security definer set search_path = public as $fn$
declare req public.upgrade_requests%rowtype; ch public.characters%rowtype; cur int;
begin
  for req in select * from public.upgrade_requests
              where id = any(p_ids) and status = 'approved'
              order by requested_at
  loop
    select * into ch from public.characters where id = req.character_id for update;
    if not found then
      raise exception 'request % points at a character that no longer exists', req.id;
    end if;

    if req.kind = 'rating' then
      cur := coalesce((ch.ratings ->> req.rating)::int, 0);
      update public.characters
         set ratings          = ratings || jsonb_build_object(req.rating, cur + req.delta),
             points_available = points_available - req.cost,
             points_spent     = points_spent + req.cost
       where id = ch.id;
    else
      cur := coalesce((ch.potentials ->> req.rating)::int, 0);
      update public.characters
         set potentials       = potentials || jsonb_build_object(req.rating, cur + req.delta),
             points_available = points_available - req.cost,
             points_spent     = points_spent + req.cost
       where id = ch.id;
    end if;

    insert into public.point_ledger (character_id, amount, reason)
    values (ch.id, -req.cost, format('%s %s +%s', req.kind, req.rating, req.delta));

    update public.upgrade_requests
       set status = 'applied', applied_at = now()
     where id = req.id
    returning * into req;
    return next req;
  end loop;
end;
$fn$;

-- The commissioner has claimed a reserve slot in a save and stamped the character onto
-- it (codec rename + set). This records that and switches the character on.
create or replace function public.activate_character(p_character uuid, p_league text, p_team_abbrev text,
                                                     p_claimed_slot jsonb, p_game_dob date)
returns public.characters
language plpgsql security definer set search_path = public as $fn$
declare ch public.characters%rowtype;
begin
  if p_league not in ('prep','college','pro') then
    raise exception 'league must be prep, college or pro (got %)', p_league;
  end if;
  update public.characters
     set league       = p_league,
         team_abbrev  = p_team_abbrev,
         claimed_slot = p_claimed_slot,
         game_dob     = coalesce(p_game_dob, game_dob),
         status       = 'active'
   where id = p_character
  returning * into ch;
  if not found then
    raise exception 'no such character %', p_character;
  end if;
  return ch;
end;
$fn$;

-- The one RPC the website calls: a prep character declaring for the college draft, or a
-- college character declaring for the pro draft. The commissioner picks it up from
-- pending work and moves him into the next save at the next offseason.
-- Returns the new status rather than the row: a security definer function's return value
-- is not filtered by the column grants, and claimed_slot must stay service-role only.
create or replace function public.declare_for_draft(p_character uuid)
returns text
language plpgsql security definer set search_path = public as $fn$
declare ch public.characters%rowtype;
begin
  select * into ch from public.characters where id = p_character;
  if not found then
    raise exception 'no such character';
  end if;
  if ch.owner <> auth.uid() and not public.cv_is_admin() then
    raise exception 'that is not your character';
  end if;
  if ch.status <> 'active' then
    raise exception 'only an active character can declare (this one is %)', ch.status;
  end if;
  if ch.league = 'pro' then
    raise exception 'a pro is already there';
  end if;
  update public.characters set status = 'declared' where id = p_character returning * into ch;
  return ch.status;
end;
$fn$;

-- -------------------------------------------------------------------------------------
-- Row Level Security
-- -------------------------------------------------------------------------------------
-- The league sites are public on GitHub Pages, so the hub has to render for a signed-out
-- visitor: anon and authenticated both get SELECT on characters, profiles and settings.
-- point_ledger and upgrade_requests are owner-only. Nothing but the service role writes
-- the plumbing columns.

alter table public.profiles         enable row level security;
alter table public.characters       enable row level security;
alter table public.upgrade_requests enable row level security;
alter table public.point_ledger     enable row level security;
alter table public.settings         enable row level security;

-- ---- profiles ----
drop policy if exists profiles_read       on public.profiles;
drop policy if exists profiles_update_own on public.profiles;
drop policy if exists profiles_admin_all  on public.profiles;

-- display names are public: the hub credits whoever created each character
create policy profiles_read on public.profiles
  for select to anon, authenticated using (true);

create policy profiles_update_own on public.profiles
  for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

create policy profiles_admin_all on public.profiles
  for all to authenticated using (public.cv_is_admin()) with check (public.cv_is_admin());

-- ---- characters ----
drop policy if exists characters_read       on public.characters;
drop policy if exists characters_insert_own on public.characters;
drop policy if exists characters_update_own on public.characters;
drop policy if exists characters_delete_own on public.characters;
drop policy if exists characters_admin_all  on public.characters;

create policy characters_read on public.characters
  for select to anon, authenticated using (true);

create policy characters_insert_own on public.characters
  for insert to authenticated with check (owner = auth.uid());

-- paired with the column grants below: the only columns an owner can actually write are
-- first_name and last_name.
create policy characters_update_own on public.characters
  for update to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- a character can be thrown away only while it is still waiting for a slot
create policy characters_delete_own on public.characters
  for delete to authenticated using (owner = auth.uid() and status = 'pending');

create policy characters_admin_all on public.characters
  for all to authenticated using (public.cv_is_admin()) with check (public.cv_is_admin());

-- ---- upgrade_requests ----
drop policy if exists upgrade_requests_read_own   on public.upgrade_requests;
drop policy if exists upgrade_requests_insert_own on public.upgrade_requests;
drop policy if exists upgrade_requests_delete_own on public.upgrade_requests;
drop policy if exists upgrade_requests_admin_all  on public.upgrade_requests;

create policy upgrade_requests_read_own on public.upgrade_requests
  for select to authenticated using (
    exists (select 1 from public.characters c where c.id = character_id and c.owner = auth.uid())
  );

create policy upgrade_requests_insert_own on public.upgrade_requests
  for insert to authenticated with check (
    exists (select 1 from public.characters c where c.id = character_id and c.owner = auth.uid())
  );

-- cancelling a request you have not had applied yet
create policy upgrade_requests_delete_own on public.upgrade_requests
  for delete to authenticated using (
    status = 'pending'
    and exists (select 1 from public.characters c where c.id = character_id and c.owner = auth.uid())
  );

create policy upgrade_requests_admin_all on public.upgrade_requests
  for all to authenticated using (public.cv_is_admin()) with check (public.cv_is_admin());

-- ---- point_ledger ----
drop policy if exists point_ledger_read_own  on public.point_ledger;
drop policy if exists point_ledger_admin_all on public.point_ledger;

create policy point_ledger_read_own on public.point_ledger
  for select to authenticated using (
    exists (select 1 from public.characters c where c.id = character_id and c.owner = auth.uid())
  );

create policy point_ledger_admin_all on public.point_ledger
  for all to authenticated using (public.cv_is_admin()) with check (public.cv_is_admin());
-- no insert/update/delete policy for plain users: the ledger is service-role only.

-- ---- settings ----
drop policy if exists settings_read      on public.settings;
drop policy if exists settings_admin_all on public.settings;

create policy settings_read on public.settings
  for select to anon, authenticated using (true);

create policy settings_admin_all on public.settings
  for all to authenticated using (public.cv_is_admin()) with check (public.cv_is_admin());

-- -------------------------------------------------------------------------------------
-- Column-level grants
-- -------------------------------------------------------------------------------------
-- RLS `with check` only sees NEW, so it cannot express "you did not change this column".
-- Column privileges can. Note the order: a table-wide UPDATE grant is NOT narrowed by a
-- column-level REVOKE, so the table grant is dropped first and specific columns granted
-- back. Column-level UPDATE has no effect on INSERT, so creating a character with its
-- starting ratings still works.

revoke update on public.characters from anon, authenticated;
grant  update (first_name, last_name) on public.characters to authenticated;

-- claimed_slot is the save-file plumbing and stays out of the public API surface.
-- Everything else is public, including the quiz answers: half the fun is reading somebody
-- else's. Keep this list in step with CHARACTER_COLUMNS in site/js/supabase.js - the site
-- never does select('*'), precisely because a column without a grant here would 403.
revoke select on public.characters from anon, authenticated;
grant  select (id, owner, first_name, last_name, "position", height_inches, archetype,
               league, team_abbrev, status, game_dob, ratings, potentials,
               points_available, points_spent, created_at,
               jersey_preference, hometown, build, career_goal, traits, quiz_answers,
               growth_bias, height_seed, expected_adult_height,
               -- the career page's thread between levels; the commissioner writes all of these
               college_years, declared, level_history, league_player_ids,
               draft_round, draft_pick, draft_season)
  on public.characters to anon, authenticated;

-- height_seed is NOT insertable: it is derived from the row's own id, which does not exist
-- until the insert lands, so the commissioner writes it with the service role afterwards.
revoke insert on public.characters from anon, authenticated;
grant  insert (owner, first_name, last_name, "position", height_inches, archetype,
               ratings, potentials, points_spent, league,
               jersey_preference, hometown, build, career_goal, traits, quiz_answers,
               growth_bias, expected_adult_height)
  on public.characters to authenticated;
grant delete on public.characters to authenticated;

-- upgrade_requests: insert the four fields that describe the wish, read everything back.
revoke update, insert on public.upgrade_requests from anon, authenticated;
grant  select on public.upgrade_requests to authenticated;
grant  insert (character_id, rating, delta, kind, note) on public.upgrade_requests to authenticated;
grant  delete on public.upgrade_requests to authenticated;

revoke insert, update, delete on public.point_ledger from anon, authenticated;
grant  select on public.point_ledger to authenticated;

revoke insert, update, delete on public.settings from anon;
grant  select on public.settings to anon, authenticated;
grant  insert, update, delete on public.settings to authenticated;  -- gated by settings_admin_all

-- profiles matters more than it looks: is_admin lives here, and cv_is_admin() reads it.
-- Supabase's default privileges hand `authenticated` a table-wide UPDATE, and a table-wide
-- grant is NOT narrowed by a column-level one, so without this revoke any signed-in user
-- could `update profiles set is_admin = true where id = auth.uid()` and unlock every
-- admin policy in the file. Revoke first, then grant back the one harmless column.
revoke insert, update, delete on public.profiles from anon, authenticated;
grant  select on public.profiles to anon, authenticated;
grant  update (display_name) on public.profiles to authenticated;

-- -------------------------------------------------------------------------------------
-- Function grants
-- -------------------------------------------------------------------------------------
revoke all on function public.grant_points(uuid, int, text)                      from public, anon, authenticated;
revoke all on function public.grant_week_points(text, text)                      from public, anon, authenticated;
revoke all on function public.apply_upgrade_requests(uuid[])                     from public, anon, authenticated;
revoke all on function public.activate_character(uuid, text, text, jsonb, date)  from public, anon, authenticated;

grant execute on function public.grant_points(uuid, int, text)                     to service_role;
grant execute on function public.grant_week_points(text, text)                     to service_role;
grant execute on function public.apply_upgrade_requests(uuid[])                    to service_role;
grant execute on function public.activate_character(uuid, text, text, jsonb, date) to service_role;

grant execute on function public.declare_for_draft(uuid) to authenticated, service_role;
grant execute on function public.cv_ensure_profile()     to authenticated, service_role;
grant execute on function public.cv_is_admin()           to anon, authenticated, service_role;
grant execute on function public.cv_step_cost(int)       to anon, authenticated, service_role;
grant execute on function public.cv_upgrade_cost(int, int, text) to anon, authenticated, service_role;
grant execute on function public.cv_ratings()            to anon, authenticated, service_role;
grant execute on function public.cv_potentials()         to anon, authenticated, service_role;

commit;

-- =====================================================================================
-- SEED - the default settings rows. Identical to supabase/seed.sql; running either is
-- enough. `on conflict do nothing` means a re-run never clobbers a value you have tuned.
-- =====================================================================================

insert into public.settings (key, value) values
  ('max_characters',  '2'::jsonb),      -- live (non-retired) characters per Discord account
  ('starting_points', '20'::jsonb),     -- points a brand new 14 year old gets to spend
  ('points_per_week', '1'::jsonb),      -- points granted per simmed in-game week
  ('auto_approve',    'false'::jsonb),  -- true = upgrade requests skip the commissioner's queue
  ('current_season',  '2030'::jsonb),   -- must match START_YEAR in commissioner/universe/config.py
  ('current_week',    '0'::jsonb)
on conflict (key) do nothing;
