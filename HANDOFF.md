# Handoff to Codex — 2026-09-20 19:47

Nate is out of limit and has authorized a full takeover. I could not deliver this by message:
every Codex relay address fails with `ENOINBOX` (its own relay sessions are short-lived and are
gone by the time a reply is sent), and `ListAgents` shows no reachable peer. **This file is the
handoff.** The worktree is clean and I stop editing after committing it.

Sections are in the order Codex asked for. Where something does not apply it says **none**
rather than being omitted.

---

## (6) Live simulation, offseason, long test, or held lock — **NOTHING IS RUNNING**

Verified at 19:47:

| | |
|---|---|
| Panel | `busy=False`; last run `kind=offseason`, `running=False`, `status=ok` |
| FBPB3.exe | not running |
| `universe/run_in_progress.json` | absent (marker clean) |
| SAVE_LOCK | free |
| Panel PID | 22520, started 19:02:34 |
| Background tests of mine | none |

**The panel at PID 22520 has the ageout dry-run fix but NOT the offseason status card** — that
was committed after it started. A restart loads it.

The 2027→2028 offseason **completed at 19:39**, `status=ok`, 2095s. Store: `current_season=2028`,
`current_week=0`. All three saves: season 2028, day 15, 2028-10-31, in lockstep. Champs pages
carry 2027: prep **Tulips**, college **Mandibles**, pro **Swiss**.

---

## (5) Ownership release and permission

I release every file claim. All paths below are yours. Nate's words: *"give him access to
everything he will do the rest."* You may **edit, commit, push, publish, restart the panel, and
run `protect_rosters`**. I make no further edits after this commit.

Two caveats, because they were never mine to hand over: I cannot change permission settings,
`CLAUDE.md`, or config on your behalf; and Nate's standing rule stands — **do not start a Sim
Week or an offseason without his explicit OK.**

---

## (4) Uncommitted changes — **NONE**

`git status --porcelain` is empty. HEAD is pushed to `origin/master`.

**DISCARD: none.**

**PRESERVE — committed, but flagged because it is UNTESTED and has never run live:**

`commissioner/simstatus.py` + `commissioner/offseason.py` — the offseason Discord progress card
Nate asked for. `SimStatus` gained `kind`/`label`; `render()` gained `kind`/`label`; `STAGES`
gained `archive`/`grow`/`bonus`/`move`/`ageout`/`rollover`/`report`; `offseason.py` gained
`_say`/`_finish`/`_one_line` plus 8 `_say` call sites. It imports clean and I rendered both card
states by hand, but **there is no test and it has never executed in a real run.** Treat as
unproven.

---

## (1) + (2) + (3) Every known unresolved bug, with paths and evidence

### BUG 1 — SEVERE, I shipped it: the age-out is undone by FBPB3's own rollover

**Files:** `commissioner/offseason.py` — the ordering of `_run_offseason(...)` (~line 728)
against `seasonflow.rollover_saves(...)` (~line 734). The age-out block is inside
`_run_offseason` at ~lines 1085–1110. Module: `commissioner/ageout.py`.

**Trigger:** ran the real 2027→2028 offseason via the panel's "Start next season".

**Expected:** prep rosters read ages 14–18, college 19–22, 15 players per team.

**Actual**, read out of the live saves afterwards:

```
prep    ages {14:51, 16:82, 17:69, 18:40, 19:7, 20:9, 21:18, 22:23, 23:16, 24:4, 25:1}
        roster sizes {20: 16}
college ages {18:1, 19:55, 20:80, 21:94, 22:67, 23:22, 24:1}
        roster sizes {20: 16}

78 players aged 19+ are back on prep rosters; 0 of them still defanged.
Gregory Delima: released and set to rating 2 by the age-out
               -> rostered=True  team=18  age2028=19  topRating=5
Caron Gilstrap, Erman Beehler: gone from the file entirely (FBPB3 retired them itself)
```

**Run log proving the order:**

```
 391.8  prep: 51 aged out at 19+, 51 joined at 14
 736.6  college: 51 aged out at 23+, 51 joined at 19
 743.6  prep: advancing the game to season 2028      <- native rollover starts AFTER
```

**Cause:** the age-out releases 51 players into free agency and defangs them to rating 2 /
potential 5. FBPB3's native rollover — free agency, HIRE STAFF, TRAINING CAMPS — then signs them
straight back and grows them past the floor.

**Why the obvious fix is not sufficient on its own:** moving the call after `rollover_saves`
narrows the window but may not close it, because FBPB3 also signs free agents during preseason.
And `tools/protect_rosters.py` will not clean it up: it only releases players **not in the
manifest**, and these are manifest fillers; `_looks_like_ours()` additionally spares anyone whose
birth year is inside our band.

**Options I did not pursue:** run the age-out as the last step after the rollover and re-defang;
give `protect_rosters` an age rule; or delete the record outright instead of releasing it — the
codec cannot delete, so that needs the player-file import path in
`commissioner/universe/generate.py` (how the original 425 got in).

**Survived:** the 51 newcomers at 14 are on rosters.

**Where I stopped / unknowns:** the 20-man rosters are probably camp rosters — the 2026 notes say
preseason left them at 17–20 and a later sim trims them — but **I did not confirm that.** I also
did not check whether pro is affected the same way (pro has no age cap, so it should not be).

### BUG 2 — `output_mdb` budget is still below the real cost

**File:** `commissioner/driver/fbpb3.py`, `output_mdb()`, signature at ~line 824:
`def output_mdb(self, save_name, attempts=3, timeout=600):`

**Evidence:** pro's export began at elapsed 312.1s (18:03:42); the file was written at 18:13:59 —
**~617s against a 600s budget.** Attempt 1 timed out; the file appeared 17s later and attempt 2
immediately saw the fresh mtime, so it recovered.

**It recovered by luck, not design.** No message box happened to be open, so the
`if self._message_boxes(): self.dismiss_all()` guard did not fire and did not cancel the
in-flight export. With a dialog up it would have repeated this morning's failure.

**Expected:** the export is waited out however long it takes, with no second
Tools→Output MDB click landing on a running export.

**Fix I would make:** an adaptive wait that watches the target file's size/mtime still moving and
gives up only when it stops — the pattern `LOAD_LIMIT_FLOOR`/`SAVE_LIMIT_FLOOR` already use. Any
fixed number will be wrong again next season; the database grows every year.

**Correction to my own earlier claim:** the "211s" figure I reported for this export was
**wrong** — inferred from file timestamps across two runs. The first clean measurement is ~620s.

### BUG 3 — height drift between the save and the store

**Files:** `commissioner/growth.py` writes inches into `league.dat` through the codec; whatever
updates the store's `height_inches` is the other half. **I did not find the divergence point.**

**Evidence**, read simultaneously from `CV_Prep/league.dat` and the store, before the offseason:

```
Chris Zimmer   save 6'2"   store 6'0"
Liam Zimmel    save 6'0"   store 5'10"
(the other five matched exactly)
```

**Expected:** the website and the game show the same height.

**Where I stopped:** I noticed it, reported it, and did not investigate. Unknown whether FBPB3's
training camp also changes heights, or whether a store write was missed.

### BUG 4 — `verify_save.py` fails on pro (NOT corruption, but real)

**Command:** `python tools/verify_save.py`

```
FAIL  pro: every roster is 15 {15: 16, 14: 4}
FAIL  pro: our players imported 296 of 300 matched by name
FAIL  pro: birthdays restored 296 of 300 matched by name+DOB
FAILED: pro
```

**Missing:** Jess Amidon (CHE), Cole Tamayo (PAR), Cedric Brumback (ASI), Barrett Bradwell (LCH)
— all `role=filler`.

**Why it is not corruption:** all four are present in `CV_Pro/retiredplayers0.dat`. They retired
at the 2026 rollover. `universe/manifest.json` is a creation-time snapshot and cannot know a
player legitimately retired. Present in every backup back to 2026-09-20 08:35 and absent from
2026-09-19 backups, so it long predates today's work.

**Still a real effect:** 4 pro teams carry 14 players. The weekly tidy logged *"4 of the AI's
signings out"*, which suggests the AI refills those slots and `protect_rosters` releases them
again — a churn loop **I did not confirm.**

### BUG 5 — `tests/test_season_boundary.py` fails whenever anything holds the save lock

**File:** `tests/test_season_boundary.py` line 268 → `commissioner/simweek.py` line 938.

```
commissioner.simweek.SimBusy: a sim is already running
AssertionError: "never finished" does not match "a sim is already running"
```

**Cause:** it calls `simweek.run_sim(leagues=["prep"], days=999)` for real. It passes when idle
and fails whenever anything else is using the saves. I saw it fail only while a live sim was
running, and pass on every idle run.

**Possible real issue underneath:** a test that starts a real sim will keep doing this to whoever
runs the suite at the wrong moment. Might want a guard.

### BUG 6 — skipped tests

* `tests/test_codec.py` — SKIPs here for lack of fixtures. **This matters beyond coverage:** it
  guards a roughly one-third faster parser, and the parser is the dominant cost in BUG 1's
  performance problem. Fixing the fixtures unblocks the biggest optimization available.
* `tests/test_raise_stamina.py` — SKIPs; needs the game.

### BUG 7 — mine, already fixed, recorded for shape

`commissioner/offseason.py` called `ageout.apply(dry_run=True)` for the **Dry run** button.
`apply()` performs every release/rename/sign and then throws the result away, and each splices
and re-parses 425 records — ~7 minutes per league. It turned a button documented as answering in
half a second into a **14-minute silent stall holding the save lock.** Nate hit it: *"idk what
it's doing and idk when it ends"*. Now calls `ageout.plan()`. **14 min → 13s.** Fixed, committed,
and live in the running panel.

### Incomplete implementations

* **No trades / transactions / news anywhere in the reports.** FBPB3 exports
  `transactions.htm` and `waiverwire.htm` per league and nothing reads them. Nate asked for
  "trades, any news" and I told him it was not built.
* **The offseason status card is untested** (see section 4).
* **`commissioner/takeaways.py` reads the PUBLISHED `site/leagues/<key>/*.json`.** If a report is
  ever generated before a publish, it silently produces nothing. It has never been exercised
  inside a real offseason — the run that would have used it predates the module.
* **No 15-year-olds in prep for one season is EXPECTED, not a bug:** a 4-year band (14–17) became
  a 5-year band (14–18), so one cohort is missing. Self-corrects by 2031.

---

## Measured timings — Nate explicitly asked these be written down

From the real 2027→2028 offseason (2095s total):

| step | per league | total |
|---|---|---|
| age-out | 346s | **691s** (prep + college) |
| new-season verify + export | ~193s | ~580s |
| simming 52 idle days to the offseason panel | ~122s | ~370s |
| END SEASON / OFFSEASON / TRAINING CAMPS | 19s each | ~170s |
| HIRE STAFF | 43s | ~130s |
| growth, bonuses, promotions, points | — | ~46s |

From sim rounds: **pro's MDB export ~620s every round**, over 60% of a playoff round.

**Ranked:**

1. **Age-out, 691s.** 153 splices × ~2.2s, each re-parsing all 425 records. Fix by applying edits
   from the **end of the file backwards** so earlier offsets never shift, then re-parse once.
   Touches `commissioner/codec/league_dat.py` `_splice()`, which has no undo.
2. **52 idle days at ~2.4s each** — *slower* than real basketball at ~1.2s/day, which points at
   the settle wait dominating a screen that barely changes. Likely the quickest win.
3. **Pro's MDB** — probably should not run on every intermediate playoff round.

Nate asked that these go into the repo as a **dated** baseline. Every optimization today started
from a stale comment measured once and never revisited (`"about 7 s a league"`, `211s`).

---

## Shipped today (all on master)

`output_mdb` 180s→600s (`dismiss_all` was **cancelling the running export**, four times over);
`git_push` rewritten with plumbing — no worktree, checkout or copy, local deploy ~40s→4.7s, with
`tests/test_deploy_push.py` driving it against a real bare repo; day-watcher capture 62ms→0.73ms
via BitBlt with **every accepted advance still PrintWindow-confirmed**, `tests/test_day_watch.py`;
`commissioner/ageout.py` + `tools/age_out.py` + `tests/test_age_out.py` (ladder prep 14–18,
college 19–22, `PREP_LAST_AGE` 17→18); `commissioner/takeaways.py` + `tests/test_takeaways.py`;
playoff controls beside the calendar plus a "Play the whole playoffs" chain with three tested
stop conditions; the dry-run fix.

**Test state: 47 pass, 0 fail, 2 skip when idle.** Run with
`DISCORD_WEBHOOK_URL=http://127.0.0.1:9/blocked`.

**2027 champions:** prep **Tulips**, college **Mandibles**, pro **Swiss** (#2 seed, 4–2 over the
top-seeded Leghorns).

---

## Standing rules

* Never print or commit the Supabase `service_role` key or the Discord webhook URL.
* Do not start a Sim Week or an offseason without Nate's explicit OK.
* Never open FBPB3 by hand on a save.
* `tests/test_season_boundary.py` starts its own sim — run it only when idle.

The worktree is clean and stable. It is yours.
