# Hoops Universe conventions

Source of truth for decisions and the `league.dat` layout. Plan: `~/.claude/plans/quiet-dazzling-pebble.md`.

## Rules
- Never write to a live save the user plays; codec tests run on a copy (`leaguedata/Chung_test`).
- FBPB3 must be closed before any read/copy/write of `league.dat` (it rewrites the file and `league.bak` on save).
- `league.bak` is the game's own rotation, not our backup. Our backups go to `backups/`.
- Codec writes are in-place int16 overwrites only. The file layout (length, string contents) never changes.
- Never write derived fields (CurrentRating/FutureRating/Overall*); the game recomputes them.
- Never write the ratings-history rows after the ratings block.

## league.dat player records (FBPB3 3.1.0)
Confirmed 390/390 players on the Chung save against the in-game Player File export
(`fixtures/exports/chung_export.csv`) and Output MDB `Player` table (`fixtures/exports/chung_mdb_player.csv`).

Strings: uint16 LE length + latin-1 bytes. Numbers: int16 LE. Records are sequential but variable length
(awards, injury history, date strings, stat history), so fields are addressed from three anchors.

| Anchor | Definition |
|---|---|
| **S** | start of name triple: `str Full`, `str First`, `str Last` (Full == First + " " + Last) |
| **bio** | after `str Nickname`: int16 `Height`(in), `Weight`, `0`, `BirthMonth`, `BirthDay`, `BirthYear` |
| **E** | after bio ints + `str College`, `str City`, `str State`, `str Nation` |
| **R** | current-ratings block: 18 current + 12 potential values all 0..100, R+36 in 0..100, and preceded either by `1,1` + 48 zero bytes (fresh save) or by a per-season **archive row** (int16 season, 18 ratings, height, weight = 42 bytes). Candidates are tried in order and only accepted when the two team fields also line up, which filters out non-player name triples |

Other fixed layout facts: the last injury string (current injury, may be empty) ends exactly at R-64.
Duplicate names exist (Tony Thompson ×2, Charles Taylor ×2); disambiguate by DOB.

| Field | Offset | Confidence |
|---|---|---|
| Position (1 C, 2 PF, 3 SF, 4 SG, 5 PG) | E+18 | 390/390 |
| Team id (MDB `Team.ID`; FA = -1, Draft = -2) | E+40 | 390/390; save-time copy, rebuilt by the game on load |
| Team1 (current team, read on load) | T1 = unique q in (E+84, R) with a `01 00 02 00 00 00 00 00 00 00` array header at q-20 and at q+2 | 390/390 |
| Team2 (contract/last team) | T1+166 | 264/264 rostered; FA/draft keep former team |
| Player id | not stored at a fixed offset and **not contiguous** once a save has aged: recovered from the roster arrays (records are in ascending id order, team records in ascending team-id order, each player's Team field says which array he belongs to); free agents / draft players fall back to the `(id, id)` int16 pair before the name, bounded by their neighbours | 390/390 vs MDB |
| Experience | E+82 | 390/390 |
| Inactive (0 = dressed out, -1 = benched) | E+46 | 400/410 on the aged save; the game recomputes it on load (a roster over the limit forces the extra player inactive) |
| Ratings ×18 | R+0..R+34 in order: Inside, JumpShot, FT, 3pUsage, 3pShot, Handling, Passing, Quickness, PostD, PerimD, Stealing, Blocking, OReb, DReb, Jumping, Strength, Stamina, Fouling | 390/390 |
| Potentials ×12 | R+168..R+190 in order: Inside, JumpShot, FT, 3pShot, Handling, Passing, OReb, DReb, PostD, PerimD, Stealing, Blocking | 390/390 |
| Happiness | R-62 | 386/386, not yet used |
| OverallRating / OverallPotential | R+276 / R+278 | **unverified** (mostly-zero values) |
| Ratings history snapshots | ~94-byte rows from R+192 onward | observed, read-only |
| Per-season archive rows | 42 bytes each immediately before R once a season has been played | confirmed on the aged save |
| Player id, uniform | a few bytes before S (S-24 / S-4 most common) | **not fixed**; needs more work |

## Team membership (all must agree, or FBPB3 releases the player to FA on load)
- Player fields: E+40 `Team`, `Team1`, `Team2` (above).
- Roster: VB6 dynamic array in the team record before the first player record: `01 00 | int32 n | int32 0` then
  n int16 with element 0 = 0 and elements 1..n-1 = player ids. Identified per team as the unique array whose id set
  equals the players with that E+40 team.
- Lineup: 14 int16 slots at roster_end + 22 (roster_end = header + 10 + 2n); ids ⊂ roster, zero-padded.
- Depth charts: 5 blocks per team, 162 bytes each (`00 00` + 80 int16 minute slots), contiguous near the end of the
  file in ascending team-id order. Region start found as the unique offset where every block's ids belong to the
  expected team and slot 1 is non-zero.
- Game-history structures (schedule leaders, box scores) also hold ids; never rewrite them.
- **Swap** (codec `swap_teams`) replaces the two ids in both teams' roster, lineup and depth blocks and sets all three
  team fields. File length unchanged.

- **Release** (codec `release`): roster array count -1 and the id's 2 bytes removed (file shifts), lineup compacted,
  his depth-chart minutes given to the most-used teammate in each block, Team/Team1 = -1, Team2 keeps former team.
- **Sign** (codec `sign`): roster count +1 and id appended, all three team fields = team, Inactive cleared, and the
  player takes the lineup slot and depth-chart minutes of the least-used player at his position (depth block k holds
  position k+1: C, PF, SF, SG, PG).
- **Roster limit matters**: signing onto a full (15-man) roster leaves the newcomer Inactive after load no matter what
  the file says, so a transition must free a slot first - release then sign, or use `swap_teams`. Once the roster size
  is legal the signed player is active; how many minutes he gets is then the AI coach's call.
- **Rename** (codec `rename`): re-encodes the three name strings, changing the record's length; verified in-game with
  both a longer and a shorter name (this is how a reserve slot becomes a real character).
- Length changes are fine: the file is a sequential VB6 stream; the codec re-parses after every splice.

## League Options that matter (Tools → League Options, real combo boxes)
- Finances (402,190): **Finances Off** required. With Full Finances a codec-signed player (no contract) is released on load.
- Attribute Style (402,166): **0-100** makes MDB `Player` current ratings numeric (potentials stay letters).
- Scouting (402,334): set **Off** for the universe; with it On the MDB/UI ratings are fuzzed (Harper Inside 27 shows 24).
- Autosave (788,214): **Never** (the app controls saves). Cpu offers trades (788,335): **No**.

Unknowns to resolve: DOB age floor, depth charts for signed players, draft pool / reserve parking, offseason flow.

## FBPB3 automation facts
- VB6 app, no native menus. Top bar and buttons are owner-drawn `ThunderRT6UserControlDC`, so clicks are
  window-relative coordinates. Combo boxes (`ThunderRT6ComboBox`), text boxes and message boxes (`#32770`) are real
  Win32 controls and can be driven by pywinauto `backend="win32"`.
- Text boxes need real keystrokes (`type_keys`); `set_edit_text` does not enable dependent buttons.
- Tools → Output MDB writes `leaguedata/<save>/LeagueOutput.mdb` and shows a "File Created" dialog (OK button).
- Tools → Current League Editor → Sort by Players → EXPORT → SAVE → name → writes `PlayerFiles/<name>.csv`.
- Read MDBs with 32-bit PowerShell + Jet OLEDB (`commissioner/export/mdb_query.ps1`).

## Phase 0 results log
- 2026-09-17 **Write gate PASSED.** Codec set Chris Harper Inside 27→60 / PotInside 37→75, Handling 5→33 /
  PotHandling 7→40, Billie Holmes Passing 21→55 / PotPassing 23→66 on `Chung_test`. FBPB3 loaded it (player export
  showed new values), simmed to postseason, saved (file grew 6,905,603 → 6,938,578 bytes), and the codec re-parsed
  390/390 players with the edited values intact.
- Driver steps proven hands-off: launch, Load Career (row click), Current League Editor player export,
  Output MDB, Hot Seat Sim Day, top-bar SAVE (name box prefilled → SAVE at 622,495), EXIT
  ("Save before exiting?" Yes/No/Cancel message box).
- Export screens return to the League Editor; EXIT (917,662) → Tools screen → left-nav HOT SEAT (55,95).
- 2026-09-17 **Team-field-only move FAILED as expected**: E+40 alone is rebuilt from rosters on load.
- 2026-09-17 Swap with rosters/lineups/depth + E+40 only: both players released to FA on load (Team1/Team2 stale).
- 2026-09-17 **Team swap PASSED.** Marvin Williams (272, RIO) ↔ Lewis Miller (244, TOR) with all structures + 3 team
  fields: player export and MDB show the new teams, both played 2 games for their new clubs, in-game save + codec
  re-parse shows 390 players / 18 teams consistent.
- 2026-09-17 Release PASSED (Marvin Williams to FA, survives load + save). Sign with Full Finances FAILED (released on
  load). Switched `Chung_test` to Finances Off via League Options → sign PASSED (Howard Aman on TOR after load and after
  game save, contract 0). No regular-season games left in Chung to confirm he receives minutes.
- 2026-09-17 **Age floor: no floor found.** Birth years giving in-game ages 12-15 all load and sim; a save with a
  13- and a 14-year-old simmed a full season plus offseason with both still rostered and no crash. One crash during
  testing was a stray click, not the ages (not reproducible). The game does sometimes rewrite an edited DOB to
  `12/1/<year>` at season rollover, so re-assert DOB after each offseason if exact birthdays matter.
- 2026-09-17 **Offseason runs hands-off**: sim months to the end of the postseason, then END SEASON -> OFFSEASON ->
  HIRE STAFF (its screen needs PROCESS ALL, then the same button becomes PROCEED) -> FREE AGENCY, and the next
  regular season starts. Draft lottery / rookie draft / dispersal / expansion stay disabled in this league.
- 2026-09-17 **Codec survives an aged save**: `tests/test_codec.py` parses both fixtures (390 fresh, 409 aged),
  matches the game's own exports field for field, and round-trips rating edits, swaps and release+sign on both.
- Two FBPB3 instances at once silently break automation (clicks and exports go to the wrong league). `launch()`
  now refuses to run when more than one process exists.
- Menu labels are windowless VB6 controls: they only react to real mouse input with the window genuinely on top,
  so `click(real=True)` raises the window first, and `output_mdb` confirms by the file timestamp and retries.
- 2026-09-17 **Age confirmed by the game itself**: the aged save's own MDB reports Chris Harper age 12 and Billie
  Holmes 14, both active and rostered, after a full season plus offseason. No minimum-age problem.
- 2026-09-17 Ratings are not capped at 100 (a draft-pool player had Quickness 101; a real player file reaches 139),
  so the codec accepts 0..150.
- 2026-09-17 Rename + sign verified in-game together: renamed players kept their new names through a sim and the
  game's own save, and appeared in box scores under them.

## FBPB3 HTML Output (the public site) — reference: the Stabbyverse sites
Captured 2026-09-17 from the live Stabbyverse sites into `fixtures/html-output/svprep/`
(`http://svprep.atspace.cc/html/`). This is stock FBPB3 `Tools -> Commish Tools -> HTML Output`,
uploaded by FTP (their commissioner used FileZilla; atspace.cc is a free FTP host).

- **Structure**: `index.htm` is a two-frame FRAMESET (`menu.htm` 150px left, data pane right, default
  `standings.htm`). Title is the league name + season, e.g. `SV Prep S5`.
- **18 menu pages**: standings, playoffstandings, schedule, leaders, teamleaders, transactions, injuries,
  freeagents, waiverwire, potentialfreeagents, staff, draft, awards, seasonawards, playoffs, playoffleaders,
  champs, humancoaches.
- **Team pages**: `rosters/roster<teamid>.htm`, one per team, linked only from `standings.htm`. Each is a
  single ~160 KB page: team/owner/arena/finances, team stat ranks, staff, roster (bio), current ratings,
  potentials, season and career stats (basic + shooting + advanced), salary table, full schedule with results.
- **There are no per-player pages.** The only internal links in the whole site are the 16 roster pages.
  A career page that follows one character prep -> college -> pro has to be built by us from the MDB + codec.
- Links are relative (`./rosters/rosterN.htm`), so the folder hosts as-is. Styling is inline `<style>` per page,
  team colour driven (`#990000` for SV Prep). Images referenced from `images/`.
- Only the current season is on the site; `champs.htm` is the only cross-season page (season, champion, MVP).
- Total upload is ~2 MB per league per refresh (`freeagents.htm` alone is 920 KB).

### What the Stabbyverse sites imply about league layout
Three separate sites, one per level: `svprep` (SV Prep S5), `svcollege` (SV College S5),
`stabbyverse` (StabbyVerse S4, the pro league). **The pro league is a season behind the other two**, which one
FBPB3 save cannot do - leagues inside a save advance together. So the Stabbyverse runs **three separate saves**.
Their prep league runs Finances ON, a 30-game season, ages 14-19, and marks created characters with a `*`
prefix on both names (`*Josh *Hall`).

Open decision: one save with three leagues (our plan; codec moves players between them; HTML Output coverage
of non-active leagues is **unverified**) vs three saves (their proven layout; promotion = stamping a reserve
slot in the destination save, which `rename` + `set` already do). Resolve with a throwaway two-league New Game
before the real universe is created.

## Cheezeyverse layout (decided 2026-09-17)
**Three saves, one per level**, matching the Stabbyverse: `CV_Prep`, `CV_College`, `CV_Pro`. Each has its own
league file, roster file, HTML Output and site. A Sim Week is three launch/load/sim/export/save/exit cycles.
Promotion between levels is not a team move: it claims a **reserve slot** in the destination save and stamps the
character onto it (codec `rename` + `set`), because a save's leagues cannot see each other. Career history across
levels is stitched by us, which we have to do regardless - FBPB3's HTML Output has no per-player page.

Defined in `commissioner/universe/config.py`, generated by `tools/generate_universe.py`:

| League | Save | Teams | Roster | Fillers | Reserves | Ages | Games |
|---|---|---|---|---|---|---|---|
| Cheezeyverse Prep (CVP) | CV_Prep | 16 | 15 | 12/team = 192 | 3/team = **48** | 14-17 | 30 |
| Cheezeyverse College (CVC) | CV_College | 16 | 15 | 12/team = 192 | 3/team = **48** | 18-21 | 32 |
| Cheezeyverse (CV) | CV_Pro | 20 | 15 | 12/team = 240 | 3/team = **60** | 22-34 | 58 |

- `filler_per_team + reserve_per_team` must stay <= 15: signing onto a full roster silently inactivates the
  player (proven in Phase 0). **Reserve count is therefore the hard ceiling on concurrent characters per level.**
- Reserve slots are ordinary-looking players rated 3-12 with potentials 10-25, so the AI gives them no minutes
  and the public site shows no placeholder junk. `universe/manifest.json` records which rows they are; the codec
  finds one by name + DOB.
- The league CSV and the roster CSV are generated from the same team table. FBPB3 matches a player's Team column
  against team abbreviation, name and nickname, and silently makes him a free agent when nothing matches.
- Rookie/lottery/expansion/dispersal drafts are all 0 in the league file: the app runs the draft.
- `Age.ini` is left alone. It only has keys 16-45, it is global install state, and initial ages come from the
  roster CSV (with the codec able to go lower - a rostered 12-year-old is already proven).

## Playoff bracket encoding (confirmed from the game's own data)
`Round1..Round4` in a league file is always four entries. **Round4 is the final**; a bracket with fewer than
four rounds pads with **leading zeros**. Derived from every season in
`Historical Leagues/North America/Leagues/USA{1,2,3}.csv`:

| Playoff teams | Round1..4 seen in the game's data |
|---|---|
| 4 | `(0,0,1,1)`, `(0,0,3,3)` |
| 8 | `(0,1,1,3)`, `(0,3,3,3)`, `(0,5,7,7)` |
| 12 | `(3,7,7,7)` |
| 16 | `(5,7,7,7)`, `(7,7,7,7)` |

Series lengths are odd. The only exception to the leading-zero rule is the 6-team bracket (`(5,5,7,7)`, the
early-NBA divisional format); no Cheezeyverse league uses it. `config._check_bracket()` enforces all of this,
because leagues can only be created at New Game and a wrong bracket means redoing the save.

Our brackets: Prep 8 teams `(0,1,1,3)` (single elimination into a best-of-3 final), College 8 teams `(0,1,1,1)`
(single elimination throughout), Pro 8 of 20 teams `(0,5,7,7)`.

- Teams are data, not code: `universe/teams.csv` is the source of truth and overrides the tables in
  `config.py` whenever it exists. Nate owns that file.
- The prep league is an **elite** prep league, so heights use the adult ranges with at most 1 inch off for
  the youngest (`LeagueSpec.youth_shrink`), not the 5 inches an ordinary 14-year-old population would get.
- **CPU trades stay ON** (Nate's call): the AI moving players around is flavour, and a trade is only a team
  change, so a traded character keeps every rating.

## Creating the saves (automated 2026-09-17)
`tools/create_universe.py` drives FBPB3's New Game wizard end to end; `commissioner/driver/newgame.py`
holds the screen map. Both wizard screens are real Win32 controls, so combos and text boxes are driven by
pywinauto directly and only three things need a real mouse click (the league row, ACTIVATE, CONTINUE).

Full sequence to build the universe from nothing:

    python tools/generate_universe.py      # league + roster CSVs from universe/teams.csv
    python tools/create_universe.py        # three New Games, ~1 min each
    python tools/stamp_dobs.py             # put the real birthdays back
    python tools/release_intruders.py      # undo AI preseason signings
    python tools/verify_save.py            # must print ALL PASS

### Three things the game does that will silently ruin a save

1. **`Contract1` must be non-zero in the player file.** A row with `Contract1 = 0` is imported as a
   **free agent** no matter what its Team column says - the league looks healthy, every team exists, and
   not one of our players is on a roster. Proven by bisection: the identical file with
   `Contract1 = 1,000,000` filled all 16 prep rosters (240/240); with 0 it filled none. True even with
   Finances Off. This is why the Stabbyverse roster pages show $1,000,000 salaries on created players.
   The generator writes `IMPORT_CONTRACT = 1000000`. Ruled out along the way: the match key (abbreviation
   vs nickname vs city all behave identically), age, season year, finance level, and our league file
   (the stock league with our players failed too, the stock league with stock players worked).
2. **The importer will not create a player younger than 16.** It keeps day and month and pulls the birth
   *year* forward until the player is 16 at the first season - which erases the entire point of a 14-17
   prep league. The binary editor has no such floor, so `tools/stamp_dobs.py` writes the manifest
   birthdays back after creation. Prep ends up 14/15/16/17 = 82/69/38/51.
3. **The AI signs free agents between league creation and the first save** (Starting Stage is Preseason),
   which pushes a roster to 16 and forces somebody inactive. `tools/release_intruders.py` releases anyone
   rostered who is not in the manifest.

### Prestige
The combo reads **Global > Continental > National > Regional** (Global is the strongest league), so the
league file's numeric Prestige is an index into that list: 1 Global, 2 Continental, 3 National, 4 Regional.
Cheezeyverse: Pro 1, College 3, Prep 4. `config.validate()` rejects anything outside 1..4.

### Confirmed by the created saves
- The playoff-bracket encoding is right: prep's `(0,1,1,3)` shows in-game as None / Single game /
  Single game / Best of 3.
- Each save carries its league plus a generated pool the game adds on its own: prep 425 total
  (240 ours + 120 FA + 65 draft), college 430, pro 530. Those extras are 18+ regardless of league, so a
  prep roster spot that opens up can be filled by an adult - keep rosters full.

## FROZEN — the filler population (locked 2026-09-17)
Nate locked this after the height rebuild. From here on the generated population is **fixed**, and a
change to it is a migration against live saves, not a regeneration.

Frozen: the team tables in `universe/teams.csv` (16 prep / 16 college / 20 pro, names, abbreviations,
divisions, colours), the rating and potential bands, `filler_per_team = 12` and
`reserve_per_team = 3` (so 48 / 48 / 60 concurrent characters), the age ranges, the height model
including `youth_shrink`, and the generator seed. `universe/manifest.json` is the record of exactly
who was generated and which rows are reserve slots; the commissioner finds every slot through it, so
it must stay in step with the saves that were built from it.

What this means in practice:
- `tools/create_universe.py --force` deletes and rebuilds a save. Safe only while no character
  exists in it. Once one does, that command destroys a person's player.
- Changing `universe/teams.csv` or any generator constant no longer takes effect on a live save. It
  would need a codec migration that renames and re-rates in place.
- Adding capacity later means raising `reserve_per_team`, which is also a migration - the roster is
  already at the 15-man limit, so a new reserve slot has to displace a filler.

Heights taper with age inside each league: the full `youth_shrink` at the league's youngest age,
zero at its oldest. Prep 14-year-old point guards are 5'1"-5'9" and 17-year-olds 5'10"-6'3", which
matches the range a created 14-year-old rolls (`HEIGHT_RANGES` in `site/js/rules.js`). Those two
tables have to move together or characters stand a head shorter than their own teammates.


## The game's player pages lag a mid-season rating change
Verified 2026-09-17 against a real sim: for four unchanged fillers the codec's values matched their
generated `players/player<id>.htm` column for column, and the only player who differed was the one we
had just upgraded mid-season - his page showed Handling 24 / Passing 26 while the save (and the sim
engine) had 26 / 28.

So FBPB3's player page prints a **season-start snapshot**, not the live record. The upgrade is real -
the sim uses the live ratings, which Phase 0 proved by changing a player's behaviour - but the league
site will not show it until the season rolls over. Our own character pages read the live values from
the store, so the person who spent the point sees it immediately; the FBPB3 page is the one that
lags. Do not "fix" this by writing the archive rows: CONVENTIONS already forbids touching the
ratings-history block, and the game rebuilds it.

## The offseason (built 2026-09-17)
`commissioner/offseason.py`, in this order because each step depends on the last:

1. **Growth** - `growth.grew_this_offseason` says how many inches; this writes them into the save.
2. **Promotion** - prep after the age-17 season, college on declaring or at the four-year cap.
3. **The draft** - the app runs it (the league files ship with FBPB3's rookie draft off): declared
   players ranked by current ratings plus ceiling, teams picking in reverse standings order read
   from the published `standings.htm`.
4. **Refill** - the departing character's reserve slot is handed back its manifest name, birthday
   and floor ratings, so the level he left can take somebody new. Without this the ceiling on
   concurrent characters ratchets down by one every time anyone is promoted.

A move between levels is **not** a trade: the three saves cannot see each other, so it is a claim of
a reserve slot in the destination, a stamp carrying his ratings and height, and a refill behind him.
Verified end to end 2026-09-17: a 17-year-old grew 2 inches, moved from Saskatoon to the Moose Jaw
Mandibles carrying Inside 34 / PotInside 74, and his old slot came back as Bret Fenner.

**The AI keeps signing free agents even after the pool is defanged.** A 7-day sim put 27 of the
game's own players back onto Prep rosters. `protect_rosters.py` runs at the top of every Sim Week and
undoes it, so the system is self-healing, but a character can miss games in between. If somebody's
player stops appearing, that is the first thing to check. When a displaced player's own team has
filled up, he is signed to any team with room rather than left in free agency - the AI will not
re-sign a defanged 14-year-old, so free agency is where a career goes to die.

## Point economy, and how it was balanced (2026-09-17)
Model it, do not guess: `node tools/progression_model.mjs` prints what eight seasons of points buy.
Re-run it after touching the cost curve, points per week, the offseason lump sum or the potential
constants.

A season is **26 in-game weeks** (the prep calendar runs 2030-10-15 to 2031-04-17, 184 days), so at
1 point per week plus a **15-point offseason lump sum** a character earns about **41 points a year**.

Measured arc, spending evenly across six core skills:

| | points so far | core average | population |
|---|---|---|---|
| End of season 1 | 41 | 24 | prep fillers 8-38 |
| End of prep (season 4) | 164 | 44 | above every prep filler |
| End of college (season 7) | 287 | 56 | pro fillers 28-62 |

So four years to become the best kid in prep, four more to arrive pro-ready, and after that the only
place left to spend is buying ceilings at double rate. FBPB3's own progression runs on top of all of
this, and it develops a young player *toward his potential* - which is the second reason the ceilings
matter.

**Two balance bugs found by modelling it, both severe:**
1. **Ceilings were anchored to the filler band** (`START_POTENTIAL_CEILING` 58, headroom 12), which
   gave a new character 9-18 points of room on every skill. One season of points exhausted it, and
   his ceiling was *lower than the AI free agent next to him*. Now headroom 34, affinity swing 22,
   ceiling 95: a projection for a fourteen year old, not a current stat, with the quiz deciding
   **where** it is high rather than whether it is high at all.
2. **The six ratings with no potential were uncapped** - Quickness, Strength, Jumping, Stamina,
   3pUsage, Fouling - so every surplus point after the skilled twelve topped out flowed into them
   forever (Quickness 77 and climbing by college). They now take an **athletic ceiling** from the
   body the quiz described: explosiveness for quickness and jumping, frame for strength, motor for
   stamina. See `athleticCeiling()`.

## Declaring early, and the conversion between levels (2026-09-17)
A promotion does not carry ratings across at face value. The same skill is worth less against
bigger, older, better opposition, and an FBPB3 rating is relative to the league a player is in.

| Move | Carries |
|---|---|
| Prep -> College | 97% |
| College -> Pro, eligibility used (4 years) | 94% |
| College -> Pro, 1 year early | 87% |
| College -> Pro, 2 years early | 80% |
| College -> Pro, 3 years early (declare as a freshman) | 73% |

**Potentials are never converted.** The ceiling is who he can still become, and leaving early must
not close it: the points are earnable again, the years are not. Ratings at or below 8 are left alone,
because a percentage of nothing is nothing.

**Balanced by modelling the trade, not by feel.** At the first numbers I tried (4% per year early)
declaring as a freshman was strictly best by about 34 points over a career - that is not a choice,
it is an answer. Two changes fixed it: the penalty went to 7% per year, and a college season that is
seen through pays a **12-point development bonus** on top of the usual offseason lump. Staying pays
in points, leaving pays in time. The four options now finish within about 12 points of each other
over a 300-point career, and the two extremes beat dithering in the middle, which is the right shape.

`declareWarning()` in `rules.js` mirrors the constants so the site can say what declaring costs
*before* the confirm dialog. Those two copies have to move together.

## The career thread between levels (2026-09-17)
Each league's generated site knows a player only inside that league, so the thread between Prep,
College and Pro is ours to keep. The commissioner writes three things on every placement:

- `level_history` - one row per level: team, the FBPB3 player id at that level, the seasons it
  spanned, how it started and how it ended ("created", "aged out of prep", "drafted #1 by GOU").
  The previous level's row is closed when the next one opens.
- `league_player_ids` - `{"prep": 53, "college": 53}`, which is what a direct link to
  `leagues/<level>/players/player<id>.htm` needs.
- `draft_round`, `draft_pick`, `draft_season` on a drafted character.

**A new character column takes three edits, not one:** the column in `schema.sql`, its name in that
file's `grant select (...)` list, and its name in `CHARACTER_COLUMNS` in `site/js/supabase.js`.
`supabase.js` never does `select('*')`, so a column missing from that list is simply `undefined` in
the browser, and a column missing from the grant errors the whole query and blanks the page.

**Orphaned reserve slots.** Deleting a character (or restoring the store from an older backup)
leaves his renamed row stranded in the save: the manifest name it used to answer to is missing and
nobody claims it, so that slot can never be handed to anybody again. `protect_rosters.py` now
detects them - a player the manifest does not name whose birth year is inside our band, since the
game's own free agents are all adults - and gives the row its manifest identity back.

## The AI signs free agents during every sim
Measured after the first three-league Sim Week (7 days each): college rosters came back at 19-20 and
pro at 17-20, with **73 and 79** of the game's own players signed onto them. Prep was untouched that
run, but has been hit before. This is FBPB3's AI doing its job - the roster limit is 15 *active*, and
a team may carry more with the extras inactive.

`protect_rosters.py` runs at the top of every Sim Week and releases them, so the system is
self-healing across runs. What it cannot do is repair *between* the sim and the export, because the
game holds the save open for all three leagues in one session: the pages published by a run therefore
show that run's AI signings, and the next run cleans them up. If that ever matters more than the
extra game time, the fix is a second pass - exit, repair, re-export - not a codec write while FBPB3
has the file open.

## The depth-chart matcher has to tolerate a stale id
`teams()` located the depth-chart region by demanding that every id in every block belong to the
expected team. On a save that had actually been played, **one** stale id - a player released since
the chart was last rebuilt - disqualified the entire region and `teams()` raised
"0 candidate starts", which broke `verify_save`, `release` and `sign` on any aged save.

It now scores candidates instead: an id belonging to another team is still fatal, an id belonging to
nobody is not, and the best offset above 0.95 wins. The search window also went from 64 KB to 256 KB
from the end, because game history pushes the region further forward as a season is played.


## Playing time, and why a new character has to be mid-pack (2026-09-18)
**The Inactive flag is 0 for a player the game dresses and -1 for one it benches.** The note in the
record layout had it backwards. Measured against FBPB3's own player pages: 97 players the game calls
Active all carry 0, and 23 it calls Inactive all carry -1.

Knowing that exposed the thing that actually mattered. Every created character was **benched, with
zero games**, because a fresh 14-year-old averaged **16.9** against a prep filler median of **24.2**
- better than only 21% of the league he was joining. The coach plays the better fillers, so a new
player would never appear in a box score, and the entire premise fails at the first sim.

The filler population is frozen, so the fix is the starting sheet: the position templates were
lifted about 45% and `START_RATING_CEILING` went 30 -> 44. A median new character now averages 23.5
against the filler median of 24.2 - **44th percentile, ranging 27th to 71st**. He gets minutes
without starting, which is what a fourteen year old should be. The progression model still lands
(prep exit above every filler, college exit inside the pro band).

Re-measure both of these together after any change to either side: `node tools/progression_model.mjs`
for the arc, and compare a batch of `deriveCharacter` sheets against the league's own averages for
the percentile. One without the other is how this was missed.

### Getting a character onto the floor (settled 2026-09-18)
Three things have to be true or a created character never plays, and the first two are not enough
on their own:

1. **He has to be dressed and on the depth chart.** A reserve slot is built unused - floor ratings
   and no depth-chart minutes - so a character taking one over inherits that emptiness. `LeagueDat.
   dress()` puts him in the lineup and gives him half the least-used player's slots at his position
   (half, not all: earning a rotation spot is the story). `stamp_character` calls it, and Sim Week
   re-asserts it every week, because the AI coach rewrites depth charts constantly.
2. **He must not be the worst man on the roster.** This is the one that actually decides it. The AI
   benches from the bottom, and writing the depth chart before a sim does not survive the sim if the
   coach disagrees. See the percentile note above: a median new character now sits 44th percentile
   of prep instead of 21st.
3. **He has to be in a league he belongs in.** A fourteen year old stamped into the pro league is
   benched no matter what, and correctly so.

Proven end to end 2026-09-18: Gouda Kid, created through the real quiz rules, 6th of 15 on the
Saskatoon Berries, played 18 minutes with 5 rebounds and 0 points in his first simmed week.
