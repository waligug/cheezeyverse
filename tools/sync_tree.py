"""Write the skill tree into Supabase: commissioner/tree.py -> public.tree_nodes.

The SQL (supabase/skill_tree.sql) creates the table and guards purchases; the nodes themselves
live in commissioner/tree.py, so tuning a node is an edit there and a run of this - no paste.
A node the file no longer defines is switched off, never deleted: whoever owns it keeps it.

    python tools/sync_tree.py            show what would change
    python tools/sync_tree.py --write    write it
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store, tree  # noqa: E402

FIELDS = ("branch", "tier", "name", "blurb", "cost", "stage", "requires", "min_spent",
          "min_ratings", "group_key", "effects", "sort")


def diff(live):
    by_id = {r["id"]: r for r in live}
    changes = []
    for node in tree.TREE:
        have = by_id.get(node["id"])
        if have is None:
            changes.append(("new", node["id"], node["name"]))
            continue
        moved = [f for f in FIELDS if json.dumps(have.get(f), sort_keys=True) != json.dumps(node.get(f), sort_keys=True)]
        if moved or not have.get("active", True):
            changes.append(("change", node["id"], ", ".join(moved) or "reactivated"))
    defined = {n["id"] for n in tree.TREE}
    for r in live:
        if r["id"] not in defined and r.get("active", True):
            changes.append(("retire", r["id"], r.get("name", "")))
    return changes


def main(argv):
    write = "--write" in argv
    live = store.tree_nodes()
    changes = diff(live)
    for kind, node_id, what in changes:
        print(f"{kind:<7} {node_id:<30} {what}")
    if not changes:
        print(f"the tree in Supabase matches commissioner/tree.py ({len(tree.TREE)} nodes)")
        return 0
    if not write:
        print(f"\n{len(changes)} change(s); run with --write to apply")
        return 0
    store.upsert_tree_nodes(tree.TREE)
    if any(k == "retire" for k, _, _ in changes):
        store.retire_tree_nodes([n["id"] for n in tree.TREE])
    left = diff(store.tree_nodes())
    print(f"\nwritten; {len(left)} difference(s) left" + ("" if not left else f": {left}"))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
