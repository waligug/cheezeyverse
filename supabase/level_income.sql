-- Run this in the Supabase SQL editor. It replaces one function and inserts three settings
-- rows; nothing else changes, and it is safe to run more than once.
--
-- WHY. The cost curve charges by rating: 1 point a step under 50, 2 from 50-69, 3 from 70-84,
-- 5 from 85 up. Characters sit roughly in those bands as they climb - prep in the first,
-- college in the second, the pros in the third - so a flat income means every promotion
-- quietly halves what a season is worth. tools/progression_model.mjs puts a character in the
-- pros at about a 64 core average, where a season's points buy barely +2 per core skill
-- against about +7 in prep. Paying 1 / 2 / 3 a week by level keeps a season worth roughly the
-- same number of upgrades for a whole career.
--
-- THE FUNCTION CHANGES SHAPE. grant_week_points gains a third argument, so the OLD two-argument
-- version is dropped explicitly - `create or replace` cannot change a signature, and without
-- the drop Postgres would keep both and resolve the two-argument call to the stale one, which
-- would go on paying the flat rate with nothing anywhere saying so.
--
-- Nothing is paid differently until characters are promoted: everyone is in prep, and prep
-- pays 1, exactly as before.

drop function if exists public.grant_week_points(text, text);

-- p_per is the resolved level rate, worked out by commissioner/points.py. It is passed in
-- rather than recomputed here on purpose: the price curve IS duplicated in schema.sql, because
-- players submit upgrade requests and the browser must not be able to name its own price, but
-- weekly income is only ever granted by the commissioner under the service key. A second
-- implementation would buy nothing and could drift. Left null it falls back to points_per_week.
create or replace function public.grant_week_points(p_league text default null, p_reason text default 'week simmed', p_per int default null)
returns int
language plpgsql security definer set search_path = public as $fn$
declare n int := 0; c record; per int;
begin
  per := coalesce(p_per, public.cv_setting_int('points_per_week', 1));
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

revoke all on function public.grant_week_points(text, text, int) from public, anon, authenticated;
grant execute on function public.grant_week_points(text, text, int) to service_role;

-- The rates themselves. on conflict do nothing so re-running never overwrites a rate that has
-- since been tuned in the settings table.
insert into public.settings (key, value) values
  ('points_per_week_prep', '1'::jsonb),
  ('points_per_week_college', '2'::jsonb),
  ('points_per_week_pro', '3'::jsonb)
on conflict (key) do nothing;

-- Check: should list the three rates, and the function should have three arguments.
select key, value from public.settings where key like 'points_per_week%' order by key;
select pg_get_function_identity_arguments(oid) as args
  from pg_proc where proname = 'grant_week_points';
