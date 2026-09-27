-- THE SKILL TREE. Run this in the Supabase SQL editor. Safe to run twice.
--
-- NOTHING CHANGES WHEN YOU RUN IT. Every new rule sits behind the setting `skill_tree_enabled`,
-- which starts false: until the commissioner flips it, prices, caps and requests behave exactly
-- as they did before this file. Flipping it back to false is the rollback.
--
-- What it adds:
--   * public.tree_nodes              the tree itself: five branches, three tiers and a
--                                    Signature each. Filled in by tools/sync_tree.py
--                                    (commissioner/tree.py is the source of truth).
--   * characters.nodes               the nodes a character owns
--   * characters.cap_breakers        Cap Breakers he has not used yet (each is +3 on a ceiling)
--   * upgrade_requests.kind          gains 'node' and 'breaker'; delta may now reach 150
--   * the price list, stage caps, the ceiling room and the wider growth-bias range, all read
--     from settings so they can be tuned without another paste
--
-- supabase/schema.sql carries the same definitions; tests/test_skill_tree_sql.py keeps the two
-- in step and runs this file against a real Postgres.

begin;

-- ---------------------------------------------------------------------------- columns
alter table public.characters add column if not exists nodes text[] not null default '{}'::text[];
alter table public.characters add column if not exists cap_breakers int not null default 0;
alter table public.characters drop constraint if exists characters_cap_breakers_check;
alter table public.characters add constraint characters_cap_breakers_check check (cap_breakers >= 0);

grant select (nodes, cap_breakers) on public.characters to anon, authenticated;

-- ---------------------------------------------------------------------------- request kinds
alter table public.upgrade_requests drop constraint if exists upgrade_requests_kind_check;
alter table public.upgrade_requests add constraint upgrade_requests_kind_check
  check (kind in ('rating', 'potential', 'node', 'breaker'));
-- 40 was a limit nobody chose: the spend page sends a rating's whole staged change as one
-- request, so staging 41 steps on one rating failed the insert.
alter table public.upgrade_requests drop constraint if exists upgrade_requests_delta_check;
alter table public.upgrade_requests add constraint upgrade_requests_delta_check
  check (delta > 0 and delta <= 150);

-- ---------------------------------------------------------------------------- the tree
create table if not exists public.tree_nodes (
  id          text primary key,
  branch      text not null check (branch in ('Scoring', 'Playmaking', 'Defense', 'Rebounding', 'Athletic')),
  tier        int  not null check (tier between 1 and 4),
  name        text not null,
  blurb       text not null default '',
  cost        int  not null check (cost >= 0),
  stage       text not null check (stage in ('prep', 'college', 'pro')),
  requires    text[] not null default '{}'::text[],
  -- points spent on this branch's ratings and ceilings since the tree opened
  min_spent   int  not null default 0 check (min_spent >= 0),
  -- {rating: value}; every one must be met
  min_ratings jsonb not null default '{}'::jsonb,
  -- at most one node of a group per character ('signature')
  group_key   text,
  -- {"ratings": {r: +n}, "potentials": {r: +n}, "bias": {r: -n}, "shield": {branch: 0.5},
  --  "tendency": {r: +n}}. shield is read by the commissioner's offseason, not here.
  effects     jsonb not null default '{}'::jsonb,
  sort        int  not null default 0,
  active      boolean not null default true,
  updated_at  timestamptz not null default now()
);

alter table public.tree_nodes enable row level security;
drop policy if exists tree_nodes_read on public.tree_nodes;
create policy tree_nodes_read on public.tree_nodes for select to anon, authenticated using (true);
revoke insert, update, delete on public.tree_nodes from anon, authenticated;
grant select on public.tree_nodes to anon, authenticated;

-- ---------------------------------------------------------------------------- vocabulary
create or replace function public.cv_branch_of(p_rating text) returns text
language sql immutable as $fn$
  select case
    when p_rating in ('InsideScoring', 'JumpShot', 'FtShot', '3pShot', '3pUsage') then 'Scoring'
    when p_rating in ('Handling', 'Passing') then 'Playmaking'
    when p_rating in ('PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking') then 'Defense'
    when p_rating in ('OReb', 'DReb') then 'Rebounding'
    when p_rating in ('Quickness', 'Jumping', 'Strength', 'Stamina') then 'Athletic'
  end;
$fn$;

create or replace function public.cv_stage_rank(p_league text) returns int
language sql immutable as $fn$
  select case p_league when 'prep' then 1 when 'college' then 2 when 'pro' then 3 else 0 end;
$fn$;

create or replace function public.cv_setting_json(p_key text, p_default jsonb) returns jsonb
language sql stable security definer set search_path = public as $fn$
  select coalesce((select value from public.settings where key = p_key), p_default);
$fn$;

-- THE SWITCH. False until the commissioner opens the tree.
create or replace function public.cv_tree_on() returns boolean
language sql stable security definer set search_path = public as $fn$
  select public.cv_setting_bool('skill_tree_enabled', false);
$fn$;

-- ---------------------------------------------------------------------------- prices
-- Cost of the single step that *leaves* value v. With the tree off this is the old curve,
-- untouched: below 50 -> 1, 50-69 -> 2, 70-84 -> 3, 85 and up -> 5. With it on, the bands come
-- from settings.price_bands, [[below, cost], ...] - a step leaving v costs the first band whose
-- `below` is greater than v.
create or replace function public.cv_step_cost(v int) returns int
language plpgsql stable security definer set search_path = public as $fn$
declare bands jsonb; band jsonb;
begin
  if not public.cv_tree_on() then
    return case when v < 50 then 1 when v < 70 then 2 when v < 85 then 3 else 5 end;
  end if;
  bands := public.cv_setting_json('price_bands',
    '[[50,1],[60,2],[70,3],[80,5],[85,8],[90,12],[95,18],[100,25],[999,35]]'::jsonb);
  for band in select value from jsonb_array_elements(bands) loop
    if v < (band ->> 0)::int then
      return (band ->> 1)::int;
    end if;
  end loop;
  return (bands -> -1 ->> 1)::int;
end;
$fn$;

-- Raising a value from p_from by p_steps, each step charged at the band it leaves. A ceiling
-- (potential) step costs double with the tree off and potential_multiplier (3) with it on.
create or replace function public.cv_upgrade_cost(p_from int, p_steps int, p_kind text default 'rating')
returns int language plpgsql stable security definer set search_path = public as $fn$
declare total int := 0; i int;
begin
  if p_steps is null or p_steps <= 0 then return 0; end if;
  for i in 0 .. p_steps - 1 loop
    total := total + public.cv_step_cost(p_from + i);
  end loop;
  if p_kind = 'potential' then
    total := total * case when public.cv_tree_on()
                          then public.cv_setting_int('potential_multiplier', 3) else 2 end;
  end if;
  return total;
end;
$fn$;

-- Points a character has put into one branch's ratings and ceilings since the tree opened.
-- Queued requests count, so a player can queue the ratings and the node they unlock together.
create or replace function public.cv_branch_spent(p_character uuid, p_branch text) returns int
language sql stable security definer set search_path = public as $fn$
  select coalesce(sum(cost), 0)::int
    from public.upgrade_requests
   where character_id = p_character
     and kind in ('rating', 'potential')
     and status in ('pending', 'approved', 'applied')
     and public.cv_branch_of(rating) = p_branch
     and requested_at >= coalesce((public.cv_setting_json('tree_opened_at', 'null'::jsonb) #>> '{}')::timestamptz,
                                  'epoch'::timestamptz);
$fn$;

-- ---------------------------------------------------------------------------- the guard
create or replace function public.cv_upgrade_request_guard() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare
  ch           public.characters%rowtype;
  tree_on      boolean := public.cv_tree_on();
  bias_pct     int;
  bias_lo      int;
  bias_hi      int;
  base         int;
  queued       int;
  effective    int;
  pot_base     int;
  pot_queued   int;
  pot_eff      int;
  ceiling      int := 100;
  stage_cap    int;
  room         int;
  reserved     int;
  has_pot      boolean;
  node         public.tree_nodes%rowtype;
  owned        text[];
  queued_nodes text[];
  req          text;
  k            text;
  v            text;
  used         int;
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

  if new.kind in ('node', 'breaker') and not tree_on then
    raise exception 'the skill tree is not open yet';
  end if;

  if new.kind = 'node' then
    -- ------------------------------------------------------------ a tree node
    if new.delta <> 1 then
      raise exception 'a node is bought once';
    end if;
    select * into node from public.tree_nodes where id = new.rating and active;
    if not found then
      raise exception 'there is no node %', new.rating;
    end if;
    owned := coalesce(ch.nodes, '{}'::text[]);
    queued_nodes := coalesce((select array_agg(rating) from public.upgrade_requests
                               where character_id = new.character_id and kind = 'node'
                                 and status in ('pending', 'approved')), '{}'::text[]);
    if node.id = any(owned) or node.id = any(queued_nodes) then
      raise exception '% is already his', node.name;
    end if;
    if public.cv_stage_rank(ch.league) < public.cv_stage_rank(node.stage) then
      raise exception '% opens in %', node.name, node.stage;
    end if;
    foreach req in array node.requires loop
      if not (req = any(owned) or req = any(queued_nodes)) then
        raise exception '% needs % first', node.name,
          coalesce((select name from public.tree_nodes where id = req), req);
      end if;
    end loop;
    if node.group_key is not null and exists (
         select 1 from public.tree_nodes t
          where t.group_key = node.group_key and t.id <> node.id
            and (t.id = any(owned) or t.id = any(queued_nodes))) then
      raise exception 'only one % per player', node.group_key;
    end if;
    used := public.cv_branch_spent(ch.id, node.branch);
    if used < node.min_spent then
      raise exception '% needs % points spent on % (% so far)', node.name, node.min_spent, node.branch, used;
    end if;
    for k, v in select key, value #>> '{}' from jsonb_each(node.min_ratings) loop
      if coalesce((ch.ratings ->> k)::int, 0) < v::int then
        raise exception '% needs % of at least %', node.name, k, v;
      end if;
    end loop;
    new.cost := node.cost;

  elsif new.kind = 'breaker' then
    -- ------------------------------------------------------------ a Cap Breaker
    if new.delta <> 1 then
      raise exception 'one Cap Breaker at a time';
    end if;
    if not (new.rating = any(public.cv_potentials())) then
      raise exception '% has no ceiling to break', new.rating;
    end if;
    used := (select count(*) from public.upgrade_requests
              where character_id = new.character_id and kind = 'breaker'
                and status in ('pending', 'approved'));
    if used + 1 > ch.cap_breakers then
      raise exception 'no Cap Breakers left';
    end if;
    used := (select count(*) from public.upgrade_requests
              where character_id = new.character_id and kind = 'breaker' and rating = new.rating
                and status in ('pending', 'approved', 'applied'));
    if used >= public.cv_setting_int('breaker_max_per_rating', 3) then
      raise exception '% has had all % of its Cap Breakers', new.rating, used;
    end if;
    new.cost := 0;

  else
    -- ------------------------------------------------------------ a rating or a ceiling
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
                           and status in ('pending', 'approved')), 0);
    effective := base + queued;

    pot_base   := coalesce((ch.potentials ->> new.rating)::int, 100);
    pot_queued := coalesce((select sum(delta) from public.upgrade_requests
                             where character_id = new.character_id
                               and rating = new.rating and kind = 'potential'
                               and status in ('pending', 'approved')), 0);
    pot_eff := pot_base + pot_queued;

    if new.kind = 'rating' then
      if has_pot then
        -- capped by its potential, which can pass 100 - up to 150, the codec's own RATING_MAX
        ceiling := least(150, pot_eff);
      end if;
      if tree_on then
        stage_cap := case ch.league
                       when 'prep' then public.cv_setting_int('stage_cap_prep', 70)
                       when 'college' then public.cv_setting_int('stage_cap_college', 85)
                       else 150 end;
        if effective + new.delta > stage_cap then
          raise exception '% cannot be bought past % in %', new.rating, stage_cap, ch.league;
        end if;
      end if;
      if effective + new.delta > ceiling then
        raise exception '% would reach % but is capped at %', new.rating, effective + new.delta, ceiling;
      end if;
      new.cost := public.cv_upgrade_cost(effective, new.delta, 'rating');
    else
      if pot_eff + new.delta > 150 then
        raise exception 'potential % would reach %, over the 150 ceiling', new.rating, pot_eff + new.delta;
      end if;
      if tree_on then
        -- The game grows every player toward his ceiling on its own, so a bought ceiling far
        -- above the rating is free growth for years. Keep it within reach of the rating.
        room := public.cv_setting_int('ceiling_room', 10);
        if pot_eff + new.delta > effective + room then
          raise exception 'a ceiling can be bought at most % above its rating (% is %)', room, new.rating, effective;
        end if;
      end if;
      new.cost := public.cv_upgrade_cost(pot_eff, new.delta, 'potential');
    end if;

    -- The growth bias, on top of the finished curve cost and never inside it.
    bias_lo := case when tree_on then public.cv_setting_int('bias_min', 50) else 85 end;
    bias_hi := case when tree_on then public.cv_setting_int('bias_max', 200) else 115 end;
    bias_pct := least(bias_hi, greatest(bias_lo, coalesce((ch.growth_bias ->> new.rating)::int, 100)));
    new.cost := greatest(1, round(new.cost * bias_pct / 100.0));
  end if;

  -- requests that are queued but not applied have already reserved their cost
  reserved := coalesce((select sum(cost) from public.upgrade_requests
                         where character_id = new.character_id
                           and status in ('pending', 'approved')), 0);
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

-- ---------------------------------------------------------------------------- applying
-- Apply approved requests: move the sheet, move the points, write the ledger, stamp the
-- request. All of it in one transaction, so a half-applied upgrade is impossible. A node's
-- effects land on the stored sheet here and on the save through commissioner/tree.py, which
-- does the same arithmetic.
create or replace function public.apply_upgrade_requests(p_ids uuid[])
returns setof public.upgrade_requests
language plpgsql security definer set search_path = public as $fn$
declare
  req  public.upgrade_requests%rowtype;
  ch   public.characters%rowtype;
  node public.tree_nodes%rowtype;
  cur  int;
  k    text;
  d    int;
  r    jsonb;
  p    jsonb;
  b    jsonb;
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
      insert into public.point_ledger (character_id, amount, reason)
      values (ch.id, -req.cost, format('%s %s +%s', req.kind, req.rating, req.delta));

    elsif req.kind = 'potential' then
      cur := coalesce((ch.potentials ->> req.rating)::int, 0);
      update public.characters
         set potentials       = potentials || jsonb_build_object(req.rating, cur + req.delta),
             points_available = points_available - req.cost,
             points_spent     = points_spent + req.cost
       where id = ch.id;
      insert into public.point_ledger (character_id, amount, reason)
      values (ch.id, -req.cost, format('%s %s +%s', req.kind, req.rating, req.delta));

    elsif req.kind = 'node' then
      select * into node from public.tree_nodes where id = req.rating;
      if not found then
        raise exception 'request % names a node that no longer exists', req.id;
      end if;
      r := coalesce(ch.ratings, '{}'::jsonb);
      p := coalesce(ch.potentials, '{}'::jsonb);
      b := coalesce(ch.growth_bias, '{}'::jsonb);
      -- ceilings first, then ratings, and a ceiling never ends below its rating
      for k, d in select key, (value #>> '{}')::int from jsonb_each(coalesce(node.effects -> 'potentials', '{}'::jsonb)) loop
        p := p || jsonb_build_object(k, least(150, coalesce((p ->> k)::int, 0) + d));
      end loop;
      for k, d in select key, (value #>> '{}')::int from jsonb_each(coalesce(node.effects -> 'ratings', '{}'::jsonb)
                                                                     || coalesce(node.effects -> 'tendency', '{}'::jsonb)) loop
        cur := least(case when k = any(public.cv_potentials()) then 150 else 100 end,
                     greatest(0, coalesce((r ->> k)::int, 0) + d));
        r := r || jsonb_build_object(k, cur);
        if k = any(public.cv_potentials()) and coalesce((p ->> k)::int, 0) < cur then
          p := p || jsonb_build_object(k, cur);
        end if;
      end loop;
      for k, d in select key, (value #>> '{}')::int from jsonb_each(coalesce(node.effects -> 'bias', '{}'::jsonb)) loop
        b := b || jsonb_build_object(k, coalesce((b ->> k)::int, 100) + d);
      end loop;
      update public.characters
         set ratings          = r,
             potentials       = p,
             growth_bias      = b,
             nodes            = array_append(coalesce(nodes, '{}'::text[]), node.id),
             points_available = points_available - req.cost,
             points_spent     = points_spent + req.cost
       where id = ch.id;
      if req.cost <> 0 then
        insert into public.point_ledger (character_id, amount, reason)
        values (ch.id, -req.cost, format('node %s (%s)', node.name, node.id));
      end if;

    elsif req.kind = 'breaker' then
      cur := coalesce((ch.potentials ->> req.rating)::int, 0);
      update public.characters
         set potentials   = potentials || jsonb_build_object(req.rating,
                              least(150, cur + public.cv_setting_int('breaker_size', 3))),
             cap_breakers = greatest(0, cap_breakers - 1)
       where id = ch.id;
    end if;

    update public.upgrade_requests
       set status = 'applied', applied_at = now()
     where id = req.id
    returning * into req;
    return next req;
  end loop;
end;
$fn$;

-- ---------------------------------------------------------------------------- grants
revoke all on function public.apply_upgrade_requests(uuid[]) from public, anon, authenticated;
grant execute on function public.apply_upgrade_requests(uuid[]) to service_role;
grant execute on function public.cv_step_cost(int) to anon, authenticated, service_role;
grant execute on function public.cv_upgrade_cost(int, int, text) to anon, authenticated, service_role;
grant execute on function public.cv_branch_of(text) to anon, authenticated, service_role;
grant execute on function public.cv_branch_spent(uuid, text) to authenticated, service_role;
grant execute on function public.cv_tree_on() to anon, authenticated, service_role;

commit;

-- ---------------------------------------------------------------------------- settings
-- Structural defaults only. The economy itself (pay, bonuses) and the switch are set by the
-- commissioner when the tree opens; `on conflict do nothing` never clobbers a tuned value.
insert into public.settings (key, value) values
  ('skill_tree_enabled',     'false'::jsonb),
  ('price_bands',            '[[50,1],[60,2],[70,3],[80,5],[85,8],[90,12],[95,18],[100,25],[999,35]]'::jsonb),
  ('potential_multiplier',   '3'::jsonb),
  ('ceiling_room',           '10'::jsonb),
  ('stage_cap_prep',         '70'::jsonb),
  ('stage_cap_college',      '85'::jsonb),
  ('bias_min',               '50'::jsonb),
  ('bias_max',               '200'::jsonb),
  ('breaker_size',           '3'::jsonb),
  ('breaker_max_per_rating', '3'::jsonb)
on conflict (key) do nothing;
