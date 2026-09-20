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
- 2026-09-18 **Stamina has no potential.** `POT_BY_RATING` covers the twelve skill ratings only; there is no
  PotStamina in the save, so Stamina (like Quickness, Jumping, Strength, 3pUsage and Fouling) can simply be set and
  it stays set. This is the one rating a floor works on without raising a ceiling first. `tools/raise_stamina.py`.
- 2026-09-18 **The quiz produces badly conditioned characters.** Measured against CV_Prep's own 240 rostered
  players: median Stamina 65, lower quartile 52, one man in 240 below 30. All seven characters sat at 19-34, i.e.
  the bottom two percent of the league. The commissioner's floor is **70** (the 59th percentile, deliberately above
  the league median, not merely out of the cellar); 50 would have been the conservative "fix the outlier" choice at
  the 18th. Whether this affects how often the AI coach benches them is UNKNOWN and should not be asserted: the
  least-played character has the group's lowest Stamina but its second-highest minutes per game, which is the
  opposite of what a conditioning limit looks like. Run `tools/raise_stamina.py` after each new character.
- 2026-09-18 **Stamina drifts UP, so a floor holds.** Measured on the 329 players present in both fixtures, i.e.
  across two simmed seasons: mean +3.3, 274 rose, 38 fell, 17 unchanged. Among the 114 who started above 65 - the
  band a floor of 70 puts the characters in - the mean is still +2.5 and only 14 fell, by 4-6 points. So the game
  does not claw a raised Stamina back the way it crushes a rating to its potential, and the floor does not need
  re-applying on a schedule; only new characters need it.
- 2026-09-19 **End-of-season bonus is league-relative on purpose.** The first draft paid a fixed rate per
  counting stat (a point per 50 PTS, 25 REB...). Measured on real exports that inflates with level, because
  totals grow with both talent AND season length: a full season paid the best prep player 22 and the best pro
  115 for the same achievement. Both halves are now measured against the league's own season - top 25 by total
  (max +2) and multiples of the 5th-best total (max +2) - which pays #1 -> 4, #10 -> 3, #25 -> 2, median -> 0 in
  every league. `commissioner/seasonbonus.py`; every number is a settings key.
- 2026-09-19 **Bonus components: the two AWARD ones are off, the two TEAM ones pay 2.** Nate first removed the
  playoff bonus ("everyone makes it") along with player of the week and the season awards, then put the playoff
  one back at 2 and dropped the title from 3 to 2. So `bonus_playoffs` 2, `bonus_title` 2, `bonus_potw` 0,
  `bonus_season_award` 0, `bonus_potm` 2. The premise for the removal was not right - a season-end rehearsal on a copy of CV_Prep
  confirmed the bracket takes the top 4 of each of the 2 conferences, 8 of 16
  in prep and college and 8 of 20 in the pros - and at 3 + 3 a title was worth six points, which crowded out
  everything a player does himself; 2 + 2 leaves a good individual season competitive. Off components are zeroed
  rather than deleted: a zero-point row is dropped before anything is paid, so it costs a dictionary lookup,
  keeps its parser under test, and comes back from the settings table without a deploy - which is exactly what
  happened here, twice, within an hour. Now pays the seven prep characters Chris 2, Zach 2, Johnny 3, Dodger 2,
  Gravy 2, Liam 3, Tim 0.
- 2026-09-19 **A `bonus_*` / `stat_bonus_*` settings row should not exist unless somebody is deliberately tuning
  that number live, and says so.** Settings override `seasonbonus.DEFAULTS`, so a row left behind after a
  rebalance silently blocks the next change to the code: `bonus_playoffs` was set to 0 in settings when the
  component was switched off, and when it came back at 2 in DEFAULTS an hour later the live universe would have
  gone on paying 0, with the code, the tests and CONVENTIONS all saying 2. The fix was to DELETE the rows rather
  than add a second override, which is the right instinct generally - prefer no row to a row that happens to
  agree, because only one of those two can go stale. There are currently no bonus rows at all.
- 2026-09-19 **The playoff bonus and the title were read off the wrong pages, both silently wrong.** Found by
  simming a throwaway copy of CV_Prep through its own season end. `playoffstandings.htm`'s asterisk marks
  DIVISION WINNERS, not qualifiers - it found 4 of the 8 teams in the bracket, so half of them lost the bonus
  every season. `champs.htm` prints champion AND beaten opponent on one row, so "the first team named on the
  page" returned the LOSER; it keeps a row per season, so from year two every past finalist matches too; and it
  is header-only for some window after the final (empty at 5/1, filled by 6/21). `playoffs.htm` has none of
  those problems: complete the moment the final ends, lists every qualifier including first-round losers, and
  states who won each series. Parse it by pairing the "#seed Team wins" entries CONSECUTIVELY - document order
  is not round order (the final was the 4th of 7 pairs) and it is a rowspan table, so column position means
  nothing either. The champion is whoever won the most series.
- 2026-09-19 **ENTITIES ARE NOT WHITESPACE, and `_text` leaves them alone.** It strips tags and collapses
  spaces; `&nbsp;` and `&#160;` survive. `playoffs.htm` separates every token with the NUMERIC `&#160;` - 49 of
  them, and not one `&nbsp;` - so a pattern written against "#1 Tulips 0" matches nothing. Normalise entities
  LOCALLY where a reader needs it: doing it inside `_text` empties `standings_teams` and every `league_stats`
  player, because the standings and award patterns anchor on a literal `&nbsp;`. Measured on the live saves
  before it was ruled out.
- 2026-09-19 **A fixture flattened for human eyes is a different page from the one in production.** The bracket
  reader was written from page text that had had its entities turned into spaces for legibility, and the test
  fixture inherited the same tidy spacing - so the test passed while the reader returned nothing on every real
  export. This is the fourth parser this week broken by the wrong sample, after ASCII-only names dropped eleven
  players, a hardcoded date format nearly shipped a dead guard, and a flat page order decided a cross-conference
  tie. **Build fixtures from real bytes, or reproduce the exact separators the game writes.**
- 2026-09-19 **A missing page is not an empty answer.** `playoffs.htm` does not exist before the postseason, so
  the bracket readers return None rather than an empty set. An empty set is indistinguishable from "nobody
  qualified" and would pay the playoff bonus to nobody at the one moment it is owed.
- 2026-09-19 **The elite-line half pays nothing yet, by design rather than by fault.** It needs the league's
  5th-best total in a category: prep is PTS 201 / REB 137 / AST 34 / STL 17 / BLK 6, and the characters' bests
  are 108 / 77 / 26 / 10 / 4. Unlike the top-10 rule that was rejected for being unreachable, this is close -
  Zach is at 76% of the assists line - so it is dormant, not dead. `stat_bonus_elite_rank` moves the yardstick
  if it needs to pay sooner.
- 2026-09-19 **Four traps in FBPB3's exported HTML**, all found by running the parser against the live prep
  export and all silent: an undefeated team's percentage reads `1.000` not `.652` (the 24-0 Tulips vanished from
  the standings, which dropped their players from the ranking pool AND denied them a playoff bonus); the Season
  Totals header repeats STL, so BLK is the 17th number after the season year; that header contains `3PM`/`3PA`,
  so "find the numbers" reads every column one place out; and draft-pool players get pages with season lines
  earned elsewhere (one showed 247 assists without playing a minute in the league). The standings team list is
  the draft-pool filter. Award rows have nothing between the player and his team but a space, so a name matched
  as "capitalised words" yields "Sid McFate Tulips", which matches nobody and pays nothing.
- 2026-09-18 **A run pays `round(days / 7)` points.** 28 days pays 4 and 35 pays 5, for about three minutes
  more. **Superseded 2026-09-19: do not sim 35.** The regular season ends before that from where the universe
  now stands, and `run_sim` refuses any run that would cross it - see the next entry.
- 2026-09-19 **A sim must not cross the end of the regular season, and now cannot.** `sim_days` clicks SIM DAY
  blind and `offseason.py` never drives FBPB3's own playoffs or rollover, so what the game does on the day after
  the last one is unknown - a modal that eats later clicks, playoff games, its own aging and re-signing, any of
  which is the game taking over a rollover we own. `run_sim` counts the scheduled dates after the last played one
  and refuses to exceed them; `allow_season_end=True` lifts it for the code that eventually owns that path.
  **Count date POSITIONS, never parsed dates.** FBPB3 is VB6 and renders the Windows short date, so the format
  is a property of the machine: this desktop exports ISO (2030-10-15), SERVERPC exports 10/20/2026. A guard
  written against one of them is silently inert on the other, and the first version of this was.
- 2026-09-18 **Sim cost is ~480 s fixed + ~26 s per day** across three leagues; only the SIM DAY clicking scales,
  and it is linear at ~8.7 s per day per league. Measured: 4x7 days = 2663.1 s, 1x28 days = 1210.4 s, so one
  monthly run is **2.2x faster** than four weekly ones for the same basketball. `docs/SIM_SPEED.md`.
- 2026-09-18 **Batching does not starve fringe characters of minutes.** The worry was that `_dress_characters`
  runs once per run, so a 28-day run lets the coach bench somebody for a month. Measured the opposite: the 28-day
  run needed 2 re-dresses once, against 3-4 every week across the four weekly runs, and Tim Turner gained 5 games
  and 79 minutes with zero re-dresses. What fixed him was moving him to a team he fits (0 games in 16 on JER,
  a regular on MTL) - re-dressing is a safety net, not the thing that earns minutes.

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
Model it, do not guess: `node tools/progression_model.mjs` prints what a career of points buys.
Re-run it after touching the cost curve, points per week, the offseason lump sum or the potential
constants.

A season is **26 in-game weeks** (the prep calendar ran 2030-10-15 to 2031-04-17, 184 days, when
the universe started in 2030; it now starts in 2026 and the same 184 days apply).

**Income scales with level (2026-09-19): prep 1, college 2, pro 3 points a week.** The cost curve
charges by rating - 1 a step under 50, 2 from 50-69, 3 from 70-84, 5 from 85 up - and characters
sit in those bands as they climb, so a flat rate meant every promotion quietly halved what a season
was worth. With the flat 15-point offseason lump (which does NOT scale) a season is **41 points in
prep, 67 in college, 93 in the pros**. The rule lives in `commissioner/points.py` and nowhere else;
`grant_week_points` takes the resolved rate as an argument rather than recomputing it, because
unlike the price curve nobody but the commissioner can grant income.

Measured arc, spending evenly across six core skills:

| | points so far | core average | population |
|---|---|---|---|
| End of season 1 | 41 | 33 | prep fillers 8-38 |
| End of prep (season 4) | 164 | 52 | above every prep filler |
| End of college (season 8) | 432 | 64 | pro fillers 28-62 |

So four years to become the best kid in prep, four more to arrive pro-ready.

**The ceilings bind from season 7 (2026-09-19).** With level-scaled income this character has every
core skill at its ceiling by mid-college, and the remaining six seasons earn 372 points the model
cannot spend at all - because it only ever buys RATINGS. In a real career that surplus goes on
POTENTIALS, which cost double a rating step at the same value and raise the ceiling that is doing
the binding. So the later rows above are a FLOOR on what the scaling is worth, not a ceiling - but
it does mean level-scaled income only pays off for somebody who buys ceilings, and a player who
spends only on ratings will simply accumulate points he cannot use. FBPB3's own progression runs on top of all of
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

## Box scores: SOLVED (2026-09-19)

**"Output old boxes" in HTML Output is a DATE dropdown, not a Yes/No.** Set it to a date and
FBPB3 writes real box scores into `html/boxes/` - quarter-by-quarter scores and full player lines
(MIN, FGM-A, 3PM-A, FTM-A, OFF, REB, AST, STL, BLK, TO, PF, +/-, PTS), ~21 KB a page. Measured on
a throwaway copy: 55 pages written, and `restyle` then keeps all 55 schedule links as links with
zero falling back to plain text, because it already checks whether the file exists.

### Why every previous attempt proved nothing

`html_output` mapped its flags through `{True: "Yes", False: "No"}`, and **"Yes" is not one of
that control's options** - they are `No`, `After 2030-11-14`, `After 2030-11-13`, ... So the
experiment that was supposed to test the setting could not set it, and reported a negative
result. The driver takes `old_boxes=True` now, meaning the oldest date offered, read off the
control rather than constructed from a calendar.

### What the four dropdowns actually offer, read off the real dialog

| Control | Options |
|---|---|
| Output player pages | `No`, `Yes`, `Human Only` |
| Output coach pages | `No`, `Yes`, `Human Only` |
| **Output old boxes** | `No`, then **one entry per day**, ~31 of them |
| Include box links | `No`, `Yes` |

The earlier note here put the three-value list on the *box* controls. It belongs to the player and
coach ones; "Include box links" has only two, and "Output old boxes" has thirty-two.

### THE CONSTRAINT THAT MATTERS: the window is rolling

The date list reaches about **31 days back from the save's own current date**, and there is no
"everything ever" option. Box scores must therefore be exported **while the games are still inside
that window**. A season's early games are gone from it long before the season ends, and after a
rollover the previous season is far outside it.

So: `html_output(..., old_boxes=True)` on every publish captures the recent stretch and the
`boxes/` folder accumulates on the site, since `restyle` copies what it finds. Skipping publishes
loses the games that fall out of the window in between - which is the same shape as the games.json
problem, and the same answer: what is not archived while it exists cannot be recovered later.

### What was wrong before, and why

This file previously said the game stores each game as a `.box` file under the save, that HTML Output
converts those into `html/boxes/`, that no `box\` folder is ever created, and therefore "the
games are being simmed without being recorded". **All of that is wrong.** No `box\` folder was
created during this test either, and 55 box scores were written anyway: FBPB3 builds them from the
game history already in `league.dat`. The `.box` wildcard string in the executable is not the
mechanism the HTML export uses.

Two hypotheses were also carried here for days and both are dead:
- **Human-coached teams**: the Stabbyverse's `humancoaches.htm` is an empty header row, in the
  2026-09-17 capture and live, while all 273 of their games have box scores. No human coaches
  needed. (This file had argued they *did* have human coaches because the FILE EXISTS in the
  capture - every export writes that page whether anybody coaches or not. A claim read off a
  filename instead of the file.)
- **A different sim path**: not needed; Hot Seat SIM DAY is fine.

`Save all play by play logs` (League Options -> DATA SETTINGS) is a **separate** feature: turning
it on wrote `pbp/30-1.txt`, a 99 KB text play-by-play, for a day that had previously written
nothing. It is not required for box scores. Worth knowing it exists and that it is off by default.

That whole panel - Team record list size, Save retired coaches, Save retired players, Autosave,
Save all play by play logs, Pbp style - was **missing from this file's list of League Options**,
which is exactly the incompleteness the list warned about itself.

## A publish is not visible everywhere at once, and a verifier can pass against the old copy

GitHub Pages serves `Cache-Control: max-age=600`, and a page's URL never changes when its
contents do. Measured 2026-09-19: `Age: 113` from `cache-yyc1430031-YYC` while the bare URL
already held the new playoff bracket. Nate published the bracket, pressed Ctrl+F5, and still read
the old page - a hard refresh cannot help when the stale copy is at the edge rather than in the
browser.

`restyle` now stamps every link it writes with the publish time (`standings.htm?v=20260919185855`),
including FBPB3's own roster, team and player links, and the bar shows `published HH:MM`. A query
string is part of the cache key on Pages, so a CLICK is always fresh. **A typed or bookmarked
address cannot be versioned** and stays cacheable for ten minutes; that is the floor, which is
why the bar states the time rather than pretending otherwise.

**Different files propagate at different speeds.** After the 2027 rollover publish the CDN served
the old *pages* for about four minutes while `games.json` was already new - Season 2026 pages
beside Season 2027 data, from one deploy. Nothing is wrong when that is seen; it resolves itself.

### Verifying a publish: the trap

A check that passes against the copy it was meant to replace has verified nothing.

That happened here. The post-rollover publish was verified by polling `games.json` until it showed
161 game lines - and **the previous publish's file also had 161 lines**, written before `merge()`
existed and therefore with no season on any line. The verifier reported success against the old
file, inside the very propagation window being discussed, and the "all lines unstamped" reading it
produced was a fact about a file that had already been superseded.

So: **never verify a publish with a number that did not change.** Use something only the new copy
can have -

- `generated`, which every publish rewrites;
- a key the old file does not have at all (`seasons` here);
- the `?v=` stamp in a page's own links, or the `published HH:MM` in its bar.

Counting rows is a content check, not a freshness check, and the two are easy to confuse precisely
when the content is supposed to be unchanged - which, for an archive whose whole purpose is that
nothing is lost, is every single time.

## Weight is chosen, not derived (2026-09-20)

`weight_lbs` is a column on `public.characters` and the number the commissioner writes into
league.dat's int16 `Weight` when it stamps a character onto a reserve slot.

It used to be derived from height and build wherever anybody needed it - `buildWeight()` in
`site/js/rules.js`, and nothing in Python at all. That stopped being true the day the create page
grew a weight slider: the review card promised a number nobody stored and the game was never
told. Deriving it in three places was fine only while there was one right answer.

- **The slider's range is `buildWeight(height, build) ± WEIGHT_SPREAD` (25 lbs).** `validateBuild()`
  enforces exactly that range, because whatever the form accepts goes into the save. SQL cannot
  re-derive it (BUILDS lives in JS), so the column's `between 50 and 400` is a plausibility net,
  not the guard - the same posture `growth_bias` has.
- **`buildWeight()` in rules.js and `build_weight()` in commissioner/growth.py are the same
  function**, pinned by `tests/test_rules.py` over 35 height-and-build cases. The Python copy is
  what runs when a character has no weight of his own: a commissioner-created one, or anybody made
  before the column existed.
- **Weight follows him.** `stamp_character` writes what he chose; from his next offseason on,
  `offseason.apply_growth` writes `Height` and `Weight` together, in the same `expect` entry so
  `ch.commit` verifies both or refuses the offseason.

### The curve after fourteen (decided 2026-09-20, off the saves themselves)

```
frame_weight(h, age) = 100 + (h - 60) * 5.2 - max(0, 18 - age) * 4
offset               = weight_lbs - frame_weight(height_at_14, 14)
weight_now           = frame_weight(height_in_the_save_now, age) + offset
```

`buildWeight()` was the obvious candidate and it is the wrong one: it describes a fourteen-year-old
and has no age in it. Measured against 955 real players in the saves it is 15-25 lbs light at every
adult height band, while the curve above lands within 3-9:

| | real | buildWeight | frame |
|---|---|---|---|
| pro, 6'2"-6'5" (mean age 26) | 186 | 170 | 183 |
| pro, 6'6"-6'9" | 208 | 183 | 199 |
| pro, 6'10"+ | 230 | 206 | 220 |

So a character grows on the same curve the league's own population was built with - it is the
deterministic core of `universe/generate.weight_for`, duplicated into `growth.py` without the gauss
noise or its `max(120)` floor, which are population-generation details and would flatten every small
prep kid onto one number.

Three properties worth keeping in mind when touching it:

- **The level cancels; only the shape matters.** A constant added to `frame_weight` disappears from
  `weight_at`, because the offset subtracts the same curve at fourteen that it adds back later. What
  is load-bearing is the 5.2 lbs an inch (the saves say 5.4 across 516 pro players) and the 4 lbs a
  year to eighteen.
- **His slider choice is worth the same pounds for life.** Two characters 25 lbs apart at fourteen
  are still 25 lbs apart at twenty-five.
- **Weight moves even when height does not.** A sixteen-year-old who gains no inches still fills
  out, so `apply_growth` recomputes it for everybody rather than only for whoever grew.
  `tests/test_growth_weight.py` runs both paths against a copy of a real save.
- **An old wrong weight is walked to the truth, not snapped to it.** `growth.weight_step` applies
  this year's real change in full and then closes at most `WEIGHT_CATCHUP_PER_YEAR` (12 lbs) of
  whatever gap is left between the file and the model. Set that constant to `None` to correct
  everybody in one write.

  It exists for the seven characters who predate the column: they are carrying the weight of the
  dormant filler whose slot they took, and two of those sat on the generator's 120 lb floor, so an
  instant correction moves a 6'0" fourteen-year-old 50 lbs in one summer. Anybody created since is
  stamped with his own weight and therefore has no gap at all - the catch-up is dormant for him.

  The cost, and it is the only one: while a character is catching up, his career page shows the
  model's number and his league page shows the file's, and for those few years they disagree. The
  page cannot know what the save holds - the site has never been able to see a league.dat.
- Adding a character column means adding its name to **four** lists: the create table, the
  `grant select`, the `grant insert`, and `CHARACTER_COLUMNS` in *both* `site/js/supabase.js` and
  `commissioner/store.py`. Only the first two fail loudly. `tests/test_column_grants.py` reads all
  of them out of the source and compares them.

## The offseason puts the saves back when it fails (2026-09-20)

All three saves are copied before the offseason's FIRST write - not once per phase - and if
anything raises, every one of them is copied back. `back_up_every_save` and `restore_saves` in
`commissioner/offseason.py`.

Per-phase backups were too late to be a safety net: retirements run before growth and `refill`
writes to a save, so the league a retirement had already touched had no copy yet.

**Why a restore and not just a backup.** Growth is cumulative - `apply_growth` reads the Height
out of the save and adds this year's inches to it - and `last_offseason` is only written at the
very end of a successful run. So a run that stopped halfway left prep and college grown and pro
not, with nothing recorded to say it had happened, and the obvious recovery (press it again)
grew those same characters a second time. Nothing anywhere would have shown it afterwards: the
heights are plausible and the model does not know what it wrote last year.

What the restore does NOT undo is the store. A retirement or a banked college year that already
landed is a row somebody may have read, so the failure message says so and tells whoever is
looking to check the panel before running it again. The saves are the half nobody can fix by
hand; the store is the half they can.

`python tools/offseason_rehearsal.py` proves all of it against copies of real saves, including
that a rerun after a restore does not double-grow anybody.

## A reserve slot remembers the body it had (2026-09-20)

`Slot` carries `height` and `weight`; `stamp_character` reads them off the row just before it
overwrites it, `as_json()` puts them in `claimed_slot`, and `reset_reserve` writes them back when
the slot is handed over. The manifest never recorded either, which is why they travel on the
claim instead.

Ratings were always scrubbed on the way out and the body never was, so a recycled slot kept the
departed character's height - and, now that weight follows growth, his weight too. A slot that a
7'2" pro vacated came back as a dormant fourteen-year-old filler listed at 7'2" and 230 lbs.

A slot claimed before this existed has no body recorded and is left alone: the number is gone and
a guess would be worse. `tools/protect_rosters.py` passes a bare manifest row for the same reason
- it is recovering a slot whose character is unknown, so it genuinely does not know the body.
