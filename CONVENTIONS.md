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
| **R** | current-ratings block, first offset after E where int16 at R-52,R-50 = `1,1`, bytes R-48..R-1 are zero, and the 18 current + 12 potential values are all 0..100 (not all zero) |

Other fixed layout facts: the last injury string (current injury, may be empty) ends exactly at R-64.
Duplicate names exist (Tony Thompson ×2, Charles Taylor ×2); disambiguate by DOB.

| Field | Offset | Confidence |
|---|---|---|
| Position (1 C, 2 PF, 3 SF, 4 SG, 5 PG) | E+18 | 390/390 |
| Team id (MDB `Team.ID`; Draft = -2) | E+40 | 390/390 (current == contract team in fixture) |
| Experience | E+82 | 390/390 |
| Ratings ×18 | R+0..R+34 in order: Inside, JumpShot, FT, 3pUsage, 3pShot, Handling, Passing, Quickness, PostD, PerimD, Stealing, Blocking, OReb, DReb, Jumping, Strength, Stamina, Fouling | 390/390 |
| Potentials ×12 | R+168..R+190 in order: Inside, JumpShot, FT, 3pShot, Handling, Passing, OReb, DReb, PostD, PerimD, Stealing, Blocking | 390/390 |
| Happiness | R-62 | 386/386, not yet used |
| OverallRating / OverallPotential | R+276 / R+278 | **unverified** (mostly-zero values) |
| Ratings history snapshots | ~94-byte rows from R+192 onward | observed, read-only |
| Player id, uniform | a few bytes before S (S-24 / S-4 most common) | **not fixed**; needs more work |

Unknowns to resolve: team roster lists (team records appear to hold player-id arrays), DOB age floor,
where to park reserve slots.

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
