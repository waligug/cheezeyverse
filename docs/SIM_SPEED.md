# Making a sim faster

Measured 2026-09-18 on SERVERPC, from the panel's own event timestamps across five real runs
(weeks 1-4 at 7 days each, plus a 21-day run). Raw numbers in
`C:\claude\sim-timing-baseline-2026-09-18.md` on SERVERPC.

## Where the time actually goes

Week 4, 694 s, all three leagues:

| Phase | Time | Scales with days? |
|---|---:|---|
| load save (3x) | 108 s | no |
| **clicking SIM DAY** | **194 s** | **yes** |
| save game (3x) | 64 s | no |
| HTML export (1 965 pages) | 118 s | no |
| apply pending work | 70 s | no |
| snapshots | 35 s | no |
| publish + git push (push alone 51 s) | 81 s | no |
| **fixed cost per run** | **~500 s** | |

So a run costs roughly:

    seconds ~= 500 + 27.7 x days          (9.2 s per day per league, x3 leagues)

**Only 28 % of a weekly run is the simming.** The other 72 % is paid once per run no matter how
many days it covers. That single fact drives everything below: the cheapest speedup is not to
make the work faster but to press the button less often.

## Tier 0 - already available, no code (DONE 2026-09-18)

Type a bigger number into the panel.

**35 WAS the number to type. It is not, from 2026-09-19 onward.** 35 days pays a clean five
skill points, which is why it was recommended - but the regular season is running out. Nothing
in this project has ever simmed past the end of one: `sim_days` clicks SIM DAY blind, and
`offseason.py` moves everybody on through the codec without ever driving FBPB3's own playoffs or
rollover.

`run_sim` refuses any run that would cross it, and **the panel tells you the number** rather than
you having to look it up here - the refusal names the league with least room and how many days it
has. As of 2026-09-19 that is **23** (prep; college 24, pro 31), so **21 is the number to type**:
three clean weeks, safely inside it. `allow_season_end=True` lifts the guard for the code that
eventually owns the season-end path.

**The count is of scheduled dates after the last played one, not of calendar days**, and it is
deliberately an under-estimate: one click advances one calendar day, and there are at least as
many calendar days left as there are remaining game days. It also avoids parsing dates at all -
FBPB3 is VB6 and renders the WINDOWS SHORT DATE, so the format belongs to the machine. This
desktop exports `2030-10-15`; SERVERPC exports `10/20/2026`. The first version of this guard
parsed `%m/%d/%Y` and would have been silently inert on any box set the other way.

| | Wall clock |
|---|---:|
| 4 separate 7-day runs | 2 663.1 s measured (44.4 min) |
| 1 x 28-day run | 1 210.4 s measured (20.2 min) |

Same month of basketball, **2.2x faster, 24.2 minutes saved**, nothing written. Nate did this
himself on 2026-09-18 by putting 28 in the panel, and the run came in 5 % under the prediction.

The whole gain is the ~480 s of fixed cost being paid once instead of four times. The simming
itself was 728 s of the 1 210 (60 %), and it was dead linear at ~8.7 s per day per league
(244.5 / 242.0 / 241.5 across prep, college and pro) - which is what makes the formula above
trustworthy for planning longer runs.

## Tier 1 - FBPB3's own SIM MONTH button

One click instead of ~28, per league. Would take the variable 776 s of a 28-day run down to
something like 200 s, making a month about 700 s (~12 min).

Three coordinates are mapped on the Hot Seat screen already:

    HOTSEAT_SIM_DAY        = (794, 585)
    HOTSEAT_SIM_PRESEASON  = (910, 585)
    HOTSEAT_SIM_TO_PLAYOFFS= (910, 651)

The grid geometry suggests a fourth control at roughly (794, 651), and Nate reports seeing a
Sim Month. **Do not wire that coordinate up on inference.** Probe it on a throwaway copy of a
save and confirm three things before it goes anywhere near `sim_days`:

1. **What it is actually labelled.**
2. **Whether it stops at the season boundary.** If it runs through the end of the regular
   season into the playoffs, or past them into the offseason, it takes over the season rollover
   that `offseason.py` is supposed to own. This is the one that can do damage.
3. **How many days it advanced.** A calendar month is 28-31 days, not a number we choose. If
   the day delta cannot be read back afterwards, points cannot be granted correctly however
   fast the button is - and that alone disqualifies it.

Point (3) is the discriminator, so probe it first.

## Tier 2 - poll for completion instead of sleeping

**DONE for sim_days, 2026-09-19.** A day was measured at 0.55-1.2 s against a hard-coded 8 s
sleep, so about 85% of every run's sim time was spent watching an idle process. It now waits for
the calendar date to change and the window to settle, and a day that never advances raises
instead of being clicked past - which is also the 6/21 button swap caught for free. Measured
after: 10 days in 22.6 s, 2.3 s a day against 8.7. A 21-day three-league run saves about 400 s.

The remaining flat sleeps, all still worth the same treatment:

    load_save(wait=20)           and 30 in some call sites
    save_game(wait=15)
    html_output                  a fixed 5 s tail "because the per-player pages keep landing" 
Candidate completion signals, best first:

- **CPU of the FBPB3 process.** Generic, needs no UI mapping, works for loads, saves and
  exports as well as sim days: click, then wait for CPU to drop and stay down for a couple of
  samples. This is the one to try.
- **Control text on the Hot Seat screen** (the date should advance). Cheaper to read if it
  works, but VB6 windowless labels have no HWND and cannot be read at all - the same reason
  clicks on them need `real=True`. May simply not be available.
- **File mtime** is not a signal here: the game only writes league.dat when it saves.

Plausibly 500 s of fixed cost down to ~300 s. **This is the riskiest tier.** Every one of those
sleeps is load-bearing, and a poll that returns a moment early clicks into a screen that is not
ready yet - which is how the driver silently operates on the wrong league. Each converted wait
needs its own confirmation, not one blanket change.

## Tier 3 - do less work per run

- **`HOTSEAT_SIM_TO_PLAYOFFS` is already mapped.** For College and Pro, where no characters
  live, blasting to the playoffs in one click is a bigger lever than SIM MONTH and needs no new
  mapping. It collides with keeping the three leagues on the same calendar for the offseason,
  so evaluate it rather than assuming it - but it costs nothing to test.
- **The git push is 51 s** at the very end, when nothing else needs the machine. It could be
  fired off without waiting.
- **Export is 118 s** for 1 965 pages and happens every run for all three leagues. Cutting it
  means publishing College and Pro less often than Prep, which is a product decision, not a
  technical one.

## The floor

Export 118 s + publish 81 s + apply 70 s is about 270 s of work that has to happen whatever else
changes. A realistic best case for a whole month is **550-600 s, call it ten minutes**, against
44 minutes of weekly runs today.

## What batching costs, and what it does not

**It does not cost points, but 28 days pays four of them, not five.**
`weeks = max(1, round(days / 7))`, so 28 / 7 = 4 exactly and the 28-day run logged
`4 point(s) to 7 character(s)`. Nate asked for a chunk of about five points to spend, and
35 / 7 = 5 is the length that answers that.

| | Points | Wall clock |
|---|---:|---:|
| 5 x 7-day runs | 5 | ~3 330 s (55 min) |
| 1 x 28-day run | 4 | 1 210 s measured |
| 1 x 35-day run | 5 | ~1 392 s (~23 min) |

**But not this season.** The guard's count stands at 23 days (prep), so 35 would cross the end
and `run_sim` refuses. Five points in one press has to wait for a season with room in it, or for
the season-end path to be designed. 21 is the largest clean multiple of seven that fits.

A remainder bank is only needed if a variable-length calendar SIM MONTH is adopted, since 28
and 35 are both exact - do not add one before then, because `current_week` dates every snapshot
and is persisted.

**It was expected to cost re-dress passes, and it did not.** `_dress_characters` runs once per
run, after the sim, so the worry was that a monthly run lets the AI coach freeze a fringe
character out for four weeks where weekly runs would have put him back four times.

Measured on the 28-day run of 2026-09-18, and the worry was wrong:

| | 4 x 7 days | 1 x 28 days |
|---|---|---|
| characters needing a re-dress | 3-4, **every week** | **2, once, at the end** |
| Tim Turner (the fringe case) | 3 games in the last week | **+5 games, +79 min, ZERO re-dresses** |

Every character gained 5 or 6 games; Tim played every MTL game in the window and needed no
dressing at either end of the run. So the once-per-run re-dress cost nobody anything, and the
monthly run actually needed *fewer* interventions than the weekly ones.

The reason matters more than the number: **Tim's problem was never the re-dress cadence, it was
his team.** He was 0 games in 16 on JER and is a regular on MTL. Once a character is somewhere
he fits, the coach stops benching him and the re-dress frequency stops mattering. Re-dressing
is a safety net, not the mechanism that gets people minutes.

Caveat worth keeping: this is one month, with every character on a team that suits him. A
future character parked on the wrong roster could still be frozen out for a month, and the
once-per-run pass would take that long to catch it. Watch the re-dress count - if it climbs
back to 3-4 per run, that is the signal to shorten the runs again, not the clock.

Stamina is **not** known to fix this either; see `tools/raise_stamina.py`.
