-- =====================================================================================
-- Cheezeyverse - default settings rows
-- =====================================================================================
-- Run this in the Supabase SQL editor after `supabase/schema.sql`. (The same statements
-- are repeated at the bottom of schema.sql, so running that file alone is also enough.)
--
-- `on conflict do nothing` means re-running this never clobbers a value you have tuned.
-- To change one later, either edit it in the dashboard, or from the commissioner app:
--     python -c "from commissioner import store; store.set_setting('starting_points', 25)"
-- =====================================================================================

insert into public.settings (key, value) values
  ('max_characters',  '2'::jsonb),      -- live (non-retired) characters per Discord account
  ('starting_points', '20'::jsonb),     -- points a brand new 14 year old gets to spend
  ('points_per_week', '1'::jsonb),      -- points granted per simmed in-game week
  ('auto_approve',    'false'::jsonb),  -- true = upgrade requests skip the commissioner's queue
  ('current_season',  '2030'::jsonb),   -- must match START_YEAR in commissioner/universe/config.py
  ('current_week',    '0'::jsonb)
on conflict (key) do nothing;

-- Promote yourself once you have signed in with Discord at least once. Until some row
-- has is_admin = true, nobody can edit settings from the website (the commissioner app
-- always can, because the service role bypasses RLS entirely).
--
--   update public.profiles set is_admin = true where discord_username = 'your-discord-name';
