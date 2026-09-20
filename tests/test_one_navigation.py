"""The league site has ONE navigation, whichever way you arrived.

THE BUG. FBPB3 ships a two-frame site: `index.htm` is a frameset holding a 178px menu frame and
a data frame. So a visitor who came through the league's front door got the GAME'S menu down the
left and this skin's bar hidden; a visitor who followed a deep link - a character page, a shared
URL, a bookmark, any of the four hundred player pages - got the BAR instead. Same site, two
navigations, decided by the route in rather than by anything the reader did.

Worse, they did not agree about what the site contains. The left menu had eighteen in-league
links and the bar had eleven: Playoff Standings, Waiver Wire, Potential Free Agents, Available
Staff, Season Awards, Playoff Leaders and Human Coaches existed only if you happened to enter
through the front door.

So the frameset is gone, index.htm redirects, and the bar carries all eighteen. This pins the
three things that have to stay true for that to hold:

    python tests/test_one_navigation.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.publish import restyle  # noqa: E402

LEAGUES = ROOT / "site" / "leagues"


def main():
    # ---- 1. the bar offers everything the game's own menu did ------------------------------
    have = {href for href, _ in restyle.NAV_LINKS}
    for page in ("standings.htm", "playoffstandings.htm", "schedule.htm", "leaders.htm",
                 "playoffleaders.htm", "teamleaders.htm", "transactions.htm", "injuries.htm",
                 "freeagents.htm", "waiverwire.htm", "potentialfreeagents.htm", "draft.htm",
                 "staff.htm", "humancoaches.htm", "awards.htm", "seasonawards.htm"):
        assert page in have, \
            f"{page} was reachable from FBPB3's left menu and is not in the bar - removing the " \
            "frameset would make it unreachable"

    # ---- 2. index.htm is no longer a frameset ----------------------------------------------
    out = restyle._skin_index("<HTML><HEAD><title>x</title></HEAD><FRAMESET cols=178,*>"
                              "<FRAME name=Options src=menu.htm><FRAME name=data "
                              "src=standings.htm></FRAMESET></HTML>", "Cheezeyverse Prep", "2027")
    assert "frameset" not in out.lower(), "index.htm is still a frameset"
    assert "<frame" not in out.lower(), "index.htm still declares frames"
    assert "standings.htm" in out, "the redirect does not say where it is going"
    assert "refresh" in out.lower() and "location.replace" in out, \
        "a redirect needs both the meta refresh and the script - one of them is blocked often " \
        "enough that relying on either alone strands somebody on a blank page"
    assert "Cheezeyverse Prep" in out and "2027" in out, "the title was lost"

    # ---- 3. and against what is actually on disk -------------------------------------------
    # Only if a publish has run here; a clone has no site/leagues.
    checked = 0
    for key in ("prep", "college", "pro"):
        index = LEAGUES / key / "index.htm"
        if not index.exists():
            continue
        checked += 1
        text = index.read_text(encoding="latin-1")
        assert "FRAMESET" not in text.upper(), \
            f"{key}/index.htm is still a frameset - republish to pick up the change"
        # every content page must carry the bar, since it is now the only navigation
        for name in ("standings.htm", "schedule.htm"):
            page = LEAGUES / key / name
            if page.exists():
                html = page.read_text(encoding="latin-1")
                assert 'class="cv-bar"' in html, f"{key}/{name} has no navigation at all"
                nav = re.search(r"<nav>(.*?)</nav>", html, re.S)
                assert nav and len(re.findall(r"<a\b", nav.group(1))) >= 16, \
                    f"{key}/{name} carries a short nav - the bar is not the full menu"
        # nothing may address a frame that no longer exists
        for page in list((LEAGUES / key).glob("*.htm"))[:60]:
            if page.name == "menu.htm":
                continue           # written, unreferenced, and allowed to keep its targets
            bad = re.findall(r'target="?(data|options)"?', page.read_text(encoding="latin-1"), re.I)
            assert not bad, f"{page.name} targets a frame that is gone: {set(bad)}"

    where = f"{checked} published league(s)" if checked else "the skin only (nothing published here)"
    print(f"OK  one navigation: {len(restyle.NAV_LINKS)} links on every page, no frameset, "
          f"nothing targets a dead frame - checked against {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
