# Guided New Game - the one manual step, done three times

Run this once per league. Each league gets its **own save**, so the whole checklist happens three
times: Prep, then College, then Pro. Everything after this is automated.

> **Teams come from `universe/teams.csv`.** Edit that file first if you want your own cities and
> nicknames - it is the source of truth and the generator reads it on every run. See
> "Making it your own" at the bottom. Do it before the New Games; afterwards, changing teams means
> starting the saves over.

Then generate the files:

```
cd C:\claude\hoops-universe
python tools/generate_universe.py
```

That writes, into the FBPB3 documents folder:

| League | League file (LeagueFiles\) | Roster file (PlayerFiles\) | Save name |
|---|---|---|---|
| Prep | `CV-Prep.csv` | `CV-Prep-Rosters.csv` | `CV_Prep` |
| College | `CV-College.csv` | `CV-College-Rosters.csv` | `CV_College` |
| Pro | `CV-Pro.csv` | `CV-Pro-Rosters.csv` | `CV_Pro` |

and `universe/manifest.json` in the project, which records every generated player and which of them
are dormant **reserve slots** (48 in Prep, 48 in College, 60 in Pro - 3 per team). A reserve slot is
how a new character enters the universe: the codec renames and re-rates it in place.

**Only one copy of FBPB3 may be running.** Two instances silently break the automation later.

---

## For each league

### 1. New Game -> Game Settings

| Setting | Value | Why |
|---|---|---|
| Save Name | `CV_Prep` / `CV_College` / `CV_Pro` | the driver loads saves by name |
| First Season | `2026` | must match `START_YEAR` in `commissioner/universe/config.py` |
| League Password | *blank* | a password would block automated simming |
| Attribute Style | **1-100** | makes the MDB export numeric instead of letter grades |
| Coaching | On | teams need coaches to set the depth charts the codec edits |
| Scouting | **Off** | with scouting on, every rating you read is fuzzed |
| Finances | **Off** | **required** - with finances on, a codec-signed player has no contract and is released on load |
| Historical Modifiers | Off | not a historical league |
| Autosave | **Never** | the app decides when to save |

### 2. Available Leagues

Tick **only** the one custom league for this save (`Cheezeyverse Prep`, etc). Leave every stock and
historical league unticked. Leagues can only be added at New Game, so getting this wrong means
starting the save over.

### 3. League Setup

Most of this comes from the league CSV already, so it should be pre-filled. Confirm:

| Setting | Prep | College | Pro |
|---|---|---|---|
| League Name | Cheezeyverse Prep | Cheezeyverse College | Cheezeyverse |
| Abbreviation | CVP | CVC | CV |
| Prestige | lowest | low-middle | highest |
| Host Country | USA | USA | USA |
| Team Locations | National | National | National |
| Starting Stage | **Preseason** | Preseason | Preseason |
| League Type | **Standalone** | Standalone | Standalone |
| Sub League | none | none | none |

> **Check the Prestige dropdown and tell me what the options actually are.** The config assumes
> 1 = highest (the stock NBA league file ships with Prestige 1) and sets Prep 5 / College 3 / Pro 1.
> If the scale runs the other way, that one number in `config.py` flips and the files regenerate in
> a second.
>
> Standalone, not Tiered, is deliberate: tiered leagues make FBPB3 handle promotion between levels,
> but these are three separate saves and the app moves players itself.

### 4. Player Source

| Setting | Value |
|---|---|
| Initial Player Source | **Player File** -> the matching `CV-*-Rosters` file |
| Yearly Player Source | Fictional |

Initial rosters must come from the player file, or the game generates its own players and the
reserve slots never exist. Yearly stays Fictional for now; per-league draft files replace it later
so we control each incoming class.

### 5. Create the league, then check League Options

Once the league is created: **Tools -> League Options** and confirm Finances shows Off and Attribute
Style shows 0-100. **Leave CPU offers trades on** - the AI shuffling players between teams is
flavour, and a traded character keeps every rating, since a trade is only a team change.

### 6. Save and exit

Save the game, then exit. Repeat for the next league.

---

## When all three exist

Tell me, and I will:

1. Verify each save with the codec - 240 / 240 / 300 players parsed, every team at 15, every reserve
   slot in `manifest.json` findable by name + DOB.
2. Back all three up to `backups/` before anything touches them.
3. Wire up the Sim Week pipeline across the three saves.

## Known cosmetic effect of Finances Off

The team pages FBPB3 generates carry a salary table and a finances block (the reference SV Prep site
shows both). With finances off those sections come out empty or zeroed. Expected, not a bug.

---

## Making it your own - `universe/teams.csv`

Every team in all three leagues lives in one file: `universe/teams.csv` in the project. Edit it,
re-run `python tools/generate_universe.py`, and the league files and rosters rebuild from what you
wrote.

| Column | What it is |
|---|---|
| `league` | `prep`, `college` or `pro` |
| `division` | 1-4, matching the division names in `config.py` (Atlantic, Southern, Central, Pacific) |
| `city` | the team's city as it appears in standings |
| `nickname` | the team name, e.g. Ironworks. Leave blank to use the city alone |
| `abbrev` | 2-4 letters, **unique within its league** - this is what links a player to his team |
| `color` | hex, e.g. `#1D3557`. Drives the colour of that team's generated page |
| `arena_city` / `state` | where the arena physically is (Brooklyn plays in New York, NY) |
| `arena` | arena name |
| `capacity` | seats |

Rules the generator enforces, so a mistake stops the run instead of building a broken league:

- Team count must divide evenly into 4 divisions - **16 prep, 16 college, 20 pro** as it stands.
- Abbreviations must be unique within a league.
- Division must be 1-4.

Team counts are yours to change: 24 prep teams instead of 16 would take the prep ceiling from 48
concurrent characters to 72. Tell me the number and I will move the divisions to match.
