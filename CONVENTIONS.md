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
| Inactive (-1 = dressed out) | E+46 | 400/410 on the aged save; the game recomputes it on load (a roster over the limit forces the extra player inactive) |
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
