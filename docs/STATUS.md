# Where the Cheezeyverse is — and how to pick it back up

Last updated 2026-09-20. `CONVENTIONS.md` is the source of truth for decisions and the file
format; this file is the "what state is everything in" page.

**Everything runs on SERVERPC now** (192.168.1.97), not on the desktop. The saves, FBPB3, the
commissioner panel and the `.env` holding the Supabase key and the Discord webhook all live
there; the desktop is for editing code and pressing Sim Week in a browser. A checkout on the
desktop can run the codec and every test, but its copies of the saves are stale by design.

## FOR SERVERPC - read this before the next sim (2026-09-20, from the desktop)

Two sessions worked on this universe today, one per machine, and they collided once already:
both built `weight_lbs` four hours apart. **`git fetch` before starting anything here.** The
desktop's work is all on master now.

**Pull before the next sim.** Two of today's fixes change what happens when something goes
wrong mid-run, and they only exist in the repo:

- `_activate_pending` no longer lets one unplaceable character abort the whole week. A slot the
  store thinks is free that the save cannot produce used to raise straight out of the
  activation phase and take everybody's sim with it. He stays pending; the week carries on.
  (This is the one edit the desktop made inside `simweek.py` - ten lines around the existing
  `ch.stamp_character` call.)
- The offseason now copies all three saves before its first write and puts every one of them
  back if anything raises. It never restored anything before, despite its own docstring saying
  so, and growth is cumulative - so a half-finished offseason plus the obvious retry grew
  people twice with nothing to detect it.

**What else landed:** weight is stored, written into league.dat and follows a character as he
grows (CONVENTIONS.md has the curve and the measurements behind it); `promote()` now carries
his weight across a level change; a vacated reserve slot gets its own height and weight back
instead of keeping the departed character's; five columns the commissioner could write but
never read are in `CHARACTER_COLUMNS`.

**`tests/test_announce.py` was failing on the desktop and passing here.** Not a bug in the
card: it reads `SITE_URL`, which is set in this machine's `.env` and nowhere else, so the test
was testing the machine. It now sets the variable itself and passes on both.

**Still yours, and still the last unknown:** a sim crossing the end of a season. `run_sim`
refuses to cross it. Rehearse on a copy of CV_Prep before lifting that - the desktop built
`tools/offseason_rehearsal.py` for the step after it, and the same sandbox pattern applies.

**Not done, and nobody should assume it is:** the offseason has never talked to Supabase. The
rehearsal uses the local JSON store.
## Restarting your PC and getting back here

Nothing is lost by a restart. Everything that matters is on disk and in git.

```
cd C:\claude\hoops-universe
claude --continue
```

`--continue` resumes the most recent session in that folder; `claude` then `/resume` lets you pick
from a list. Claude's project memory also carries the decisions across, so a brand new session can
read `CONVENTIONS.md` + this file and be current.

Things that do **not** survive a restart, and how to bring them back:

| What | Restart it with |
|---|---|
| The commissioner panel (Sim Week lives here) | on SERVERPC: `python -m commissioner.app --lan` → http://192.168.1.97:5095 |
| Local preview of the character site | `cd site && python -m http.server 5098` → http://127.0.0.1:5098 |
| FBPB3 | The driver launches it itself. Make sure only one copy is running. |

The three saves live in `C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\leaguedata\`
and are not in git. `backups/` in this project holds timestamped copies of every codec write.

## Green — done and verified

- **The three saves exist and verify clean.** `python tools/verify_save.py` prints ALL PASS:
  CV_Prep and CV_College 16 teams x 15, CV_Pro 20 x 15, every reserve slot findable, prep ages 14-17.
- **The codec** reads and writes ratings, potentials, DOB, height, position, experience and team
  membership, verified against the game's own exports. `python tests/test_codec.py` — 18 checks.
- **The New Game wizard is automated** end to end (`tools/create_universe.py`), so the universe can
  be rebuilt from scratch in about five minutes if anything is ever wrong with it.
- **`supabase/schema.sql` is live** on project `ldybkcsmgleausdnwdmb`, including the trigger that
  prices every upgrade server-side. The browser cannot name its own price. Live as of the schema
  BEFORE `weight_lbs` - the file has moved on since and has not been re-run. See Amber.
- **The Supabase store is complete.** It was not: seven functions the app calls existed only in the
  local JSON fallback, so with Supabase configured, creating a character, every sim week and the
  whole offseason each raised. Fixed 2026-09-17.
- **`tools/e2e_test.py` is the proof.** It builds a character through the real `site/js/rules.js`
  rather than re-implementing the quiz, sims, buys a rating and a ceiling, and then reads the
  actual bytes back out of the save.
- **Seven real people are playing** in CV_Prep, and the loop works unattended: sim, publish,
  spend, repeat. Twelve tests pass.
- **Income scales with level** (prep 1, college 2, pro 3 a week) since 2026-09-19.
  `supabase/level_income.sql` has been applied to the live project.
- **The end-of-season bonus** is built and dry-run against the live saves, but has never run for
  real - no season has ended yet. `commissioner/seasonbonus.py`.
- **Discord notifications** post when a sim starts, finishes or fails. The webhook is in `.env`
  on SERVERPC; unset means silent.
- **The site knows about the season.** `publish` emits `leagues/<key>/stats.json` (counting
  stats, league ranks, elite lines, W-L and seeds, true shooting), which feeds the Season tab on
  My players and the at-a-glance panel on the front page.

## Amber — works, with a known rough edge

- **`supabase/schema.sql` is ahead of the live project: `weight_lbs` is not there yet.** Until it
  is run in the Supabase SQL Editor, `commissioner/store.py` and the website both ask for a column
  the database does not have, and every read of `characters` comes back HTTP 400 - so the sim, the
  panel and every page that lists a character are down until it is pasted and run. Both sides now
  say that in words rather than repeating PostgREST's "column does not exist". Editing that file
  deploys nothing; running it is the deploy. `python tests/test_character_columns.py` asks the
  live database and is the check that it landed.
- **The seven are catching up to their real weight over the next few offseasons.** Weight now
  follows height and age (CONVENTIONS.md has the curve), and all seven are currently carrying the
  weight of the filler whose slot they took - two of them the generator's 120 lb floor. Rather
  than one correction of up to 50 lbs, `growth.WEIGHT_CATCHUP_PER_YEAR` closes 12 lbs of the gap
  a year on top of their own growth, so they land on the curve between the next offseason and
  four of them. While that runs, a character's career page (the model) and his league page (the
  file) show different weights; set that constant to `None` to correct everybody at once instead.
  Nothing needs backfilling in the database: with no `weight_lbs` recorded their offset falls out
  of `build_weight(height, build)`.

- **The AI will cut a 14-year-old** for an adult free agent given the chance, and signs 70-95
  replacements a week. League Options has settings for trades but none for signings, so
  `tools/protect_rosters.py` runs at the top of every Sim Week and undoes it. If characters ever
  vanish, that is the first thing to check.
- **Reserve slot ages drift.** A slot's birthday is fixed at universe creation, and a character
  inherits it. Today every free prep slot is 14-17, which is right; many seasons in, the
  unclaimed ones will be older than a prep player should be. Not a problem yet.
- **The offseason has still never run on the live universe, but it has now been rehearsed end to end.**
  `python tools/offseason_rehearsal.py` builds a sandbox - copies of the three saves, a
  local JSON store, Discord stubbed - stamps in seven characters chosen to take every
  branch, and drives the real `run_offseason` through it: 44 checks, covering growth,
  promotion, the draft, retirement, refill, payment and the season roll. It also runs the
  four ways it is known to go wrong (destination league full, a character the game
  deleted, two records with one name, a write that fails halfway) and the empty universe.
  What that does NOT cover is Supabase: the rehearsal uses the local store, so the live
  run is still the first time the offseason talks to PostgREST.
- **The step BEFORE it is the untested one now: crossing the end of a season.** `sim_days`
  clicks SIM DAY blind, and the offseason never drives FBPB3's own playoffs or rollover,
  so what the game does on the next day is unknown: a modal that eats later clicks,
  playoff games, its own aging and re-signing, any of which would be the game taking over
  a rollover `offseason.py` owns. `run_sim` refuses to cross it and the panel reports the
  number. Rehearse on a copy of CV_Prep before lifting that.
- **The local saves on this desktop are NOT the live universe**, and mistaking them for it is an
  easy and expensive error. They are the abandoned pre-reset one: season 2030, and none of the
  seven characters are in them. The live saves are on SERVERPC. A code review on 2026-09-19
  measured the desktop copies and concluded the regular season had 156 days left and that the
  season-end guard was reading a date format nothing produces; both were true of the dead
  universe and false of the live one. Measure against SERVERPC, or against the published site,
  which is generated from it.
- **Sims need a live desktop.** Stay connected to SERVERPC over RDP at 2560x1440, or use VNC or
  AnyDesk. The headless console is 1024x768 and the 1019x762 game window plus the taskbar does
  not fit; there is no dummy plug in yet. `docs/SERVER.md` covers the `tscon` part.
- **The site has no cache-busting on its asset URLs.** Everything is served `max-age=600`, so for
  ten minutes after a publish a browser can hold a mixture of old and new files. Changing a
  shared signature in `site/js/ui.js` is therefore a breaking change for ten minutes; it has
  already shipped a visible bug once. Keep shared signatures additive, or give the publish step
  content-hashed names.
- **Seven stamina snapshots were never written.** `raise_stamina --sync-store` changed the live
  sheet on 2026-09-19 without recording it, so the next sim's snapshot carries the whole floor as
  one step in every character's growth line. The tool records history now; the seven old rows
  stay missing unless somebody decides otherwise.

## Red — nothing is broken right now

Six real bugs were found and fixed on 2026-09-17 by actually running the thing end to end rather
than reading it. Every one of them was silent:

1. `protect_rosters` restored orphaned reserve slots and then re-read the file without saving,
   throwing the work away. Two Pro rosters were short.
2. The Supabase store was missing `characters()`, so roster protection never ran on any week, and
   `tools/protect_rosters.py` fell back to an empty character list — which would have defanged and
   renamed live characters.
3. The third HTML export of a session always failed: a leftover dialog eats the menu click, and no
   amount of waiting helps because the screen is not coming.
4. The website never sends a birthday, so the first sim after anybody created a character died.
   And `game_dob` round-trips through a Postgres `date`, coming back ISO, which matches no player
   in any save.
5. **Potentials were never written at all.** They are keyed by rating everywhere except the codec,
   and nothing translated, so `stamp_character` wrote none of them — and FBPB3 then pulled every
   rating down to the dormant filler's ceiling. A character built at 17/28/37 came out at 7/19/16.
6. A **potential purchase was applied as a rating purchase**, capped at the very ceiling the buyer
   was raising. It did nothing and the points were charged. That is the one thing a person does
   every week.

## Waiting on you

Nothing. Discord sign-in, the redirect URLs, the level-income migration and the Discord webhook
are all done, and seven people are playing.

For reference, if the project is ever rebuilt: sign-in needs the Supabase URL Configuration
(site URL plus redirect URLs ending `/**`, because the site returns to whatever page you signed
in from) and a Discord application whose OAuth2 redirect is the **Supabase** callback
`https://<project>.supabase.co/auth/v1/callback`, not the Pages URL. Then
`update public.profiles set is_admin = true where discord_username = '<you>';`

## Rebuilding the universe from nothing

```
python tools/generate_universe.py     # league + roster CSVs from universe/teams.csv
python tools/create_universe.py       # three New Games, about a minute each
python tools/stamp_dobs.py            # put the real birthdays back
python tools/protect_rosters.py       # undo the AI's preseason signings
python tools/verify_save.py           # must print ALL PASS
```

Teams are frozen once a save exists, so edit `universe/teams.csv` before step two, not after.
