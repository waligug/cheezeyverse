# Handoff — 2026-09-20, end of the 2027 season

Nate is out of limit and has asked Codex to take everything from here. I could not reach Codex
by message (both relay addresses are stale and `ListAgents` shows no reachable peer), so this
file is the handoff. Read **The bug I shipped today** first — it is the one thing that needs a
decision before the next Sim Week.

---

## State right now, all verified rather than assumed

| | |
|---|---|
| Offseason 2027→2028 | **complete, `status=ok`, 2095s** |
| Recovery marker | clean |
| Store | `current_season: 2028`, `current_week: 0` |
| All three saves | season 2028, day 15, 2028-10-31, in lockstep |
| Panel | idle |
| Tests | 47 pass, 0 fail, 2 skip |

**2027 champions:** prep **Tulips**, college **Mandibles**, pro **Swiss** (the #2 seed, 4–2 over
the top-seeded Leghorns). All three `champs.htm` roll-of-honour pages now carry their 2027 row —
FBPB3 only appends that at END SEASON, which is why it was missing all day.

---

## The bug I shipped today — the age-out is being undone

**`commissioner/ageout.py` runs in the wrong place, and FBPB3's own offseason reverses most of
it.** The evidence, from the save as it stands now:

* Prep rosters should read 14–18. They read **14, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25**.
* **78 players aged 19+ are back on prep rosters**, and **none of them is still defanged**.
* Gregory Delima — released and crushed to rating 2 by the age-out — is back on team 18 at 19,
  with his top rating grown back to 5.
* Every roster is **20 players, not 15**.

**Why.** In `run_offseason`, `_run_offseason(...)` (which contains the age-out) is called at
~line 728, and `seasonflow.rollover_saves(...)` at ~line 734. So the age-out releases 51 players
into free agency and defangs them, and *then* FBPB3's native rollover — free agency, HIRE STAFF,
TRAINING CAMPS — signs them straight back and grows them past the floor. The run log shows it
plainly: age-outs at 391.8s and 736.6s, `prep: advancing the game to season 2028` at 743.6s.

**What survived:** the 51 newcomers at 14 are on rosters, and Caron Gilstrap and Erman Beehler
are gone from the file entirely (FBPB3 retired them itself).

**The obvious fix is to move the age-out after `rollover_saves`, but please do not just move it
and call it done.** Two things make it harder than it looks:

1. FBPB3 signs free agents during preseason too, so "after the rollover" may only narrow the
   window rather than close it.
2. `tools/protect_rosters.py` will not help. It releases anyone rostered who is **not in the
   manifest**, and the aged-out players *are* manifest fillers, so it leaves them alone — and
   `_looks_like_ours` deliberately spares anyone whose birth year is inside our band.

Worth considering: deleting the record rather than releasing it (needs the player-file import
path in `universe/generate.py`, which the codec cannot do), or re-running the age-out as the
last step after the rollover and re-defanging, or giving `protect_rosters` an age rule.

The 20-man rosters are probably just camp rosters — the 2026 notes say preseason left them at
17–20 and the next sim trims them — but that should be confirmed, not assumed.

---

## Uncommitted work, now committed alongside this file

`commissioner/simstatus.py` and `commissioner/offseason.py` carry a **finished but untested**
feature: a live Discord progress card for the offseason. Nate asked for it explicitly ("we gotta
have a discord status for the offseason too"). It reuses `SimStatus` with `kind="offseason"` and
`label="Season N -> N+1"`; `render()` gained `kind`/`label`, `STAGES` gained the offseason
phases, and `offseason.py` gained `_say`/`_finish`/`_one_line` plus 8 `_say` call sites.

It imports clean and I checked both card states by hand, but **it has no test and has never run
live** — the panel needs a restart to load it. Please test it before trusting it.

Also committed: `universe/history/*-2027.json`, including two previously untracked files. These
are the only copy of a finished season's lines once FBPB3 rebuilds `SeasonStats`.

---

## The optimization work Nate wants next

He deferred this until the offseason finished, and explicitly asked that the measured timings be
written down. **These are real numbers from the 2027→2028 run**, not estimates:

| step | per league | total |
|---|---|---|
| **age-out** | 346s | **691s** (prep + college) |
| **new-season verify + export** | ~193s | ~580s |
| **simming 52 idle days to the offseason panel** | ~122s | ~370s |
| END SEASON / OFFSEASON / TRAINING CAMPS | 19s each | ~170s |
| HIRE STAFF | 43s | ~130s |
| growth, bonuses, promotions, points | — | ~46s |

**Ranked by size:**

1. **Age-out, 691s.** My code. Every `release`/`rename`/`sign` calls `LeagueDat._splice`, which
   re-parses all 425 player records — 153 splices × ~2.2s. The fix is to apply edits from the
   **end of the file backwards** so earlier offsets never shift, then re-parse once. That touches
   the codec, which has no undo. Note `tests/test_codec.py` SKIPs here for lack of fixtures and
   is guarding a ~⅓ faster parser that would help this directly — fix the fixtures first.
2. **52 idle days to the offseason panel, 370s.** Empty days at ~2.4s each, *slower* than real
   basketball at ~1.2s/day. That points at the settle wait dominating on a screen that barely
   changes. Probably the quickest win available.
3. **Pro's MDB export, ~620s every sim round** — over 60% of a playoff round. Needed for the
   game-by-game table and for archiving careers before players retire, but almost certainly does
   not need to run on every intermediate round.

Please write these into the repo as a dated baseline. Every optimization today started from a
stale comment measured once and never revisited (`"about 7 s a league"`, `211s`), and a dated
file is what stops that happening again.

---

## Other open items

* **Height drift.** The save and the store disagree: Chris Zimmer 6'2" in `league.dat` vs 6'0"
  in the store, Liam Zimmel 6'0" vs 5'10". The site would show a different height than the game.
* **MDB budget still too tight.** `driver/fbpb3.py` `output_mdb` has `timeout=600`; pro measured
  ~620s. It recovered this morning by luck (no dialog was open, so the retry saw the finished
  file), not by design. It wants an adaptive wait that watches the file grow — the pattern the
  load and save waits already use — not a number somebody guessed.
* **`verify_save.py` fails on pro** with 4 missing players and 4 teams at 14. **This is not a
  corruption.** Jess Amidon, Cole Tamayo, Cedric Brumback and Barrett Bradwell all retired at the
  2026 rollover and are in `CV_Pro/retiredplayers0.dat`. The manifest is a creation-time snapshot
  and cannot know a player legitimately retired.
* **No 15-year-olds in prep** for one season is expected, not a bug: the league was generated as
  a 4-year band (14–17) and is now a 5-year band (14–18), so one cohort is missing. It fills
  itself by 2031.
* **Trades/news reporting is not built.** FBPB3 exports `transactions.htm` and `waiverwire.htm`
  per league and nothing reads them. Nate asked for "trades, any news" and I told him it was not
  built.

---

## Shipped today, all pushed to master

* `output_mdb` budget 180s → 600s. It was timing out and `dismiss_all()` was then **cancelling
  the still-running export**, four times over.
* `git_push` rewritten with plumbing — no worktree, no checkout, no copy. Local deploy ~40s →
  4.7s. `tests/test_deploy_push.py` drives it against a real bare repo.
* Day-watcher capture: full-window PrintWindow every 0.1s (62ms) → BitBlt of the date box
  (0.73ms), 86× cheaper. Every accepted day advance is **still** confirmed by PrintWindow; the
  cheap read never decides anything. `tests/test_day_watch.py` pins both failure directions.
* `commissioner/ageout.py`, `tools/age_out.py`, `tests/test_age_out.py`. Ladder is prep 14–18,
  college 19–22, move up at 19 — Nate's call. `PREP_LAST_AGE` 17 → 18 to match.
* `commissioner/takeaways.py`, `tests/test_takeaways.py` — real season takeaways in the report.
* Playoff controls beside the calendar, plus a "Play the whole playoffs" chain with three tested
  stop conditions.
* Dry-run fix: it was calling `ageout.apply(dry_run=True)`, which does every splice and throws
  the result away — 14 minutes holding the save lock. Now calls `ageout.plan()`. **14 min → 13s.**

---

## Standing rules

* Never print or commit the Supabase `service_role` key or the Discord webhook URL.
* Do not start a Sim Week or an offseason without Nate's explicit OK.
* Never open FBPB3 by hand on a save.
* Run tests with `DISCORD_WEBHOOK_URL=http://127.0.0.1:9/blocked`.
* `tests/test_season_boundary.py` starts its own sim and **will** fail if anything is using the
  saves. Run it when idle.
