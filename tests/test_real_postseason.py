"""The season bonus reads a REAL finished postseason correctly.

Every other season-bonus test runs on pages written by hand, and hand-written pages are how three
silent bugs got past them: they held what we expected FBPB3 to print, not what it prints. These
five pages are the game's own export from the season-end rehearsal on 2026-09-19, a throwaway
copy of CV_Prep simmed from 3/23/2027 through the playoffs to 6/21/2027 (see
C:\\claude\\season-end-rehearsal-2026-09-19.md on SERVERPC). They are committed byte-for-byte,
and .gitattributes marks the folder -text so git cannot rewrite their CRLFs either.

What that season decided, read off the game's own bracket screen at the time:
  * eight teams qualified, the top four of each conference, first-round losers included;
  * the Derricks beat the Kings 2-0 in the final.

Also pinned here: the two page shapes that made the old parsers wrong, so a future "simpler"
reader has to face them. champs.htm names the LOSER on the champion's row, and the asterisks on
playoffstandings.htm mark four division winners, not the eight qualifiers.

    python tests/test_real_postseason.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import seasonbonus as sb  # noqa: E402

FIXTURE = ROOT / "fixtures/html-output/cv-rehearsal-2026-09-19"
PAGES = ["playoffs.htm", "champs.htm", "awards.htm", "seasonawards.htm", "playoffstandings.htm"]
QUALIFIERS = {"Tulips", "Spirits", "Derricks", "Clams", "Generals", "Kings", "Boulders", "Berries"}
DIVISION_WINNERS = {"Generals", "Berries", "Tulips", "Derricks"}


def main():
    missing = [p for p in PAGES if not (FIXTURE / p).exists()]
    if missing:
        print(f"SKIP  real postseason: {', '.join(missing)} missing from "
              f"{FIXTURE.relative_to(ROOT)} (a sparse or partial checkout)")
        return 0

    # The bytes are still the game's. If anything reflowed them - an editor, a line-ending
    # setting, somebody "cleaning up" a fixture - the rest of this file would be testing a tidied
    # copy, which is precisely how the &#160; bug hid behind a passing test.
    raw = (FIXTURE / "playoffs.htm").read_bytes()
    assert b"&#160;" in raw, "playoffs.htm lost the game's &#160; separators"
    for page in PAGES:
        data = (FIXTURE / page).read_bytes()
        bare_lf = data.count(b"\n") - data.count(b"\r\n")
        assert bare_lf == 0, f"{page} has {bare_lf} bare LF line endings; FBPB3 writes CRLF only"

    qualifiers, champ = sb.playoff_bracket(FIXTURE)
    assert qualifiers == QUALIFIERS, f"qualifiers {sorted(qualifiers or [])}"
    assert champ == "Derricks", f"champion {champ!r}"
    assert sb.playoff_teams(FIXTURE) == QUALIFIERS
    assert sb.champion(FIXTURE) == "Derricks"

    # The traps, on the real pages. Name-on-the-page picks up the loser...
    assert "Kings" in sb._text(FIXTURE / "champs.htm"), "champs.htm no longer names the finalist"
    # ...and the asterisks are division winners, half the field.
    starred = set(re.findall(r"&nbsp;\s*\*\s*([A-Za-z]+)", sb._text(FIXTURE / "playoffstandings.htm")))
    assert starred == DIVISION_WINNERS, f"asterisks now mark {sorted(starred)}"

    # Player of the Month is the other award that pays, and this is its only real page.
    awards = sb.award_counts(FIXTURE)
    assert sum(v["potm"] for v in awards.values()) == 24
    assert awards.get("Dodger Manson", {}).get("potm") == 1

    print("OK  real postseason: 8 qualifiers, Derricks champions, both old traps still present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
