"""The weight a person chose has to reach league.dat, and it has to survive a save.

`weight_lbs` existed on the create page as a slider for a while before it existed anywhere
else: no column, no store field, and nothing writing it into the save. The review card said
"about 206 lbs" and the game had never heard the number. This is the test that would have
caught that, and it is deliberately end-of-the-line - it asks the FILE, after a write and a
re-parse, not the object in memory.

It runs on a COPY of a real save in a temp directory and never touches the original, the same
rule test_codec.py follows. When no save is on this machine it skips: only SERVERPC has them,
and a permanently red test teaches people to stop reading the suite.

    python tests/test_stamp_weight.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch  # noqa: E402
from commissioner import growth  # noqa: E402
from commissioner.codec.league_dat import RATINGS, LeagueDat  # noqa: E402

MANIFEST = ROOT / "universe" / "manifest.json"
failures = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def a_character(slot, **over):
    """A plausible pending character, in the shape the store hands one to stamp_character."""
    row = {
        "first_name": "Gouda", "last_name": "Brieson",
        "position": slot.position, "height_inches": 74, "build": "lean",
        "weight_lbs": 168,
        "dob": slot.dob,
        "ratings": {r: 30 for r in RATINGS},
        "potentials": {f"Pot{r}": 60 for r in ("Inside", "JumpShot", "FtShot")},
    }
    row.update(over)
    return row


def stamp_on_a_copy(character):
    """Stamp onto the first free prep reserve slot of a throwaway copy; read the file back."""
    source = ch.save_path("prep")
    if not source.exists():
        return None
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "league.dat"
        shutil.copy2(source, copy)
        L = LeagueDat(copy)

        # A slot is free when the save still calls it by its manifest name: a claimed one was
        # renamed to the character who took it.
        #
        # The manifest's birthday is tried first, because that is the pair the real code looks
        # up - `free_slots` builds a Slot straight from the manifest and `stamp_character` finds
        # the player by (name, dob). Where that misses, the save's own birthday is used and the
        # test says so. On the desktop's abandoned season-2030 copies every reserve is four years
        # younger than the manifest (`tools/stamp_dobs.py` was never run on them), and failing
        # there would be a fact about the dead universe rather than about weight.
        by_name = {p.name: p for p in L.players}
        slot, guessed_dob = None, False
        for row in manifest["players"]:
            if row["league"] != "prep" or row["role"] != "reserve":
                continue
            candidate = ch.Slot.from_manifest(row)
            try:
                L.find(candidate.name, ch.codec_dob(candidate.dob))
                slot = candidate
                break
            except Exception:
                found = by_name.get(row["name"])
                if found is None:
                    continue
                slot, guessed_dob = ch.Slot.from_manifest({**row, "dob": found.dob}), True
                break
        if guessed_dob:
            print("NOTE  no reserve matched its manifest birthday; using the save's own. "
                  "Run tools/verify_save.py - this save never had stamp_dobs run on it.")
        if slot is None:
            return "no free reserve slot in the prep save"

        body = character(slot)
        before = dict(L.find(slot.name, ch.codec_dob(slot.dob)).values)
        pl = ch.stamp_character(L, slot, body)
        in_memory = dict(pl.values)
        L.save()                                  # writes, re-parses and verifies every value
        after = LeagueDat(copy)
        again = after.find(f'{body["first_name"]} {body["last_name"]}',
                           ch.codec_dob(body["dob"]))
        return {"wanted": body, "before": before, "memory": in_memory,
                "file": dict(again.values)}


def main():
    if not ch.save_path("prep").exists():
        print(f"SKIP  no prep save on this machine ({ch.save_path('prep')})")
        print("      This runs where the game lives; see docs/SERVER.md.")
        return 0

    out = stamp_on_a_copy(a_character)
    if isinstance(out, str):
        print(f"SKIP  {out}")
        return 0

    wanted, in_file = out["wanted"], out["file"]
    check("the chosen weight is in the file after a save",
          in_file["Weight"] == wanted["weight_lbs"],
          f'wrote {wanted["weight_lbs"]}, file says {in_file["Weight"]}')
    check("height still lands too, on the same record",
          in_file["Height"] == wanted["height_inches"],
          f'wrote {wanted["height_inches"]}, file says {in_file["Height"]}')
    check("the dormant filler's own weight did not survive him",
          in_file["Weight"] != out["before"]["Weight"],
          f'slot weighed {out["before"]["Weight"]}, now {in_file["Weight"]}')
    check("a rating landed, so the stamp really happened",
          in_file["InsideScoring"] == 30, f'got {in_file["InsideScoring"]}')

    # No weight of his own: the build has to stand in for one, and the file must not be left
    # holding whatever the dormant filler weighed.
    def no_weight(slot):
        return a_character(slot, weight_lbs=None, first_name="Colby", last_name="Jackman")

    out = stamp_on_a_copy(no_weight)
    if isinstance(out, str):
        print(f"SKIP  {out}")
    else:
        want = growth.build_weight(out["wanted"]["height_inches"], out["wanted"]["build"])
        check("a character with no weight gets the one his build suggests",
              out["file"]["Weight"] == want,
              f'expected {want}, file says {out["file"]["Weight"]}')

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("the weight a person chose reaches league.dat and survives the write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
