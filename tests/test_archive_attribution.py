"""A season we did not archive at the time cannot be rebuilt from today's save.

FBPB3 keeps a player's season history on his ROW, and a row changes hands. A character is stamped
onto a reserve row when he is promoted - `characters.stamp_character` renames it and rewrites its
birthday - and the row's past seasons follow the new name.

After the 2029 rollover the live college MDB credited Chris Zimmer with 32 games in 2028, a season
he spent in prep, while the prep slot he vacated carried HIS 2029 scoring under the filler's name.
Both directions, on every promotion, for as long as the universe runs.

THE ARCHIVES ESCAPED IT, and the reason is worth stating because it is the whole defence: each
season was written while the rows still had the right names, and `save()` refuses to rewrite a
finished season. The hole that guard does NOT cover is a season the MDB still holds that we have
no archive for - a restored backup, a rebuilt machine, an archive that failed to write. There
`save()` sees no file, writes happily, and bakes the wrong attribution in forever.

So capture() now refuses to build an OLD season it has no archive for, and the gap stays a gap.
That is the right answer: the season is genuinely not recoverable from a save whose rows have
since been reassigned, and a hole in the record is better than a name that was not there.

    python tests/test_archive_attribution.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import statsarchive  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def _rows(name):
    return [{"name": name, "dob": "1/1/2012", "Games": 30, "Points": 500}]


def run():
    # The MDB holds four seasons. 2029 is the one being played; 2026-2028 are history.
    mdb_seasons = {2026: _rows("A"), 2027: _rows("B"), 2028: _rows("C"), 2029: _rows("D")}

    with tempfile.TemporaryDirectory() as d:
        archive = Path(d)
        mdb = archive / "LeagueOutput.mdb"
        mdb.write_bytes(b"\0")            # only its existence is checked

        with patch.object(statsarchive, "ARCHIVE", archive), \
             patch.object(statsarchive, "read_people", lambda *_a, **_k: {}), \
             patch.object(statsarchive, "read_mdb",
                          lambda _m, kind="stats", people=None: mdb_seasons if kind == "stats" else {}):

            print("a first capture writes everything it has")
            written = statsarchive.capture("prep", mdb, log=lambda m: None,
                                           overwrite_current=2029, kinds=("stats",))
            check("all four seasons archived", written, [2026, 2027, 2028, 2029])

            print("losing an old archive does NOT let the save rebuild it")
            statsarchive.path_for("prep", 2027, "stats").unlink()
            said = []
            written = statsarchive.capture("prep", mdb, log=said.append,
                                           overwrite_current=2029, kinds=("stats",))
            check("2027 was not rebuilt", 2027 in written, False)
            check("and it says why", any("changed hands" in m for m in said), True)
            check("the file is still missing", statsarchive.path_for("prep", 2027, "stats").exists(),
                  False)

            print("the seasons it still holds are untouched, and the current one keeps up")
            for season in (2026, 2028):
                body = json.loads(statsarchive.path_for("prep", season, "stats").read_text("utf-8"))
                check(f"{season} intact", body["players"][0]["name"],
                      {2026: "A", 2028: "C"}[season])
            check("2029 still rewritten each capture", 2029 in written, True)

            print("--force is still the way to repair a season written wrong")
            written = statsarchive.capture("prep", mdb, log=lambda m: None,
                                           overwrite_current=2029, force=True, kinds=("stats",))
            check("force rebuilds the gap", 2027 in written, True)

            print("a league with no archive at all is a first run, not a gap")
            for season in (2026, 2027, 2028, 2029):
                statsarchive.path_for("prep", season, "stats").unlink()
            written = statsarchive.capture("prep", mdb, log=lambda m: None,
                                           overwrite_current=2029, kinds=("stats",))
            check("everything archived again", written, [2026, 2027, 2028, 2029])

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  archive attribution: a missing old season is never rebuilt from a save whose rows "
          "have changed hands; held seasons and the current one are unaffected, and --force still "
          "repairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
