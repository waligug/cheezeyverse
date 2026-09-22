-- weight_lbs: the weight he CHOSE, instead of the one we worked out for him.
--
-- Until now weight was derived wherever it was needed - buildWeight(height, build) in the site's
-- rules.js, and nothing at all on the commissioner side, which left the game with whatever the
-- reserve slot came with. That agreed with itself because both ends did the same sum. The
-- builder now has a slider, so the two ends can disagree, and the way that shows up is the
-- worst kind: the review card says 185 lbs, the game gives him 160, and nothing anywhere
-- explains the difference.
--
-- NULLABLE ON PURPOSE. Every character created before the slider existed has no chosen weight,
-- and the honest record of that is null, not a number back-filled to look like a decision. The
-- commissioner derives the old formula when it reads null, so they keep the weight they have
-- always had.
--
-- The check is tied to HIS OWN HEIGHT rather than being a flat range, because 300 lbs is
-- reasonable at 7'2" and not at 5'4". The band is the base weight for his height either side by
-- 50: the builds run -14 to +20 lbs and the slider adds 25 either side of that, so 50 clears
-- the widest legal choice with room and still refuses a number nobody could have picked.
--
-- Paste this whole file into the Supabase SQL editor (Dashboard -> SQL Editor -> New query).
-- It is safe to run twice.

alter table public.characters
  add column if not exists weight_lbs int;

comment on column public.characters.weight_lbs is
  'Weight in pounds as chosen in the builder. Null means never chosen: derive from height+build.';

alter table public.characters
  drop constraint if exists characters_weight_lbs_sane;
alter table public.characters
  add constraint characters_weight_lbs_sane check (
    weight_lbs is null
    or weight_lbs between round((height_inches - 60) * 4.6 + 96) - 50
                      and round((height_inches - 60) * 4.6 + 96) + 95
  );

-- Readable by everybody who can read the rest of him, and settable at creation only. Nothing
-- outside the service role may UPDATE it: he picks it once, at fourteen, like his height.
grant select (weight_lbs) on public.characters to anon, authenticated;
grant insert (weight_lbs) on public.characters to authenticated;
