"""A character who has declared for the draft must still be seen by the offseason.

`declare_for_draft` sets **status = 'declared'** and leaves the older `declared` flag alone. The
site shows the badge, the player page shows him on his college roster, and everything reads as
though it worked - because it did.

`movers()` was the one place in the codebase that treated anything but "active" as gone:

    if c.get("status") != "active":
        continue

So Dodger Manson fell straight through into NO bucket at all - not the draft, not staying,
nothing. An offseason would have moved all six of his team-mates and silently skipped him, and
the only symptom is a man who never comes up again. He would have sat in college for ever.

Every other reader already pairs the two - publish, seasonflow (four times), simweek, and
`promote` itself all test `in ("active", "declared")`. This file pins that `movers` does too, in
both directions: the declared man reaches the draft, and the people around him are unaffected.

    python tests/test_declared_moves.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import offseason  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def who(rows):
    return sorted(r["name"] for r in rows)


def _man(name, league, status="active", declared=False, years=0, born=2012):
    return {"name": name, "first_name": name, "last_name": "", "league": league,
            "status": status, "declared": declared, "college_years": years,
            "game_dob": f"1/1/{born}"}


def run():
    season = 2030

    print("the status the site actually sets is enough to enter the draft")
    people = [
        _man("Manson", "college", status="declared", born=2012),   # the real case
        _man("Turner", "college", born=2012),
        _man("Zimmer", "college", born=2012),
    ]
    m = offseason.movers(people, season)
    check("the declared man is in the draft", who(m["draft"]), ["Manson"])
    check("his team-mates stay", who(m["stay"]), ["Turner", "Zimmer"])
    check("nobody is lost", len(m["draft"]) + len(m["stay"]) + len(m["college"]), 3)

    print("the older flag still works, so neither route strands anybody")
    m = offseason.movers([_man("Flagged", "college", declared=True)], season)
    check("declared=True reaches the draft", who(m["draft"]), ["Flagged"])

    print("a declared PREP character is still promoted, not dropped")
    m = offseason.movers([_man("Kid", "prep", status="declared", born=2012)], season)
    check("he moves up to college", who(m["college"]), ["Kid"])

    print("used-up eligibility still enters the draft without declaring")
    m = offseason.movers([_man("Senior", "college", years=offseason.COLLEGE_MAX_YEARS)], season)
    check("four years is enough", who(m["draft"]), ["Senior"])

    print("and the statuses that really are gone stay gone")
    for status in ("retired", "pending"):
        m = offseason.movers([_man("Ghost", "college", status=status)], season)
        check(f"{status} is skipped",
              len(m["draft"]) + len(m["stay"]) + len(m["college"]), 0)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  declared moves: a declared character reaches the draft by status or by flag, a "
          "declared prep kid is still promoted, and retired and pending are still ignored")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
