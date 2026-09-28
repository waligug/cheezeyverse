"""Open the skill tree - or close it again. The switch, the economy it runs on, and its nodes.

supabase/skill_tree.sql must have been pasted first; this checks, and refuses otherwise.

    python tools/open_tree.py --check    what is live, and what --open would change
    python tools/open_tree.py --open     sync the nodes, remember today's economy, switch on
    python tools/open_tree.py --close    put back the economy --open replaced, switch off

--close is the rollback: nobody loses a node or a Cap Breaker they already have, prices go back to
the old curve at once, and the old pay resumes from the next Sim Week.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store, tree  # noqa: E402

ROLLBACK = ROOT / "universe" / "tree_rollback.json"

#: what the tree runs on (docs/SKILL_TREE.md has the balance behind every number)
ECONOMY = {
    "skill_tree_enabled": True,
    # the same pay for everybody, every sim week, at every level
    "points_per_week": 3, "points_per_week_prep": 3, "points_per_week_college": 3, "points_per_week_pro": 3,
    # the offseason: flat, plus a flat grant for moving up; no stat bonuses, no salary payouts
    "offseason_points": 6, "promotion_points": 10, "college_development_bonus": 0,
    # the game's own offseason development applies to our players both ways, falls capped at 5
    "rollover_two_way": True, "rollover_drop_cap": 5,
    # Cap Breakers: one a season to every pro up to this age, and to everybody who moves up
    "breaker_last_age": 30,
}


def live_state():
    problems = []
    try:
        on = store._request("POST", "/rest/v1/rpc/cv_tree_on", body={})
    except Exception as exc:                                          # noqa: BLE001
        on = None
        problems.append(f"cv_tree_on() is missing - paste supabase/skill_tree.sql first ({str(exc)[:80]})")
    try:
        store._request("GET", "/rest/v1/characters", params={"select": "nodes,cap_breakers", "limit": "1"})
    except Exception as exc:                                          # noqa: BLE001
        problems.append(f"characters.nodes / cap_breakers are missing ({str(exc)[:80]})")
    try:
        store._request("GET", "/rest/v1/tree_nodes", params={"select": "id", "limit": "1"})
    except Exception as exc:                                          # noqa: BLE001
        problems.append(f"public.tree_nodes is missing ({str(exc)[:80]})")
    return on, problems


def price(from_value, kind="rating"):
    return store._request("POST", "/rest/v1/rpc/cv_upgrade_cost",
                          body={"p_from": from_value, "p_steps": 1, "p_kind": kind})


def main(argv):
    mode = next((a for a in argv if a in ("--check", "--open", "--close")), "--check")
    settings = store.get_settings()
    on, problems = live_state()
    print(f"skill tree switch: {settings.get('skill_tree_enabled', False)!r} (database says {on!r})")
    if settings.get("scheduled_settings"):
        print(f"note: scheduled_settings is not empty and applies at an offseason: "
              f"{json.dumps(settings['scheduled_settings'])[:200]}")
    for p in problems:
        print("MISSING  " + p)
    changes = {k: (settings.get(k), v) for k, v in ECONOMY.items() if settings.get(k) != v}
    for k, (was, now) in changes.items():
        print(f"  {k:<28} {was!r:>10} -> {now!r}")

    if mode == "--check":
        return 1 if problems else 0
    if problems:
        print("\nrefusing: the SQL is not live yet")
        return 1

    if mode == "--open":
        from tools.sync_tree import diff
        store.upsert_tree_nodes(tree.TREE)
        left = diff(store.tree_nodes())
        if left:
            print(f"refusing: the tree did not sync cleanly: {left}")
            return 1
        print(f"tree synced: {len(tree.TREE)} nodes")
        if not ROLLBACK.exists():
            ROLLBACK.write_text(json.dumps({k: settings.get(k) for k in ECONOMY}, indent=1), encoding="utf-8")
            print(f"today's economy saved to {ROLLBACK.name} for --close")
        if not settings.get("tree_opened_at"):
            store.set_setting("tree_opened_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for k, v in ECONOMY.items():
            if settings.get(k) != v:
                store.set_setting(k, v)
        got = (price(90), price(90, "potential"), price(40))
        ok = got == (18, 54, 1)
        print(f"live prices: 90 -> {got[0]}, ceiling at 90 -> {got[1]}, 40 -> {got[2]}  "
              f"{'as designed' if ok else 'NOT what the tree charges'}")
        print("the skill tree is OPEN" if ok else "opened, but check the prices above")
        return 0 if ok else 1

    # --close
    back = json.loads(ROLLBACK.read_text(encoding="utf-8")) if ROLLBACK.exists() else {}
    for k in ECONOMY:
        value = back.get(k)
        # A switch that did not exist before --open was saved as None; it goes back to off, not
        # left on. (rollover_two_way is not set today, so skipping None kept the rollover two-way.)
        if value is None and isinstance(ECONOMY[k], bool):
            value = False
        if value is None:
            continue
        store.set_setting(k, value)
    store.set_setting("skill_tree_enabled", False)
    got = price(90)
    print(f"closed. live price at 90 is {got} ({'the old curve' if got == 5 else 'check it'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
