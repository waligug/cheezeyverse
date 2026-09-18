"""Put the universe back to the start of a season, without destroying anybody's character.

Used when the leagues are rebuilt from scratch - a new start year, say. The saves are recreated
by tools/create_universe.py; this is the other half, the state that lives outside them.

What it does NOT do is delete characters. Somebody answered fourteen questions to make one, and
the rebuild is our doing, not theirs. A character goes back to `pending` - no roster slot, no
team, no in-game birthday - and the next Sim Week places him in the new save exactly as if he
had just been created. His quiz, his ratings, his potentials and his growth bias are untouched.

Points go back to the starting grant, because the weeks that paid them are being erased. Spent
points are refunded for the same reason: what they bought only ever existed in a save that is
about to stop existing.

    python tools/reset_season.py --dry-run
    python tools/reset_season.py --confirm
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store                       # noqa: E402
from commissioner.universe import config as cfg      # noqa: E402


def main():
    dry = "--confirm" not in sys.argv
    season = cfg.START_YEAR
    settings = store.get_settings()
    start_points = int(settings.get("starting_points", 20))
    characters = store.characters()

    print(f"{'DRY RUN - ' if dry else ''}resetting to season {season}")
    print(f"  settings  : current_season {settings.get('current_season')} -> {season}, "
          f"current_week {settings.get('current_week')} -> 0")
    print(f"  characters: {len(characters)}")
    for c in characters:
        print(f"    {c['first_name']} {c['last_name']}: {c['status']} "
              f"{c.get('team_abbrev') or '-'} {c['points_available']}pts"
              f"  ->  pending, no team, {start_points}pts")

    snaps = store.snapshots()
    runs = store.runs()
    print(f"  snapshots : {len(snaps)} -> deleted")
    print(f"  sim log   : {len(runs)} run(s) -> cleared")

    if dry:
        print("\nnothing written. Re-run with --confirm")
        return 0

    store.set_setting("current_season", season)
    store.set_setting("current_week", 0)
    # Deleted, not nulled: settings.value is NOT NULL, and "no offseason has run" is the
    # absence of the row rather than a null in it. run_offseason refuses to repeat a season it
    # has already done, and leaving 2030 in here would block the first real one.
    store._request("DELETE", "/rest/v1/settings", params={"key": "eq.last_offseason"})

    for c in characters:
        if c.get("status") == "retired":
            continue
        # Straight PATCH rather than set_character_field: these are the plumbing columns that
        # helper deliberately refuses, which is right everywhere except here.
        store._request(
            "PATCH", "/rest/v1/characters", params={"id": f'eq.{c["id"]}'},
            body={
                "status": "pending",
                "league": "prep",
                "team_abbrev": None,
                "claimed_slot": None,
                "game_dob": None,
                "league_player_ids": {},
                "level_history": [],
                "college_years": 0,
                "declared": False,
                "draft_round": None, "draft_pick": None, "draft_season": None,
                "retired_season": None, "retired_reason": None,
                "points_available": start_points,
                "points_spent": 0,
            })
        print(f'    reset {c["first_name"]} {c["last_name"]}')

    # Upgrade requests refer to a save that is about to be replaced.
    store._request("DELETE", "/rest/v1/upgrade_requests", params={"id": "not.is.null"})
    store._request("DELETE", "/rest/v1/rating_snapshots", params={"id": "not.is.null"})
    store._request("DELETE", "/rest/v1/point_ledger", params={"id": "not.is.null"})
    print("    cleared upgrade_requests, rating_snapshots, point_ledger")

    runs_file = ROOT / "universe" / "sim_runs.json"
    if runs_file.exists():
        runs_file.unlink()
        print("    cleared the local sim log")

    print(f"\nready for a rebuild at {season}. Next, on the machine with the game:")
    print("    python tools/generate_universe.py")
    print("    python tools/create_universe.py prep      (then college, then pro)")
    print("    python tools/stamp_dobs.py")
    print("    python tools/protect_rosters.py")
    print("    python tools/verify_save.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
