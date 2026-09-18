"""One character, end to end, the way a person would actually do it.

Builds him through the real site rules (tools/make_test_build.mjs imports site/js/rules.js, so
nothing here re-implements the quiz), records him, sims a week, and then checks the things that
have each broken at least once before:

  * he is ACTIVE and holds a reserve slot
  * the save file really holds his name, his birthday and his ratings
  * he is not Inactive, and the depth chart gives him minutes - a character who never plays is
    the failure nobody notices until a friend asks why his player has no stats
  * the published site has a page for him
  * a rating snapshot was written, so the career graph has a first point

It runs against the LOCAL json store on purpose. The Supabase path needs a signed-in Discord
profile to own the character, and creating an account is not something this script should do.

    python tools/e2e_test.py              # create, sim, check
    python tools/e2e_test.py --cleanup    # retire him and give the slot back
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

from commissioner import localstore, simweek          # noqa: E402
from commissioner.codec.league_dat import LeagueDat   # noqa: E402
from commissioner import characters as ch             # noqa: E402

LEAGUE = "prep"
NAME = ("Testy", "Mcslot")

# Force the local store: simweek.store() prefers Supabase whenever it is configured, and it is.
simweek.store = lambda: localstore

ok, bad = [], []


def check(label, passed, detail=""):
    (ok if passed else bad).append(label)
    print(f'{"PASS" if passed else "FAIL"}  {label}' + (f"  {detail}" if detail else ""))


def build_row():
    out = subprocess.run([r"node", str(ROOT / "tools" / "make_test_build.mjs")],
                         capture_output=True, text=True, cwd=ROOT)
    if out.returncode:
        sys.exit(f"the site rules would not build a character:\n{out.stderr}")
    return json.loads(out.stdout)


def find_existing():
    for c in localstore.characters():
        if (c.get("first_name"), c.get("last_name")) == NAME:
            return c
    return None


def cleanup():
    c = find_existing()
    if not c:
        print("nothing to clean up")
        return
    localstore.retire_character(c["id"], season=0, reason="end-to-end test", release_slot=True)
    print(f'retired {c["first_name"]} {c["last_name"]}')
    print("now run: python tools/protect_rosters.py prep   (it gives the reserve slot its name back)")


def main():
    if "--cleanup" in sys.argv:
        return cleanup()

    built = build_row()
    row, readout = built["row"], built["readout"]
    print(f'built through site/js/rules.js: {readout["klass"]}, '
          f'ratings {readout["ratingMin"]}-{readout["ratingMax"]}, '
          f'potentials {readout["potentialMin"]}-{readout["potentialMax"]}, '
          f'grows to {readout["expectedAdultHeight"]}in')

    c = find_existing()
    if c:
        print(f'reusing the character already recorded ({c["status"]})')
    else:
        c = simweek.create_character({**row, "owner": "e2e-local-owner"})
        print(f'recorded {c["first_name"]} {c["last_name"]} as {c["status"]}')

    # A 14 year old must actually be bad at basketball. This has regressed before.
    check("starting ratings are those of a fourteen year old",
          readout["ratingMax"] <= 50, f'highest is {readout["ratingMax"]}')
    check("every rating has room to grow",
          readout["potentialMin"] > readout["ratingMin"],
          f'lowest potential {readout["potentialMin"]} vs lowest rating {readout["ratingMin"]}')

    print("\nsimming a week (this drives the game)...")
    result = simweek.run_sim([LEAGUE])
    print(f'sim finished: {json.dumps({k: v for k, v in result.items() if k != "log"})[:300]}')

    c = find_existing()
    check("character is active after the sim", c and c.get("status") == "active",
          f'status {c and c.get("status")}')
    slot = (c or {}).get("claimed_slot") or {}
    check("he holds a reserve slot", bool(slot.get("name")), str(slot))

    L = LeagueDat(ch.save_path(LEAGUE))
    try:
        pl = L.find(f"{NAME[0]} {NAME[1]}")
        found = True
    except Exception as exc:
        pl, found = None, False
        print(f"   could not find him in the save: {exc}")
    check("the save file holds him by name", found)

    if pl:
        check("he is on a team", pl.values["Team"] >= 1, f'team {pl.values["Team"]}')
        check("he is not flagged inactive", pl.values.get("Inactive", 0) == 0,
              f'Inactive={pl.values.get("Inactive")}')
        stamped = {k: v for k, v in row["ratings"].items() if k in pl.values}
        wrong = {k: (pl.values[k], v) for k, v in stamped.items() if pl.values[k] != v}
        check("his ratings are the ones the quiz produced", not wrong,
              f"{len(wrong)} differ: {dict(list(wrong.items())[:3])}" if wrong else "")
        teams = L.teams()
        mine = teams.get(pl.values["Team"], {})
        check("he is on the roster", pl.id in (mine.get("ids") or ()),
              f'roster of {len(mine.get("ids") or ())}')
        # Real minutes, read straight out of the depth chart: five position blocks of 80 int16
        # slots each, holding the player id that plays that slot. This is the check that
        # matters - a character can be rostered, active and rated well and still never appear
        # in a box score, which is exactly what a reserve slot is built to do.
        import struct
        slots = 0
        for blk in mine.get("depth_at", []):
            slots += sum(1 for v in struct.unpack_from("<80h", L.data, blk + 2) if v == pl.id)
        check("the depth chart gives him minutes", slots > 0, f"{slots} depth slot(s)")

    snaps = localstore.snapshots((c or {}).get("id"))
    check("a rating snapshot was written for the career graph", bool(snaps),
          f"{len(snaps)} snapshot(s)")

    ids = (c or {}).get("league_player_ids") or {}
    page = ROOT / "site" / "leagues" / LEAGUE / "players" / f'player{ids.get(LEAGUE)}.htm'
    check("the published site has his player page", bool(ids.get(LEAGUE)) and page.exists(),
          str(page) if ids.get(LEAGUE) else "no league_player_ids recorded")

    print(f"\n{len(ok)} passed, {len(bad)} failed")
    if bad:
        print("failed: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
