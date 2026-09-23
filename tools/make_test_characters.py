"""Create throwaway characters for rehearsing the offseason, one per league.

The offseason is the only major path that has never run with a character in it, and it is the
destructive one: it ages everybody, moves players BETWEEN save files, runs a draft, retires
people and hands reserve slots back. Rehearsing it needs something in each league for each of
those branches to act on.

Built through the real site rules (tools/make_test_build.mjs imports site/js/rules.js), so these
are the same shape as a character somebody actually made, not hand-written numbers.

    python tools/make_test_characters.py            # show what it would create
    python tools/make_test_characters.py --confirm
    python tools/make_test_characters.py --remove   # delete them again

Every one is named so nobody could mistake it for a real player, and --remove takes them all out
by that naming. Restores `max_characters` afterwards: the database enforces a per-owner limit and
three test characters under one account would otherwise be refused.
"""
from __future__ import annotations

import json
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store  # noqa: E402

LAST = "Rehearsal"
PLAN = [
    # league, first name, which way to answer the quiz, and what this one is here to exercise
    ("prep", "Prepton", "first", "aged up within prep, or promoted to college if he is old enough"),
    ("college", "Collie", "middle", "the college path, and the conversion when he moves up"),
    ("pro", "Proctor", "last", "already at the top: growth, decline and eventual retirement"),
]


def build(answers):
    out = subprocess.run(["node", str(ROOT / "tools" / "make_test_build.mjs"), "--answers", answers],
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode:
        sys.exit(f"the site rules would not build a character:\n{out.stderr}")
    return json.loads(out.stdout)["row"]


def existing():
    return [c for c in store.characters() if c.get("last_name") == LAST]


def remove():
    rows = existing()
    if not rows:
        print("no rehearsal characters to remove")
        return 0
    for c in rows:
        store._request("DELETE", "/rest/v1/characters", params={"id": f'eq.{c["id"]}'})
        print(f'  removed {c["first_name"]} {c["last_name"]} ({c["league"]})')
    print(f"\n{len(rows)} removed. The reserve slots they held are handed back by "
          "tools/protect_rosters.py, or by rebuilding.")
    return 0


def main():
    if "--remove" in sys.argv:
        return remove()
    confirm = "--confirm" in sys.argv

    already = existing()
    if already:
        print(f"{len(already)} rehearsal character(s) already exist:")
        for c in already:
            print(f'  {c["first_name"]} {c["last_name"]}: {c["league"]}, {c["status"]}')
        print("\nRun with --remove first if you want a clean set.")
        return 1

    profiles = store._table("profiles", {"select": "id,display_name"})
    if not profiles:
        sys.exit("no profile exists yet - somebody has to sign in with Discord first, because "
                 "characters.owner references profiles(id).")
    owner = profiles[0]
    print(f'owner: {owner["display_name"]}')

    settings = store.get_settings()
    limit = int(settings.get("max_characters", 3))
    print(f"max_characters is {limit}; {len(PLAN)} are needed")

    rows = []
    for league, first, answers, why in PLAN:
        row = build(answers)
        row.update({"owner": owner["id"], "first_name": first, "last_name": LAST,
                    "league": league})
        rows.append((row, why))
        r = row["ratings"]
        print(f'  {first} {LAST:<10} {league:<8} {row["position"]:<3} '
              f'{row["height_inches"]}in  ratings {min(r.values())}-{max(r.values())}   {why}')

    if not confirm:
        print("\nnothing written. Re-run with --confirm")
        return 0

    raised = False
    if limit < len(PLAN):
        store.set_setting("max_characters", len(PLAN))
        raised = True
        print(f"\nraised max_characters {limit} -> {len(PLAN)} for this")
    try:
        for row, _ in rows:
            made = store.add_character(row)
            print(f'  created {made["first_name"]} {made["last_name"]} in {made["league"]} '
                  f'({made["status"]}, {made["points_available"]} points)')
    finally:
        if raised:
            store.set_setting("max_characters", limit)
            print(f"restored max_characters to {limit}")

    print("\nNext: one Sim Week places them on rosters, then the offseason dry run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
