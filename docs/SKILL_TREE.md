# The skill tree

Live from the day `tools/open_tree.py --open` runs. Everything here is a dial: prices, caps and
pay are rows in `settings`, the nodes are `commissioner/tree.py`, and none of it needs a new SQL
paste to tune (`tools/sync_tree.py` rewrites the nodes, `store.set_setting` the numbers).

## What a player sees

- **Pay:** 3 points every sim week, the same for everybody at every level. +6 each offseason,
  +10 on moving up. About 100 a season. Salary, stats and wins pay nothing any more.
- **Ratings:** bought one step at a time, priced by the value the step leaves:

  | rating    | under 50 | 50s | 60s | 70s | 80–84 | 85–89 | 90–94 | 95–99 | 100+ |
  |-----------|----------|-----|-----|-----|-------|-------|-------|-------|------|
  | per +1    | 1        | 2   | 3   | 5   | 8     | 12    | 18    | 25    | 35   |

  Traits, the career goal and the tree's discounts still move the price (50–200%).
- **Stage caps:** nothing is bought past 70 in prep or 85 in college. What is already over the
  line stays; it grows again after promotion.
- **Ceilings (potentials):** buyable at 3x the step price, and never more than 10 above the rating.
  The game grows every player toward his ceilings on its own, so an unlimited ceiling was free
  growth for years.
- **The tree:** five branches (Scoring, Playmaking, Defense, Rebounding, Athletic).

  | tier       | cost | opens in | needs spent in the branch | gives |
  |------------|------|----------|---------------------------|-------|
  | 1          | 15   | prep     | 20                        | ceilings +3 on each rating (+6 on the two-rating branches; Athletic +1 rating each) |
  | 2          | 30   | college  | 60                        | the same again, and the branch 10% cheaper |
  | 3          | 50   | pro      | 100                       | ceilings +6 (+10), and the branch ages half as fast |
  | Signature  | 60   | pro      | 160                       | +9 / +7 to two ratings and +8 on their ceilings; **one per player** |

  "Spent in the branch" counts points put into that branch's ratings and ceilings since the tree
  opened, so the veterans' old spending does not open anything for free.
- **Cap Breakers:** one each offseason to every pro aged 30 or under, and to anybody who moves
  up. +3 on a ceiling of his choice, at most three on one rating.

## Why these numbers (the balance model, 2026-09-27)

`scratchpad/tree/model.py` in the session that built this; the method is what matters:

1. **The game's own development was measured, not guessed.** Season-end backups 2033-2039,
   every AI player: FBPB3 moves a pro 22-30 about 0.2 of the way from rating to ceiling each
   season, a little in prep and college, and turns negative from 34. It never lowers a rating
   during the season; all the falls happen at END SEASON.
2. **The old system was reproduced.** Run from the original seven's real creation sheets under
   the old economy, the model lands where the league actually is on average. It only did so once
   it included the ratchet (3).
3. **The ratchet.** The rollover used to keep every rise and cancel every fall for our players
   only. Over the 2039 season the AI lost a third of its ratings and two thirds of its ceilings
   at the rollover; our players lost none. Tim Turner's Def. Rebounding ceiling went 80 -> 124
   without a point spent on it. That, far more than points, is why all seven sit in the pro top
   36 and four in the top five. With the tree, falls count for us too, capped at 5 per rating per
   offseason (`rollover_two_way`, `rollover_drop_cap`).
4. **Targets** (Nate: our players must have "a chance to be the goats", but not a guarantee):

   | a new character, played well | prep 16 | college 21 | pro rookie 23 | 26 | peak 29-32 | 35 |
   |------------------------------|---------|------------|---------------|----|------------|----|
   | rank in his league (median)  | ~#9     | ~#3        | ~#20 of 368   | #6 | #3-5       | #15-20 |

   About two in three dedicated builds have three or more seasons in the pro top 3; the one-way
   rollover made that 100%. A casual player (spending half the weeks) lands within a season of
   the same place, because the pay is identical and banks.
5. **The ten already playing** keep everything they have. Tim and Johnny stay #1-2 until about
   32; Josh, Lightning and Beans project to #3-5 around 30.

## How it is built

- `supabase/skill_tree.sql` (pasted once; also folded into `schema.sql`): `tree_nodes`,
  `characters.nodes` / `cap_breakers`, request kinds `node` and `breaker`, the switch
  `settings.skill_tree_enabled`. With the switch off everything behaves exactly as before.
- `commissioner/tree.py`: the nodes and their arithmetic; `simweek._apply_requests` writes a
  node or a Cap Breaker into league.dat, `offseason` pays flat and hands out Cap Breakers,
  `seasonflow` runs the two-way rollover and ages a tier-3 branch at half speed.
- `site/js/tree.js`: the page's copy of every rule and the Tree tab on My players.
- Tests: `test_skill_tree_sql.py` (a real Postgres, the live upgrade path and a fresh install),
  `test_tree.py` (every node's arithmetic against the real `apply_upgrade_requests`),
  `test_tree_js.py` (6,040 prices identical to the database's, the send order, node states).
