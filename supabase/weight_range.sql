-- Run this in the Supabase SQL editor. It replaces one CHECK constraint on public.characters
-- and touches nothing else. Safe to run twice.
--
-- WHY. The 2030 offseason died here, mid-run, on a live universe:
--
--   PATCH /rest/v1/characters -> HTTP 400 {"code":"23514", ... Tim, Turner, PF, 80 ...}
--
-- Tim Turner is 6'8" and grew to 240 lbs. The net allowed 138-238. He was TWO POUNDS over, and
-- a season rollover stopped dead with the saves already grown and the database half-written.
--
-- THE NET WAS SIZED FOR THE WRONG MOMENT. `round((height_inches - 60) * 4.6 + 96)` is the weight
-- the site derives for a character AT CREATION - a fourteen-year-old. Plus or minus fifty pounds
-- is a sensible net around that. But `growth.weight_step()` moves a man's weight every offseason
-- toward what he should weigh as an ADULT at his adult height, and a grown power forward at 6'8"
-- weighs a great deal more than a boy of the same height. The constraint was measuring an adult
-- against a child's midpoint.
--
-- So the headroom goes UP, which is the only direction growth moves, and the floor stays where it
-- was. At 6'8" the range becomes 138-283: a real NBA four weighs 240-260, and 283 still rejects
-- the kind of number that means something upstream read the wrong field.
--
-- This stays a plausibility net, not the rule. The comment at the top of schema.sql holds: the
-- form is the real guard, and BUILDS lives in the JS.

-- BOTH NAMES, because this constraint exists twice in this directory under two of them:
-- schema.sql declares it inline (Postgres names that `characters_weight_lbs_check`) and
-- weight_column.sql adds it explicitly as `characters_weight_lbs_sane`. Whichever was actually
-- applied to this database is the one that has to go, and dropping only one would leave the old
-- limit quietly in force while this file claimed to have fixed it.
alter table public.characters
  drop constraint if exists characters_weight_lbs_check;
alter table public.characters
  drop constraint if exists characters_weight_lbs_sane;

alter table public.characters
  add constraint characters_weight_lbs_sane check (
    weight_lbs is null
    or weight_lbs between round((height_inches - 60) * 4.6 + 96) - 50
                      and round((height_inches - 60) * 4.6 + 96) + 95);

-- Proof it now admits the man it rejected, and still refuses nonsense:
--   6'8"  derived 188  ->  allowed 138 .. 283   (Tim Turner at 240 passes)
--   7'2"  derived 215  ->  allowed 165 .. 310   (Dodger Manson at 252 passes)
--   5'8"  derived 132  ->  allowed  82 .. 227
