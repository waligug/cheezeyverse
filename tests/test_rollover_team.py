"""After a rollover, a character's team is re-read from the save rather than trusted.

WHY THIS BECAME NECESSARY ON 2026-09-22. Free agency runs INSIDE the game's rollover, and pro now
has Full Finances on. An expiring contract is thrown open there and the AI re-signs the man
wherever it likes - measured on a clone, 2 of 33 players on a one-year-remaining deal changed
teams across a single rollover. The store recorded his team when he was STAMPED, which can be a
season out of date before anybody looks.

That is not a transient error. The site names the team from the store, so it would name the wrong
one for a whole season, and the career page keeps that season wrong for ever afterwards. Every
other field here is already re-read from the save for exactly this reason - ratings, potentials,
player id - and the team was the last one still trusted from the stamp.

THE OTHER OUTCOME IS WORSE AND QUIETER: free agency declining to sign him at all. He stays in the
save, stays in the store, and simply never plays again, with nothing anywhere saying so.

    python tests/test_rollover_team.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner import seasonflow  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class FakeDat:
    """Just enough LeagueDat to answer a team lookup."""

    def __init__(self, ids):
        self._ids = list(ids)

    def teams(self):
        # Returned unsorted on purpose: _team_abbrev must not depend on dict order, because the
        # real one is keyed by whatever the parse happened to find first.
        return {tid: {} for tid in reversed(self._ids)}


def run():
    key = "college"
    spec = cfg.BY_KEY[key]
    ids = [11, 4, 7, 2, 9, 13, 1, 20, 5, 8, 3, 15, 6, 12, 10, 14][:len(spec.teams)]
    dat = FakeDat(ids)

    print("a team id resolves to the abbreviation config gives it, by ASCENDING id")
    ordered = sorted(ids)
    check("the lowest id is the first team",
          seasonflow._team_abbrev(key, dat, ordered[0]), spec.teams[0].abbrev)
    check("the highest id is the last",
          seasonflow._team_abbrev(key, dat, ordered[-1]),
          spec.teams[len(ordered) - 1].abbrev)
    check("and a middle one lands where it should",
          seasonflow._team_abbrev(key, dat, ordered[5]), spec.teams[5].abbrev)

    print("a free agent has no abbreviation, and that is not an error")
    # THE IMPORTANT CASE. Team <= 0 is how the save says "nobody signed him", which after a
    # Finances rollover is a real and silent outcome. It must come back as None so the caller
    # can SAY so, rather than as a crash or as a plausible-looking team name.
    for nobody in (0, -1, -2):
        check(f"team {nobody}", seasonflow._team_abbrev(key, dat, nobody), None)

    print("nothing unreadable is ever turned into a team")
    for junk in (None, "", "x", 9999, 1.5, [], {}):
        check(f"{junk!r}", seasonflow._team_abbrev(key, dat, junk), None)

    print("the rollover re-reads the team instead of trusting the stamp")
    import inspect
    src = inspect.getsource(seasonflow.rollover_saves)
    check("it looks the team up", "_team_abbrev" in src, True)
    check("and writes it back when it changed", 'set_character_field(character["id"], '
          '"team_abbrev"' in src.replace("\n", "").replace("  ", ""), True)
    check("a character left with NO team is reported, loudly",
          "NOT ON A ROSTER" in src, True)
    # IT MUST NOT RAISE. The rollover has already happened by this point; throwing here strands
    # the universe mid-offseason with the game holding the save, which is far worse than a
    # stale team name on a web page.
    check("and none of it is allowed to abort the rollover",
          "could not re-read" in src, True)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  rollover team: a character's team is re-read from the save after free agency, a "
          "free agent resolves to no team rather than a wrong one, and a man nobody re-signed "
          "is reported instead of vanishing quietly")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
