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
| Local preview of the character site | `cd site && python -m http.server 5098` → http://127.0.0.1:5098 |
| Local preview of the skinned league pages | `cd tmp/preview && python -m http.server 5099` |
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
- **Character plumbing** (`commissioner/characters.py`): claim a reserve slot, stamp a character
  onto it, apply point spends as deltas on whatever the game currently says, verify every write
  landed before anything is published.
- **The character site and Supabase schema** exist: Discord login, creation, the cost curve, the
  point ledger, row-level security.

## Amber — built but not yet proven end to end

- **The Sim Week pipeline has run end to end** (2026-09-17): a character was stamped into CV_Prep,
  two point spends applied as deltas, 7 days simmed, 605 pages exported and published, points
  granted — 160 seconds. Run it from the panel (`python -m commissioner.app`, port 5095) or call
  `commissioner.simweek.run_sim`. It is proven against the local JSON store, not yet against Supabase.
- **The AI will cut a 14-year-old** for an adult free agent given the chance; it took 80 of our 240
  Prep players on the first sim. `tools/protect_rosters.py` runs at the top of every Sim Week and
  undoes it. If characters ever vanish, that is the first thing to check.
- **The site has now been rendered in a browser** (2026-09-17) and four bugs were found and fixed
  that static checks could not see: the create page was blank without Supabase, a temporal-dead-zone
  error that threw at module load with no console message, a heading reading "He scouting report",
  and the scouts naming a player's type before a single question was answered. The create page now
  runs in **preview mode** with no Supabase at all, so the quiz can be played with and demoed - only
  the final save is blocked. `me.html` is still unexercised: it lists characters you own, and without
  a signed-in account there is nothing to show.
- **`supabase/schema.sql` has never been run against a real Postgres.**

## Red — nothing is broken right now

The HTML Output problem is fixed: it was writing into the wrong save, because `load_save` picked a
row using `league.dat`'s file mtime while the game orders that list by its own last-save time in
`saveinfo.dat`. All three league sites generate, including per-player pages, and publish into
`site/leagues/<key>/`.

The remaining gap is not a bug, it is unbuilt: **the offseason** — promotions, declarations, the
app-run draft and applying a year of height growth. Nothing has aged yet.

## Waiting on you

Nobody can log in until these two exist. Everything else can be built without them.

1. **A Supabase project** (free tier).
   - SQL Editor → run `supabase/schema.sql`, then `supabase/seed.sql`.
   - Settings → API: the **anon** key goes in `site/config.js`; the **service_role** key goes in
     `.env` in this folder (copy `.env.example`). The service key must never go in `site/`.
   - Authentication → URL Configuration: Site URL = your GitHub Pages URL, and allow
     `http://localhost:*` so local testing works.
2. **A Discord application** (discord.com/developers).
   - OAuth2 → Redirects → add `https://<project-ref>.supabase.co/auth/v1/callback`. That is the
     Supabase callback, not the Pages URL.
   - Copy the Client ID and Client Secret into Supabase → Authentication → Providers → Discord.
3. After signing in once:
   `update public.profiles set is_admin = true where discord_username = '<you>';`

## Rebuilding the universe from nothing

```
python tools/generate_universe.py     # league + roster CSVs from universe/teams.csv
python tools/create_universe.py       # three New Games, about a minute each
python tools/stamp_dobs.py            # put the real birthdays back
python tools/release_intruders.py     # undo the AI's preseason signings
python tools/verify_save.py           # must print ALL PASS
```

Teams are frozen once a save exists, so edit `universe/teams.csv` before step two, not after.
