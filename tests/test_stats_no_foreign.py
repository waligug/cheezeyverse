"""A character must not inherit the stats of the man whose row he was stamped onto.

WHAT WENT WRONG. Dodger Manson was drafted #1 into pro and had never played a pro minute. His
published page was clean. `site/leagues/pro/stats.json` credited him with 20 games, 60 minutes
and 11 points - Vicente Cowden's 2030 bench line, under Dodger's name, in a season Dodger was
not in the league for. Cowden had vanished from the file entirely. That row fed the cap board's
hover card and anything else reading stats.

WHY THE PAGE WAS CLEAN AND THE JSON WAS NOT. `fix_player_pages` ran against `dst`, the site
copy - but `_write_stats` and `_write_careers` do not read `dst`. They parse `src`, the game's
own html_output(), and nothing ever cleaned that. So the repair reached exactly one of the three
things built from those pages. Thirty foreign rows sat in the export page behind Dodger's clean
one, and every college character had 13-28 of the same.

THE FIX IS AN ORDER, NOT A FILTER. The export is cleaned FIRST, before restyle copies it, so
every downstream consumer reads pages that are already right. A filter in `_write_stats` would
have been a second implementation of a rule that already exists - and `league_stats` returns
season TOTALS, already aggregated, with no per-season rows left to filter.

    python tests/test_stats_no_foreign.py
"""
from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner.publish import publish as P  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


PAGE = """<html><body>
<table>
<tr><td>Season</td><td>G</td><td>PTS</td></tr>
<tr><td>2029</td><td>19</td><td>140</td></tr>
<tr><td>2030</td><td>20</td><td>11</td></tr>
<tr><td>2031</td><td>5</td><td>44</td></tr>
<tr><td>Career</td><td>44</td><td>195</td></tr>
</table>
<p>Dodger Manson</p>
</body></html>"""


def rows_of(html):
    """The year-led rows left on a page, as ints."""
    import re
    out = []
    for m in re.finditer(r"<tr[^>]*>.*?</tr>", html, re.S | re.I):
        cells = P._row_cells(m.group(0))
        if cells and P._YEAR_ROW.match(cells[0]):
            out.append(int(cells[0]))
    return out


def run():
    print("the export is cleaned BEFORE restyle copies it, and before stats are built from it")
    src = inspect.getsource(P.publish_league)
    at_src = src.find("fix_player_pages(key, src")
    at_restyle = src.find("pages = restyle(")
    at_stats = src.find("_write_stats(")
    at_dst = src.find("fix_player_pages(key, dst")
    check("the export itself gets repaired", at_src != -1,
          "publish_league never calls fix_player_pages on src - stats.json will carry the bleed")
    check("before restyle copies the pages", -1 < at_src < at_restyle, f"{at_src} vs {at_restyle}")
    # THIS IS THE ONE THAT BROKE. _write_stats parses `src`, so a repair that happens after it
    # has already written stats.json repairs nothing that anybody reads.
    check("and before stats.json is written from them", -1 < at_src < at_stats,
          f"{at_src} vs {at_stats}")
    check("the site copy is still repaired too, as a backstop", -1 < at_stats < at_dst,
          f"{at_stats} vs {at_dst}")

    print("fix_player_pages repairs whatever directory it is handed, not a hardcoded one")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "players").mkdir()
        page = root / "players" / "player250.htm"
        page.write_text(PAGE, encoding="latin-1")
        check("the fixture starts dirty", rows_of(PAGE) == [2029, 2030, 2031], str(rows_of(PAGE)))
        # No archived pro season for him, so `first` falls back to the season given: 2031.
        fixed = P.fix_player_pages("pro", root, 2031, characters=[{
            "league": "pro", "first_name": "Dodger", "last_name": "Manson",
            "game_dob": None, "league_player_ids": {"pro": 250}}])
        left = rows_of(page.read_text(encoding="latin-1"))
        check("it rewrote the file in place", fixed and fixed[0]["rows_dropped"] == 3, str(fixed))
        check("the previous owner's seasons are gone", 2029 not in left and 2030 not in left,
              str(left))
        check("his own season survives", left == [2031], str(left))
        # A CAREER TOTAL OF ROWS THAT ARE GONE IS WRONG, and a wrong total is worse than none.
        check("and the career total went with them",
              "Career" not in page.read_text(encoding="latin-1"))

        print("a second pass finds nothing - the repair is idempotent, which is what makes it "
              "safe to run against the game's own export")
        again = P.fix_player_pages("pro", root, 2031, characters=[{
            "league": "pro", "first_name": "Dodger", "last_name": "Manson",
            "game_dob": None, "league_player_ids": {"pro": 250}}])
        check("nothing left to drop", again == [], str(again))

    print("a stale id must not edit a stranger's page")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "players").mkdir()
        page = root / "players" / "player250.htm"
        page.write_text(PAGE.replace("Dodger Manson", "Vicente Cowden"), encoding="latin-1")
        before = page.read_text(encoding="latin-1")
        P.fix_player_pages("pro", root, 2031, characters=[{
            "league": "pro", "first_name": "Dodger", "last_name": "Manson",
            "game_dob": None, "league_player_ids": {"pro": 250}}])
        check("somebody else's page is left alone", page.read_text(encoding="latin-1") == before)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  stats carry no foreign history: the game's export is repaired before restyle "
          "copies it and before stats.json is built from it, the repair is idempotent, and it "
          "still refuses to touch a page that is not his")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
