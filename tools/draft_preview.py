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
    # THE REAL EMBED BUILDER, not a hand-written imitation of it. An earlier version of this
    # printed its own approximation of the words, which meant the code that actually runs on
    # draft night was the one thing the preview did not exercise - a preview that agrees with
    # itself and not with the broadcast is worse than none. `Capture` satisfies the transport
    # contract and keeps every payload, so nothing can leave the machine and what is printed
    # below came out of draftcast.
    from commissioner import draftcast

    class Capture:
        def __init__(self):
            self.payloads = []
            self.message_id = None

        def send(self, payload):
            self.payloads.append(payload)

        def close(self):
            pass

    seen = Capture()
    cast = draftcast.DraftCast(season if not mock else 2030, transport=seen, delay=0,
                               log=lambda m: None, sleep=lambda s: None)
    cast.open(len(picks), order)
    shown = picks[:3]
    for p in shown:
        cast.on_the_clock(p)
        cast.pick(p, contract={"team": p["team"], "rate": points.rookie_rate(p["pick"]),
                               "years": offseason.ROOKIE_YEARS})
    # The closing board lists EVERY pick, the way the real broadcast does - only the pick-by-pick
    # messages above are trimmed, because three is enough to read the shape of them.
    cast.close(picks)
    cast.finish()

    for payload in seen.payloads:
        for embed in payload.get("embeds", []):
            if embed.get("author"):
                print(f'  [{embed["author"]["name"]}]')
            if embed.get("title"):
                print(f'  {embed["title"]}')
            for line in (embed.get("description") or "").splitlines():
                print(f"      {line}")
            for field in embed.get("fields") or []:
                print(f'      {field["name"]}: {field["value"]}')
            print()
    if len(picks) > len(shown):
        more = len(picks) - len(shown)
        print(f'  ...and {more} more pick{"" if more == 1 else "s"} would be announced the '
              "same way.")
    print(f"  ({len(seen.payloads)} messages captured, 0 sent - the transport is a list.)")

    print("\nNothing was written. No save was opened for writing, no point granted, "
          "no message posted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
