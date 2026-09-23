"""A potential can pass 100, and the site, the database and the codec must agree on how far.

WHAT WENT WRONG. The site and the database's upgrade guard both capped every rating and every
potential at 100, and the site even said so in a comment: "Ratings are 0-100 here and in the save
file". The save file disagreed - the codec allows up to 150 and a real player file reaches 139 -
and on 2026-09-23 three of our characters were already past it: Tim Turner held a DReb potential
of 124 and an InsideScoring RATING of 107, Johnny Gartholomew a DReb potential of 123.

Tim's InsideScoring was frozen outright, because 107 is already over 100 and the guard refused
every step. Johnny could buy DReb only to 100 under a potential of 123.

THE RULE, from the live data: every rating above 100 belongs to one of the twelve that carry a
potential. None of the six without one ever passes 100. So a potential-bearing rating is capped by
its potential up to 150, a potential can be raised to 150, and the other six stay at 100.

THREE COPIES, which is the actual hazard: site/js/rules.js, the SQL guard (in schema.sql and in
the standalone migration), and the codec's RATING_MAX. If they disagree the site OFFERS a step the
database refuses, or the database accepts a value the codec will not write. This pins all three.

    python tests/test_potential_ceiling.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner.codec.league_dat import RATING_MAX as CODEC_MAX  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}{(': ' + detail) if detail else ''}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


def _guard(sql):
    """Just the upgrade guard's body, so a 100 elsewhere in the file cannot pass for it."""
    start = sql.index("create or replace function public.cv_upgrade_request_guard")
    return sql[start:sql.index("$fn$;", start)]


def run():
    rules = (ROOT / "site/js/rules.js").read_text(encoding="utf-8")
    schema = _guard((ROOT / "supabase/schema.sql").read_text(encoding="utf-8"))
    migration = _guard((ROOT / "supabase/potential_ceiling.sql").read_text(encoding="utf-8"))

    print("the site")
    m = re.search(r"export const POTENTIAL_MAX = (\d+);", rules)
    site_max = int(m.group(1)) if m else None
    check("declares POTENTIAL_MAX", site_max is not None)
    check("and it equals the codec's RATING_MAX", site_max == CODEC_MAX, f"{site_max} vs {CODEC_MAX}")
    check("a potential is raised to it", "return POTENTIAL_MAX;" in rules)
    check("a potential-bearing rating is capped by its potential up to it",
          "Math.min(POTENTIAL_MAX, Number.isFinite(pot)" in rules)
    # THE LIE THAT STARTED IT. A comment saying the save is 0-100 is how the ceiling got written
    # down wrong in the first place.
    check("no longer claims the save file is 0-100",
          "Ratings are 0-100 here and in the save file" not in rules)

    print("\nthe database guard")
    for label, body in (("schema.sql", schema), ("potential_ceiling.sql", migration)):
        check(f"{label}: a rating is capped by its potential up to {CODEC_MAX}",
              f"least({CODEC_MAX}, pot_eff)" in body)
        check(f"{label}: a potential is capped at {CODEC_MAX}",
              f"pot_eff + new.delta > {CODEC_MAX}" in body)
        # CODE ONLY. The guard's own comment quotes the old `least(100, ...)` to explain what it
        # replaced, so comments are stripped line by line first - `body.split("--")[0]` would have
        # kept only the text before the first comment and passed over the rest unread.
        code = chr(10).join(line.split("--")[0] for line in body.splitlines())
        check(f"{label}: the old 100 cap on potentials is gone",
              "least(100, pot_eff)" not in code and "pot_eff + new.delta > 100" not in code)
        # The six with no potential keep 100 - there is nothing in the data for them to grow into.
        check(f"{label}: ratings with no potential still default to 100",
              "ceiling      int := 100;" in body)

    print("\nthe migration IS the schema, so running it cannot revert anything newer")
    norm = lambda t: re.sub(r"\s+", " ", t).strip()
    check("the guard in the migration matches the guard in schema.sql", norm(schema) == norm(migration))

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print(f"OK  potential ceiling: site, database and codec all allow {CODEC_MAX}, a rating is capped "
          "by its own potential, and the six without one stay at 100")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
