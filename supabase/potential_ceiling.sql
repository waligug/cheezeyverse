-- Run this in the Supabase SQL editor. It replaces one function and touches nothing else.
-- Safe to run twice.
--
-- WHY. The upgrade guard capped every rating and every potential at 100. The game does not:
-- the codec allows up to 150, a real player file reaches 139, and on 2026-09-23 three of our
-- characters were already past it -
--
--   Tim Turner          potentials DReb 124, Blocking 115, OReb 109, PostDefense 108, Inside 107
--                       and a RATING of InsideScoring 107
--   Johnny Gartholomew  potentials DReb 123, Blocking 113, OReb 109
--   Dodger Manson       potential  OReb 104
--
-- Tim's InsideScoring was frozen outright: 107 is already over 100, so ANY step was refused.
-- Johnny could buy DReb only to 100 under a potential of 123.
--
-- WHAT CHANGES, and what deliberately does not:
--   * a rating that HAS a potential is capped by that potential, up to 150 (was least(100, ...))
--   * a potential can be raised to 150 (was 100)
--   * the six ratings with NO potential stay at 100. Measured across the live saves, every rating
--     above 100 belongs to one of the twelve that carry a potential; none of the other six ever
--     passes it, so there is nothing for them to grow into.
--
-- The site (site/js/rules.js POTENTIAL_MAX) was changed to the same numbers in the same commit.
-- Until this is run the site will OFFER a step the database then refuses - so run it before
-- anybody tries to spend past 100.

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
      -- CAPPED BY ITS POTENTIAL, which can pass 100 - up to 150, the codec's own RATING_MAX.
      -- least(100, ...) froze Tim Turner's InsideScoring outright (107 is already over 100, so
      -- every step was refused) and stopped Johnny Gartholomew's DReb at 100 under a
      -- potential of 123. The six ratings with no potential keep `ceiling`'s default of 100:
      -- across the live saves none of them ever passes it.
      ceiling := least(150, pot_eff);
    end if;
    if effective + new.delta > ceiling then
      raise exception '% would reach % but is capped at %', new.rating, effective + new.delta, ceiling;
    end if;
    new.cost := public.cv_upgrade_cost(effective, new.delta, 'rating');
  else
    if pot_eff + new.delta > 150 then
      raise exception 'potential % would reach %, over the 150 ceiling', new.rating, pot_eff + new.delta;
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
