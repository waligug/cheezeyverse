"""A player page must not carry the career of whoever held the row before him.

FBPB3 keeps a player's history on his ROW, and a character is stamped onto a reserve row that
already existed - `characters.stamp_character` renames it and rewrites its birthday, and the row's
past follows the new name. One day into college, Johnny Gartholomew's page listed 19 games in
2028, 5 in 2029, and TWO Cheezeyverse College Championships he did not win. He was in prep.

The save cannot be corrected from here - that history lives in league.dat structures the codec
does not model - but the PUBLISHED page is ours, and a page crediting somebody with another man's
championships is worse than a page with a gap in it.

WHY THE "Career" ROW GOES TOO. It totals the rows that were there, so once any of them are gone
it is simply wrong, and a missing total is honest where a wrong one is not. Same for
"Career Highs" and "Total - Championships", which are not year-led and so survive the first pass.

    python tests/test_player_page_history.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner.publish.publish import strip_foreign_history  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def _row(*cells):
    return "<tr>" + "".join(f"<td class='main'>{c}</td>" for c in cells) + "</tr>"


PAGE = ("<html><body><table>"
        + _row("Season Averages")
        + _row("Season", "LGE", "TEAM", "G")
        + _row("2028", "CVC", "MJW", "19")
        + _row("2029", "CVC", "MJW", "5")
        + _row("2030", "CVC", "MJW", "8")
        + _row("Career", "", "", "32")
        + _row("Awards")
        + _row("2027", "Cheezeyverse College Champion")
        + _row("2028", "Cheezeyverse College Champion")
        + _row("Total", "Championships: 2")
        + _row("Career Highs")
        + _row("Career", "4", "3", "7")
        + "</table></body></html>")


def run():
    print("a man with a real season of his own keeps it, and loses the rest")
    out, dropped = strip_foreign_history(PAGE, 2030)
    check("2030 kept", "2030" in out, True)
    check("2028 dropped", "2028" in out, False)
    check("2029 dropped", "2029" in out, False)
    check("2027 award dropped", "2027" in out, False)
    check("no championships claimed", "Championship" in out, False)
    # The table was edited, so its total is a total of rows that are no longer there.
    check("the wrong Career total went with them", "32" in out, False)
    check("something was dropped", dropped > 0, True)

    print("a man with NO season of his own loses the whole career block")
    out, _ = strip_foreign_history(PAGE, 2031)
    for gone in ("2028", "2029", "2030", "Championship", "Career Highs"):
        check(f"{gone} gone", gone in out, False)
    check("the page itself survives", "<html>" in out and "</html>" in out, True)

    print("a page with nothing foreign on it is returned untouched")
    out, dropped = strip_foreign_history(PAGE, 2027)
    check("nothing dropped", dropped, 0)
    check("byte-for-byte the same", out == PAGE, True)
    check("its Career total is left alone", "32" in out, True)
    check("and its championships stand", "Championships: 2" in out, True)

    print("a page with no rows at all does not explode")
    out, dropped = strip_foreign_history("<html><body>nothing here</body></html>", 2030)
    check("returned unchanged", out, "<html><body>nothing here</body></html>")
    check("nothing dropped", dropped, 0)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  player page history: seasons, awards and totals from before a man joined the "
          "league are dropped, his own are kept, and a clean page is untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
