# Where the Cheezeyverse is — and how to pick it back up

Last updated 2026-09-19. `CONVENTIONS.md` is the source of truth for decisions and the file
format; this file is the "what state is everything in" page.

**Everything runs on SERVERPC now** (192.168.1.97), not on the desktop. The saves, FBPB3, the
commissioner panel and the `.env` holding the Supabase key and the Discord webhook all live
there; the desktop is for editing code and pressing Sim Week in a browser. A checkout on the
desktop can run the codec and every test, but its copies of the saves are stale by design.

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
  prices every upgrade server-side. The browser cannot name its own price.
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

- **The AI will cut a 14-year-old** for an adult free agent given the chance, and signs 70-95
  replacements a week. League Options has settings for trades but none for signings, so
  `tools/protect_rosters.py` runs at the top of every Sim Week and undoes it. If characters ever
  vanish, that is the first thing to check.
- **Reserve slot ages drift.** A slot's birthday is fixed at universe creation, and a character
  inherits it. Today every free prep slot is 14-17, which is right; many seasons in, the
  unclaimed ones will be older than a prep player should be. Not a problem yet.
- **The offseason has never run for real.** It is built, dry-run repeatedly against the live
  saves, and the season bonus rides on it - but no season has rolled over. Prep is 22-27 games
  into a 30-game season, so the first real one is a few sim-weeks away and it is the largest
  untested thing in the project.
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
