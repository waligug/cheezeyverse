-- Run this in the Supabase SQL editor. It replaces two functions in place; nothing else
-- changes, and it is safe to run more than once.
--
-- Why: Discord can send an empty string for a display name rather than null, coalesce
-- only skips nulls, so a profile was created with display_name = '' and the site showed
-- that person as "somebody".

create or replace function public.cv_ensure_profile() returns public.profiles
language plpgsql security definer set search_path = public as $fn$
declare meta jsonb; pr public.profiles%rowtype;
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  meta := coalesce(auth.jwt() -> 'user_metadata', '{}'::jsonb);
  insert into public.profiles (id, discord_username, display_name)
  values (
    auth.uid(),
    coalesce(nullif(btrim(meta ->> 'user_name'), ''), nullif(btrim(meta ->> 'preferred_username'), ''), nullif(btrim(meta ->> 'name'), ''),
             nullif(btrim(meta ->> 'full_name'), ''), meta ->> 'email'),
    coalesce(nullif(btrim(meta -> 'custom_claims' ->> 'global_name'), ''), nullif(btrim(meta ->> 'global_name'), ''),
             nullif(btrim(meta ->> 'full_name'), ''), nullif(btrim(meta ->> 'name'), ''), nullif(btrim(meta ->> 'user_name'), ''),
             nullif(btrim(meta ->> 'preferred_username'), ''), 'Cheezeyverse GM')
  )
  on conflict (id) do update
    set discord_username = coalesce(excluded.discord_username, public.profiles.discord_username),
        display_name     = coalesce(public.profiles.display_name, excluded.display_name)
  returning * into pr;
  return pr;
end;
$fn$;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare meta jsonb;
begin
  meta := coalesce(new.raw_user_meta_data, '{}'::jsonb);
  insert into public.profiles (id, discord_username, display_name)
  values (
    new.id,
    coalesce(nullif(btrim(meta ->> 'user_name'), ''), nullif(btrim(meta ->> 'preferred_username'), ''), nullif(btrim(meta ->> 'name'), ''),
             nullif(btrim(meta ->> 'full_name'), ''), new.email),
    coalesce(nullif(btrim(meta -> 'custom_claims' ->> 'global_name'), ''), nullif(btrim(meta ->> 'global_name'), ''),
             nullif(btrim(meta ->> 'full_name'), ''), nullif(btrim(meta ->> 'name'), ''), nullif(btrim(meta ->> 'user_name'), ''),
             nullif(btrim(meta ->> 'preferred_username'), ''), split_part(coalesce(new.email, 'gm@cheezey'), '@', 1))
  )
  on conflict (id) do nothing;
  return new;
end;
$fn$;

notify pgrst, 'reload schema';
