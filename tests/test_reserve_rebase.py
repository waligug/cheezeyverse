"""An unclaimed reserve seat gets its youth back; a seat somebody holds is never touched.

A reserve row is the seat a new signup is stamped into. `protected_names` keeps it off every
release list so the next person to join still has one - and for a long time that was read as "do
not touch it at all", so nothing ever reset its age. The seats aged with the universe: measured
against the live prep save for season 2030, NINETEEN of prep's forty-eight were already 19 in a
league that ends at 18, sitting on rosters and playing.

The part that made it worth fixing rather than filing is `characters.stamp_character`: a new
character takes THE BIRTHDAY OF THE SEAT HE CLAIMS. The next kid to sign up for prep would have
started his career already too old for the league he was joining, and nothing in the signup flow
would have said a word about it.

THE TEST THAT MATTERS MOST HERE is `claimed seats are untouched`. The rule is that a claimed row
has been RENAMED to the character's name, so it no longer answers to the manifest's generated
name - which is what makes "can I still find it?" a safe test for "is this seat empty?". If that
ever stops being true, this file should fail loudly rather than let a rollover rewrite a real
person's birthday.

    python tests/test_reserve_rebase.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import ageout  # noqa: E402
from commissioner.codec.league_dat import CodecError  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class _Player:
    def __init__(self, name, month, day, year, exp=5):
        self.name = name
        self.values = {"BirthMonth": month, "BirthDay": day, "BirthYear": year, "Exp": exp}

    @property
    def dob(self):
        return f'{self.values["BirthMonth"]}/{self.values["BirthDay"]}/{self.values["BirthYear"]}'


class _League:
    """Just enough LeagueDat: find by (name, dob) exactly as the codec compares them."""

    def __init__(self, players):
        self.players = list(players)

    def find(self, name, dob):
        for p in self.players:
            if p.name == name and p.dob == dob:
                return p
        raise CodecError(f"no {name} born {dob}")

    def set(self, player, field, value):
        player.values[field] = value

    def rename_many(self, pairs):
        # The real one splices and re-parses; the only contract this test needs is that the
        # name changes and the row is afterwards found under the NEW name.
        for player, first, last in list(pairs):
            player.name = f"{first} {last}"


def _manifest(rows):
    return {"start_year": 2026, "players": rows}


def _row(league, name, dob, role="reserve", team="SAS"):
    return {"league": league, "team": team, "role": role, "name": name, "dob": dob,
            "position": "PG", "uniform": 15}


def _rebase(league, manifest, season, cap, intake):
    """Run the real _rebase_reserves against a temp manifest."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "manifest.json"
        path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        with patch.object(ageout, "MANIFEST", path):
            return ageout._rebase_reserves(league, "prep", season, cap, intake)


def run():
    # prep: cap 19, intake 14. Season 2030 -> a re-based seat is born 2016.
    cap, intake, season = ageout.AGE_CAPS["prep"], ageout.INTAKE_AGE["prep"], 2030

    print("an unclaimed seat at the cap is made young again")
    over = _Player("Nurcan Kelleher", 7, 11, 2011)          # 19 in 2030
    under = _Player("Bret Fenner", 11, 24, 2014)            # 16 in 2030, fine
    league = _League([over, under])
    manifest = _manifest([_row("prep", "Nurcan Kelleher", "7/11/2011"),
                          _row("prep", "Bret Fenner", "11/24/2014")])
    rebased, out = _rebase(league, manifest, season, cap, intake)

    check("one seat re-based", len(rebased), 1)
    check("it was the over-age one", rebased[0]["name"], "Nurcan Kelleher")
    check("his age before", rebased[0]["age_was"], 19)
    check("his new birth year", over.values["BirthYear"], season - intake)
    check("he is now the intake age", season - over.values["BirthYear"], intake)
    check("the day and month are kept", (over.values["BirthMonth"], over.values["BirthDay"]),
          (7, 11))
    check("the seat under the cap is untouched", under.values["BirthYear"], 2014)
    # EXPERIENCE IS A FUNCTION OF AGE. stamp_character never writes Exp, so whatever is left on
    # the seat is what the next signup inherits - a 14-year-old with five seasons behind him.
    check("his experience was reset with his age", over.values["Exp"], 0)
    check("the untouched seat keeps its experience", under.values["Exp"], 5)

    print("the manifest is moved with the save, or the seat becomes unclaimable")
    rows = {r["name"]: r["dob"] for r in out["players"]}
    # THE NAME MUST NOT MOVE. protected_names, characters.free_slots and test_age_out all
    # identify a seat by name, so renaming it reads as the seat having been recycled away.
    check("the seat keeps its name", "Nurcan Kelleher" in rows, True)
    check("the manifest date matches the save exactly", rows["Nurcan Kelleher"], over.dob)
    check("it is the intake year", rows["Nurcan Kelleher"], f"7/11/{season - intake}")
    check("the name in the save is unchanged too", over.name, "Nurcan Kelleher")
    check("the untouched seat keeps its date", rows["Bret Fenner"], "11/24/2014")

    print("a seat somebody HOLDS is never touched")
    # stamp_character renames the row, so the manifest's generated name no longer finds it.
    held = _Player("Dodger Manson", 3, 2, 2011)             # 19, but a real character
    league = _League([held])
    manifest = _manifest([_row("prep", "Nurcan Kelleher", "3/2/2011")])
    rebased, out = _rebase(league, manifest, season, cap, intake)
    check("nothing was re-based", len(rebased), 0)
    check("the character's birth year is untouched", held.values["BirthYear"], 2011)
    check("and his manifest row is untouched",
          (out["players"][0]["name"], out["players"][0]["dob"]),
          ("Nurcan Kelleher", "3/2/2011"))
    check("his name was not rewritten either", held.name, "Dodger Manson")

    print("only reserve rows, and only this league")
    filler = _Player("Old Filler", 1, 1, 2011)              # 19, but a filler, not a seat
    other = _Player("College Seat", 1, 1, 2007)             # 23, but college
    league = _League([filler, other])
    manifest = _manifest([_row("prep", "Old Filler", "1/1/2011", role="filler"),
                          _row("college", "College Seat", "1/1/2007")])
    rebased, _out = _rebase(league, manifest, season, cap, intake)
    check("an over-age FILLER is not a seat", len(rebased), 0)
    check("the filler keeps his year", filler.values["BirthYear"], 2011)
    check("another league's seat is not ours to move", other.values["BirthYear"], 2007)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  reserve seats: an unclaimed one is re-aged at the cap and the manifest moves with "
          "it; a claimed one, a filler and another league's seat are all left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
