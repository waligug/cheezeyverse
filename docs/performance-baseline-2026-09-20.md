# Commissioner performance baseline — 2026-09-20

Measured during the real 2027 → 2028 rollover. These are wall-clock observations, not estimates.

| Work | Per league | Total / share |
|---|---:|---:|
| AI age-out | 346 s | 691 s for prep + college |
| New-season verify + export | ~193 s | ~580 s |
| Sim 52 idle days to offseason | ~122 s | ~370 s |
| END SEASON / OFFSEASON / TRAINING CAMPS | ~19 s | ~170 s |
| HIRE STAFF | ~43 s | ~130 s |
| Growth, bonuses, promotions and points | — | ~46 s |
| Pro MDB during playoff rounds | ~620 s | over 60% of a round |

The age-out baseline used one full `league.dat` re-parse per released, renamed and signed player.
The first optimization batches every release for a team into one roster rewrite. Re-measure on the
next rollover before changing the intake path further.

Idle days are slower than game days (~2.4 s/day versus ~1.2 s/day), which points to the screen-settle
wait rather than basketball work. The MDB export remains necessary for game history and career
archival, but intermediate playoff rounds should be measured to decide whether each one needs it.
