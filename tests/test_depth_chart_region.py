"""Every league's depth-chart region must be locatable, including one that has had free agency.

WHAT WENT WRONG. `LeagueDat.teams()` finds the depth-chart region by scoring candidates: each
non-zero id in a block should belong to the team whose block it is. A stale id was tolerated -
somebody released or retired since the chart was last rebuilt - but an id now on ANOTHER team was
treated as fatal, disqualifying the whole region on the spot.

That rule assumed a stale entry means a player who LEFT THE LEAGUE. Free agency breaks the
assumption: it moves players BETWEEN teams. So the first 2031 sim after Full Finances went live in
pro logged

    post-sim tidy failed for pro (depth-chart region: nothing scored above 0.95 (best 0.000))
    could not dress Dodger Manson: ...

and `_dress_characters` - the thing that puts a character back on the chart after the AI coach
benches him, every single week - could not run for the only league a character had just been
drafted into. Prep and college have no free agency and never saw it.

Measured on the live save: 104 of 4721 depth entries (2.2%) pointed at another team. A wrong-team
id now costs a point instead of disqualifying, and the 0.95 threshold is what keeps the region
honest. The margin is not close - the true region scores 0.978, the best impostor 0.000.

WHY THIS TEST AND NOT test_codec.py: that one SKIPS on SERVERPC, because `fixtures/saves/` is
gitignored and empty here. This reads the live saves, which is the only codec coverage that
actually runs on the machine that ships changes.

    python tests/test_depth_chart_region.py
"""
from __future__ import annotations

import shutil
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch  # noqa: E402
from commissioner.codec.league_dat import CodecError, LeagueDat  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}{(': ' + detail) if detail else ''}")
    else:
        FAILS.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


def run():
    keys = [s.key for s in cfg.LEAGUES]
    missing = [k for k in keys if not ch.save_path(k).exists()]
    if missing:
        print(f"SKIP  live saves not present: {', '.join(missing)}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        print("every league can locate its depth chart - a copy, never the live save")
        charts = {}
        for key in keys:
            dst = Path(tmp) / f"{key}.dat"
            shutil.copy2(ch.save_path(key), dst)
            try:
                charts[key] = (dst, LeagueDat(dst).teams())
            except CodecError as exc:
                # THE FAILURE THIS TEST EXISTS FOR. Reported by league, because it struck exactly
                # one of the three and a suite that only says "codec error" hides that.
                check(f"{key}: teams() resolves", False, str(exc))
                continue
            check(f"{key}: teams() resolves", True, f"{len(charts[key][1])} teams")

        print("\nthe region is believed only when nearly every entry is right")
        for key, (dst, teams) in charts.items():
            data = dst.read_bytes()
            owner = {}
            L = LeagueDat(dst)
            for pl in L.players:
                if pl.values["Team"] >= 1:
                    owner[pl.id] = pl.values["Team"]
            hits = total = strangers = 0
            for team, info in teams.items():
                for q in info["depth_at"]:
                    for v in struct.unpack_from("<80h", data, q + 2):
                        if not v:
                            continue
                        total += 1
                        if owner.get(v) == team:
                            hits += 1
                        elif owner.get(v) is not None:
                            strangers += 1
            score = hits / total if total else 0
            check(f"{key}: the located region scores above 0.95", score >= 0.95,
                  f"{score:.3f} ({hits}/{total}, {strangers} now on another team)")

        print("\na character stays dressed across a save and a fresh parse")
        # The whole point of locating the region. Done on the copy, and only for a league that
        # actually has one of our people in it.
        from commissioner.simweek import store
        done = 0
        for key, (dst, _teams) in charts.items():
            people = [c for c in store().characters(league=key) if c.get("status") == "active"]
            if not people:
                continue
            L = LeagueDat(dst)
            # A file copy never loaded by the game: the 20-team refusal protects the LIVE pro
            # save's loadability, and pro's real path (seasonflow.dress_rehearsed) lifts it the
            # same way after a clone has loaded and simmed.
            from commissioner.codec.league_dat import rehearsed_writes
            with rehearsed_writes():
                for c in people:
                    name = f'{c["first_name"]} {c["last_name"]}'
                    pl = L.find(name, ch.codec_dob(c.get("game_dob")))
                    L.dress(pl)
            L.save()
            again = LeagueDat(dst)
            teams2 = again.teams()
            for c in people:
                name = f'{c["first_name"]} {c["last_name"]}'
                pl = again.find(name, ch.codec_dob(c.get("game_dob")))
                team = pl.values["Team"]
                if team < 1:
                    check(f"{key}: {name} is on a roster", False, f"Team={team}")
                    continue
                blob = dst.read_bytes()
                info = teams2[team]
                on_chart = any(pl.id in struct.unpack_from("<80h", blob, q + 2)
                               for q in info["depth_at"])
                check(f"{key}: {name} survives a save on the depth chart", on_chart)
                done += 1
        check("at least one character was actually checked", done > 0, f"{done} checked")

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  depth chart: every league's region is locatable, scores above the 0.95 threshold "
          "even where free agency has moved players between teams, and a character stays dressed "
          "across a save and a fresh parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
