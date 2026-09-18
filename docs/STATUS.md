# Where the Cheezeyverse is — and how to pick it back up

Last updated 2026-09-17. `CONVENTIONS.md` is the source of truth for decisions and the file
format; this file is the "what state is everything in" page.

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
| The commissioner panel (Sim Week lives here) | `python -m commissioner.app` → http://127.0.0.1:5095 |
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

## Amber — works, with a known rough edge

- **The AI will cut a 14-year-old** for an adult free agent given the chance, and signs 70-95
  replacements a week. League Options has settings for trades but none for signings, so
  `tools/protect_rosters.py` runs at the top of every Sim Week and undoes it. If characters ever
  vanish, that is the first thing to check.
- **Reserve slot ages drift.** A slot's birthday is fixed at universe creation, and a character
  inherits it. In 2030 every free prep slot is 14-17, which is right; many seasons in, the
  unclaimed ones will be older than a prep player should be. Not a problem yet.
- **The offseason has not been run against Supabase.** It is built, and its store calls now exist,
  but no season has rolled over on the live project.

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

Nobody can log in until these two exist.

1. **Supabase → Authentication → URL Configuration**
   https://supabase.com/dashboard/project/ldybkcsmgleausdnwdmb/auth/url-configuration
   - Site URL: `https://waligug.github.io/cheezeyverse/`
   - Redirect URLs, one at a time:
     `https://waligug.github.io/cheezeyverse/**`, `http://127.0.0.1:5098/**`,
     `http://localhost:5098/**`
   The site asks to come back to whatever page you signed in from, so the `**` matters.
2. **A Discord application** (discord.com/developers)
   - New Application → OAuth2 → Redirects → add
     `https://ldybkcsmgleausdnwdmb.supabase.co/auth/v1/callback`
     That is the Supabase callback, not the Pages URL.
   - Copy the Client ID and Client Secret into
     Supabase → Authentication → Providers → Discord, and enable it.
3. After signing in once:
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
