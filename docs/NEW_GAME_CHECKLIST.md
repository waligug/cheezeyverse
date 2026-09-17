# Guided New Game — the one manual step, done three times

Run this once per league. Each league gets its **own save**, so you do the whole checklist three
times: Prep, then College, then Pro. Everything after this is automated.

Before you start, generate the files:

```
cd C:\claude\hoops-universe
python tools/generate_universe.py
```

That writes, into the FBPB3 documents folder:

| League | League file (LeagueFiles\) | Roster file (PlayerFiles\) | Save name |
|---|---|---|---|
| Prep | `HU-Prep.csv` | `HU-Prep-Rosters.csv` | `HU_Prep` |
| College | `HU-College.csv` | `HU-College-Rosters.csv` | `HU_College` |
| Pro | `HU-Pro.csv` | `HU-Pro-Rosters.csv` | `HU_Pro` |

and `universe/manifest.json` in the project, which records every generated player and which of them
are dormant **reserve slots** (48 in Prep, 48 in College, 60 in Pro — 3 per team). A reserve slot is
how a new character enters the universe: the codec renames and re-rates it in place.

**Only one copy of FBPB3 may be running.** Two instances silently break the automation later.

---

## For each league

### 1. New Game → Game Settings

| Setting | Value | Why |
|---|---|---|
| Save Name | `HU_Prep` / `HU_College` / `HU_Pro` | the driver loads saves by name |
| First Season | `2030` | must match `START_YEAR` in `commissioner/universe/config.py` |
| League Password | *blank* | a password would block automated simming |
| Attribute Style | **1-100** | makes the MDB export numeric instead of letter grades |
| Coaching | On | teams need coaches to set depth charts the codec can edit |
| Scouting | **Off** | with scouting on, every rating you read is fuzzed |
| Finances | **Off** | **required** — with finances on, a codec-signed player has no contract and is released on load |
| Historical Modifiers | Off | not a historical league |
| Autosave | **Never** | the app decides when to save |

### 2. Available Leagues

Tick **only** the one custom league for this save (`Hoops Universe Prep`, etc). Leave every stock
and historical league unticked. Leagues can only be added at New Game, so getting this wrong means
starting the save over.

### 3. League Setup

Most of this comes from the league CSV already, so it should be pre-filled. Confirm:

| Setting | Prep | College | Pro |
|---|---|---|---|
| League Name | Hoops Universe Prep | Hoops Universe College | Hoops Universe Pro |
| Abbreviation | HUP | HUC | HUX |
| Prestige | lowest | low-middle | highest |
| Host Country | USA | USA | USA |
| Team Locations | National | National | National |
| Starting Stage | **Preseason** | Preseason | Preseason |
| League Type | **Standalone** | Standalone | Standalone |
| Sub League | none | none | none |

> **Check the Prestige dropdown and tell me what the options actually are.** The config currently
> assumes 1 = highest (the stock NBA league file ships with Prestige 1) and sets Prep 5 / College 3 /
> Pro 1. If the scale runs the other way, that one number in `config.py` flips and the files regenerate
> in a second.
>
> Standalone, not Tiered, is deliberate: tiered leagues make FBPB3 handle promotion between levels,
> but these are three separate saves and the app moves players itself.

### 4. Player Source

| Setting | Value |
|---|---|
| Initial Player Source | **Player File** → `HU-Prep-Rosters` (the matching roster file) |
| Yearly Player Source | Fictional |

Initial rosters must come from the player file, or the game generates its own players and the
reserve slots never exist. Yearly stays Fictional for now; per-league draft files replace it later
so we control each incoming class.

### 5. Create the league, then set the last option

Once the league is created: **Tools → League Options** and set **CPU offers trades: No**. Everything
else was set at New Game; confirm Finances shows Off and Attribute Style shows 0-100.

### 6. Save and exit

Save the game, then exit. Repeat for the next league.

---

## When all three exist

Tell me, and I will:

1. Verify each save with the codec — 240 / 240 / 300 players parsed, every team at 15, every reserve
   slot in `manifest.json` findable by name + DOB.
2. Back all three up to `backups/` before anything touches them.
3. Wire up the Sim Week pipeline across the three saves.

## Known cosmetic effect of Finances Off

The team pages FBPB3 generates carry a salary table and a finances block (the reference SV Prep site
shows both). With finances off those sections come out empty or zeroed. That is expected, not a bug.
