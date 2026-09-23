"""A drafted character comes out of the rollover on the team that drafted him, on his deal.

WHAT WENT WRONG. Gravy Jones was drafted #3 by THP in the 2031 offseason on a three-year rookie
deal. The game's own rollover ran free agency straight afterwards and released him: traced
through the offseason's save backups, he went in on THP with the deal and came out Team -1 with
his contract ERASED - not shortened. Dodger Manson made the identical trip a year earlier and
kept both, so it is not the draft path.

The offseason noticed, and logged "He needs placing by hand". He was then placed by hand - onto
the wrong team, because pro's team ids run 5..24 and the script counted from 1 - and with the
wrong term, three years instead of the two the deal had left. Nate's rule, stated 2026-09-22:
"He needs to play his rookie year for the team that drafted him, add that in 100%."

So the rollover now puts him back itself, and this pins the three shapes of it on a COPY of the
live pro save: released outright, released into a roster that has since filled, and re-signed by
somebody else.

    python tests/test_rookie_survives_rollover.py

ON PRO THE LIVE PATH REHEARSES in FBPB3 first (seasonflow._place_rehearsed, pinned in
test_rollover_recovery). This pins the placement itself, `_place_character`, on file copies
that are never loaded, so the codec's 20-team refusal is lifted with rehearsed_writes().
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch, seasonflow  # noqa: E402
from commissioner.codec.league_dat import LeagueDat, rehearsed_writes  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}{(': ' + detail) if detail else ''}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


def _maps(L):
    ids = sorted(L.teams())
    teams = cfg.BY_KEY["pro"].teams
    return ({t: teams[i].abbrev for i, t in enumerate(ids)},
            {teams[i].abbrev: t for i, t in enumerate(ids)})


def run():
    if not ch.save_path("pro").exists():
        print("SKIP  no live pro save on this machine")
        return 0
    from commissioner.simweek import store
    st = store()
    rookie = None
    season = None
    for c in st.characters(league="pro"):
        for entry in reversed(list(c.get("level_history") or [])):
            deal = entry.get("contract") if isinstance(entry, dict) else None
            if entry.get("level") == "pro" and entry.get("to_season") is None and deal:
                s = int(deal["season_from"])
                if int(deal.get("game_years") or 0) - 1 > 0:
                    rookie, season = c, s
                break
        if rookie:
            break
    if rookie is None:
        print("SKIP  no pro character on a live rookie deal to test with")
        return 0
    name = f'{rookie["first_name"]} {rookie["last_name"]}'
    dob = ch.codec_dob(rookie.get("game_dob"))
    deal = [e for e in rookie["level_history"]
            if e.get("level") == "pro" and e.get("to_season") is None][0]["contract"]
    left = int(deal["game_years"]) - (season + 1 - int(deal["season_from"]))
    want = [int(deal["salary"])] * left
    protected = {f'{c["first_name"]} {c["last_name"]}' for c in st.characters()}
    print(f"{name}: drafted by {deal['team']}, {deal['game_years']} game years from "
          f"{deal['season_from']}, so {left} left after the {season} rollover")

    def fresh():
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "league.dat"
        shutil.copy2(ch.save_path("pro"), path)
        return path

    def after(path):
        L = LeagueDat(path)
        abbr, _ = _maps(L)
        p = L.find(name, dob)
        return abbr.get(p.values["Team"]), [x for x in L.contract_of(p) if x], L

    print("\nreleased outright, contract erased - exactly what the rollover did")
    path = fresh()
    L = LeagueDat(path); L.release(L.find(name, dob)); L.save()
    L = LeagueDat(path); L.set_contract(L.find(name, dob), [0]); L.save()
    seasonflow._place_character("pro", path, rookie, name, dob, season, st, lambda m: None)
    team, contract, L = after(path)
    check("back on the team that drafted him", team == deal["team"], str(team))
    check("on the years the deal has LEFT, not its full term", contract == want,
          f"{contract} vs {want}")

    print("\nreleased, and the drafting team has filled its spot since")
    path = fresh()
    L = LeagueDat(path); L.release(L.find(name, dob)); L.save()
    L = LeagueDat(path); _, id_of = _maps(L)
    spare = next(p for p in L.players if p.values["Team"] == -1 and p.name not in protected)
    L.sign(spare, id_of[deal["team"]]); L.save()
    L = LeagueDat(path)
    full = sum(1 for p in L.players if p.values["Team"] == id_of[deal["team"]])
    seasonflow._place_character("pro", path, rookie, name, dob, season, st, lambda m: None)
    team, contract, L = after(path)
    _, id_of = _maps(L)
    size = sum(1 for p in L.players if p.values["Team"] == id_of[deal["team"]])
    check("he still gets his spot", team == deal["team"], str(team))
    # Not against config's 15: with Finances on the game's own free agency fills pro rosters to
    # 18-20. What placing him must never do is GROW the roster - somebody makes room.
    check("the roster did not grow to fit him", size <= full, f"{size} vs {full} before")
    released = [n for n in protected
                if any(p.name == n and p.values["Team"] < 1 for p in L.players)]
    # THE ONE THING IT MUST NEVER DO to make room.
    check("no character was released to make room", released == [], str(released))

    print("\nre-signed by the wrong team in free agency")
    path = fresh()
    L = LeagueDat(path); L.release(L.find(name, dob)); L.save()
    L = LeagueDat(path); _, id_of = _maps(L)
    other = next(a for a in id_of if a != deal["team"])
    L.sign(L.find(name, dob), id_of[other]); L.save()
    seasonflow._place_character("pro", path, rookie, name, dob, season, st, lambda m: None)
    team, contract, L = after(path)
    check(f"moved off {other} and back to the drafting team", team == deal["team"], str(team))

    print("\nnothing counts team ids from 1")
    import inspect
    src = inspect.getsource(seasonflow._place_character)
    # Pro's ids run 5..24. `enumerate(spec.teams, start=1)` put Gravy on the Curds.
    check("resolves teams by ascending save id", "sorted(L.teams())" in src)
    check("and not by counting from one", "start=1" not in src)

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  rookie survives the rollover: released, displaced or re-signed elsewhere, he comes "
          "back to the drafting team on the years his deal has left, and no character is ever "
          "released to make room")
    return 0


def main():
    with rehearsed_writes():
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
