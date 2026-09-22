"""Show exactly what draft night will do. Writes nothing, posts nothing, touches no save.

WHY THIS EXISTS. The draft has never run on this universe. Neither had the offseason rollover
until the night it did, and it failed twice in paths that were tested but never exercised - once
because the panel was serving a stale module, once because a progress popup never cleared. The
cheapest defence against a third is a command that builds the real board from the real data and
prints it, so the first time anybody sees draft night is not the night it moves real people
between leagues.

It reads the store and, if it can, the pro save. It never calls promote(), never grants a point,
never opens Discord. The Discord text it prints is rendered by the same code that would post it,
so what you read here is what the channel gets.

    python tools/draft_preview.py              # whoever has actually declared
    python tools/draft_preview.py --mock 6     # six invented prospects, no store needed
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import draft, offseason, points  # noqa: E402
from commissioner import characters as ch  # noqa: E402


def _mock(n):
    """Invented prospects, for running this anywhere - no store, no save, no universe."""
    from commissioner.codec.league_dat import RATINGS
    out = []
    for i in range(n):
        now = 70 - i * 3
        out.append({
            "id": f"mock-{i}", "first_name": "Prospect", "last_name": chr(65 + i),
            "league": "college", "status": "declared",
            "position": ["C", "PG", "SF", "PF", "SG"][i % 5],
            "height_inches": 84 - i,
            "ratings": {f: now for f in RATINGS},
            # A deliberately uneven ceiling spread, so "upside" and "wins now" teams disagree.
            "potentials": {f: now + (20 if i % 2 else 3) for f in RATINGS},
        })
    return out


def main(argv):
    mock = 0
    if "--mock" in argv:
        i = argv.index("--mock")
        mock = int(argv[i + 1]) if len(argv) > i + 1 else 6

    if mock:
        declared, needs = _mock(mock), {}
        print(f"{mock} invented prospects; no store or save read.\n")
    else:
        from commissioner import simweek
        st = simweek.store()
        season = int(st.get_settings().get("current_season", 0))
        moving = offseason.movers(st.characters(), season)
        declared = moving["draft"]
        if not declared:
            print(f"Nobody is in the draft for {season}.")
            print("A college character enters it by declaring, or by using up four years.")
            for c in st.characters():
                if c.get("league") == "college":
                    print(f'   {c["first_name"]} {c["last_name"]:20} status={c.get("status")} '
                          f'declared={c.get("declared")} years={c.get("college_years")}')
            return 0
        needs = offseason._draft_needs(log=lambda m: print("  " + m))
        print(f"Season {season}: {len(declared)} in the draft.\n")

    order = offseason.draft_order()
    picks = draft.build_board(declared, order, needs=needs)

    print(f"Order, worst record first: {', '.join(order[:6])}...\n")
    print("THE BOARD")
    for p in picks:
        rate = points.rookie_rate(p["pick"])
        need = p.get("need") or "-"
        print(f'  #{p["pick"]:>2}  {p["team"]:<4} {draft.describe(p["character"]):<34} '
              f'{rate} pts/wk')
        print(f'        profile: {draft.PROFILES[p["profile"]]["label"]:<22} '
              f'their hole: {need}')
        print(f'        {p["reason"]}')
        print()

    print("WHAT DISCORD WOULD SEE")
    from commissioner import draftcast
    cast = draftcast.DraftCast(2030, transport=None, delay=0, log=lambda m: None)
    # No transport is configured under a preview, so nothing can leave the machine; render the
    # embeds by hand to show the words.
    for p in picks[:3]:
        contract = {"team": p["team"], "rate": points.rookie_rate(p["pick"]),
                    "years": offseason.ROOKIE_YEARS}
        print(f'  {p["team"]} are on the clock with pick #{p["pick"]}...')
        print(f'  #{p["pick"]} · {p["team"]} select {draft.describe(p["character"])}')
        print(f'      {p["reason"]}')
        print(f'      Rookie deal: {contract["years"]} yr · {contract["rate"]} '
              "skill points a week")
        print()
    if len(picks) > 3:
        print(f"  ...and {len(picks) - 3} more.")
    cast.finish()

    print("\nNothing was written. No save was opened for writing, no point granted, "
          "no message posted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
