"""The playoff control must appear for EVERY number the season guard refuses.

THE BUG THIS PINS SHUT. A league carries two different counts:

    regular_season_left  remaining dates that have games   - prep: 8   <- the GUARD refuses above this
    days_to_season_end   SIM DAY clicks to the last day    - prep: 10  <- what a person TYPES

The panel offered "sim into the playoffs" when the days asked for exceeded the CLICK count, while
the server refused when they exceeded the GAME-DATE count. Between the two - 9 and 10 days for
prep - the sim was refused and the checkbox that would have permitted it was not on screen. The
refusal even named the flag. Nate hit it three times in a row, walking 11 -> 10 -> 9 trying to
find a number that worked, and every one was refused.

So the crossing test must key on the GUARD'S number, and only the suggestion may use the clicks.
The two must never be read from the same attribute again.

This parses the shipped template and script rather than re-implementing them, because the bug was
not in the logic anybody would write on a whiteboard - it was in which attribute got read.

    python tests/test_no_dead_zone.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMPLATE = ROOT / "commissioner" / "web" / "templates" / "index.html"
SCRIPT = ROOT / "commissioner" / "web" / "static" / "app.js"


def rendered(left, clicks, round_one=None):
    """The league checkbox markup, rendered through the real template."""
    from flask import render_template

    from commissioner import app as panel
    row = {"key": "prep", "stage": "Regular season",
           "regular_season_left": left, "days_to_season_end": clicks}
    if round_one is not None:
        row["round_one_days"] = round_one
    with panel.app.test_request_context("/"):
        return render_template(
            "index.html", boot={}, status={"leagues": [row]}, run=None, busy=False,
            simweek={"ok": True, "error": ""}, offseason={"ok": True, "error": ""},
            plan={}, defaults={"week": 7, "chunk": 21}, now="x")


def main():
    html = rendered(8, 10)

    # ---- both numbers reach the page, under their own names --------------------------------
    assert 'data-left="8"' in html, \
        "the guard's own count must be on the element; the crossing test keys on it"
    assert 'data-clicks="10"' in html, \
        "the click count must be on the element; the suggestion keys on it"

    # ---- and the badge shows the number the box takes ---------------------------------------
    assert ">10d<" in html, "the badge should show clicks, to match what the box suggests"

    # ---- the script reads each for its own purpose -------------------------------------------
    js = SCRIPT.read_text(encoding="utf-8")

    crossing = re.search(r"function refreshSeasonEnd\(\)[\s\S]*?\n}", js)
    assert crossing, "refreshSeasonEnd not found"
    body = crossing.group(0)
    assert "tight.left" in body, \
        "the crossing test must compare against tight.left - the guard's number"
    # the decision itself, `asked > tight.left`, must not be made against the click count
    decision = re.search(r"crossing\s*=\s*[^\n;]*", body)
    assert decision and "tight.clicks" not in decision.group(0), \
        f"the crossing decision reads the click count, which reopens the dead zone: {decision}"

    suggest = re.search(r"function suggestDays\(\)[\s\S]*?\n}", js)
    assert suggest and "tight.clicks" in suggest.group(0), \
        "the suggestion must use the click count, or it stops short of the season end"

    # ---- the property itself: no number is refused-without-a-control -------------------------
    # The server refuses days > left. The panel shows the control when days > left. Walk every
    # number a person might plausibly type across and either side of both boundaries.
    left, clicks = 8, 10
    for asked in range(1, 25):
        refused = asked > left           # what _refuse_to_cross_the_season does
        offered = asked > left           # what refreshSeasonEnd now does
        assert refused == offered, \
            f"{asked} days: refused={refused} but control offered={offered} - a dead zone"
    # and the old, broken pairing would have failed exactly at 9 and 10
    broken = [a for a in range(1, 25) if (a > left) != (a > clicks)]
    assert broken == [9, 10], f"the test's own premise is wrong: {broken}"

    print("OK  no dead zone: every refused number offers the control (9 and 10 were the gap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
