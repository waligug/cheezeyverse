"""The manifest has to name the bodies on the rosters, or the roster guard evicts them.

`tools/protect_rosters.py` defangs and evicts everybody `universe/manifest.json` does not name.
`ageout`'s intake recycles a free-agent body, renames it and signs it to a team - and nothing ever
told the manifest. So the next sim week saw a stranger on a roster, floored his ratings and threw
him off, and nothing refilled the hole. That is the loop that took a prep team down to six men,
and it is why a third of prep and college ended up rated 2 across the board.

THE TWO CASES THAT MUST NEVER BREAK are a reserve seat and a character. A seat is found by the
name and date the manifest records, so rewriting one loses somebody's placement; a character
lives on a seat under his own name. Both are in `keep`, and both have a test here.

    python tests/test_manifest_sync.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import ageout  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class _Player:
    def __init__(self, name, team, position=1):
        self.name = name
        self.values = {"Team": team, "Position": position,
                       "BirthMonth": 3, "BirthDay": 4, "BirthYear": 2012}

    @property
    def dob(self):
        return f'{self.values["BirthMonth"]}/{self.values["BirthDay"]}/{self.values["BirthYear"]}'


class _League:
    def __init__(self, players):
        self.players = list(players)


def _row(name, dob, role="filler", league="prep", team="SAS", uniform=7):
    return {"league": league, "team": team, "role": role, "name": name, "dob": dob,
            "position": "PG", "uniform": uniform}


def run():
    # team ids in the save map to the config's team table in ascending order
    first_team = 1
    dob = "3/4/2012"

    print("an unregistered body on a roster gets a filler row")
    rookie = _Player("Fresh Intake", first_team)
    L = _League([rookie])
    man = {"players": []}
    man, registered, dropped = ageout.sync_manifest(L, "prep", set(), man)
    check("registered", registered, ["Fresh Intake"])
    check("dropped", dropped, [])
    check("one row written", len(man["players"]), 1)
    check("it is a filler", man["players"][0]["role"], "filler")
    check("with his real date", man["players"][0]["dob"], dob)

    print("a row pointing at somebody who no longer exists is dropped")
    L = _League([_Player("Still Here", first_team)])
    man = {"players": [_row("Still Here", dob), _row("Recycled Away", "1/1/2009")]}
    man, registered, dropped = ageout.sync_manifest(L, "prep", set(), man)
    check("nothing newly registered", registered, [])
    check("the vanished row is dropped", dropped, ["Recycled Away"])
    check("the surviving row is kept", [r["name"] for r in man["players"]], ["Still Here"])

    print("a RESERVE seat is never rewritten, held or free")
    seat = _Player("Derek Uribe", first_team)          # unclaimed: still its generated name
    L = _League([seat])
    man = {"players": [_row("Derek Uribe", dob, role="reserve", uniform=15)]}
    man, registered, dropped = ageout.sync_manifest(L, "prep", {"Derek Uribe"}, man)
    check("the seat is not registered as a filler", registered, [])
    check("and not dropped", dropped, [])
    check("its row is untouched", man["players"][0]["role"], "reserve")
    check("including its uniform", man["players"][0]["uniform"], 15)

    print("a CHARACTER on a roster never becomes a filler row")
    hero = _Player("Dodger Manson", first_team)        # a claimed seat, renamed to him
    L = _League([hero])
    man = {"players": [_row("Lee Lords", "9/9/2011", role="reserve")]}
    man, registered, dropped = ageout.sync_manifest(L, "prep", {"Dodger Manson", "Lee Lords"}, man)
    check("the character is not registered", registered, [])
    check("his seat's row survives", [r["name"] for r in man["players"]], ["Lee Lords"])

    print("the free-agent pool is not the roster")
    L = _League([_Player("In The Pool", -1), _Player("On A Team", first_team)])
    man = {"players": []}
    man, registered, dropped = ageout.sync_manifest(L, "prep", set(), man)
    check("only the rostered body is registered", registered, ["On A Team"])

    print("another league's rows are left alone")
    L = _League([_Player("Prep Body", first_team)])
    man = {"players": [_row("College Body", dob, league="college"),
                       _row("Gone From Prep", "2/2/2010")]}
    man, registered, dropped = ageout.sync_manifest(L, "prep", set(), man)
    check("the college row survives", [r["name"] for r in man["players"]
                                       if r["league"] == "college"], ["College Body"])
    check("the stale prep row went", "Gone From Prep" in [r["name"] for r in man["players"]], False)

    print("an existing row keeps its uniform rather than being reset")
    body = _Player("Old Hand", first_team)
    L = _League([body])
    man = {"players": [_row("Old Hand", dob, uniform=23)]}
    man, _r, _d = ageout.sync_manifest(L, "prep", set(), man)
    check("uniform preserved", man["players"][0]["uniform"], 23)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  manifest sync: the rosters' bodies get filler rows, vanished rows are dropped, "
          "and reserve seats, characters, the pool and other leagues are all left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
