"""Raise every character's Stamina to a floor, in the save, with a verified write.

WHY A FLOOR STICKS HERE, WHEN RAISING A RATING USUALLY DOES NOT
The game pulls a rating back down to its own potential, which is how every character's sheet
got crushed to a dormant filler's ceiling the first time potentials went unwritten. Stamina is
not subject to that: `POT_BY_RATING` has no entry for it, and neither does the save - the twelve
potentials cover the twelve skill ratings only. So Stamina can simply be set, and it stays set.
Do NOT copy this tool for JumpShot or Handling; those need their potential raised first or the
next sim takes the gain straight back.

WHY THE CHARACTERS NEED IT
Measured against CV_Prep's own 240 rostered players: median Stamina 65, lower quartile 52, upper
quartile 77, and only one man in 240 below 30. The characters sat at 19-34, which put all seven
of them in the bottom two percent of the league for conditioning - not a design choice anybody
made, just where the quiz's numbers happened to land.

WHY THE FLOOR IS 70 AND NOT 50
50 was the first proposal and it is the conservative one: it lands on the 18th percentile, still
inside the bottom quartile, and corrects the outlier without giving anybody anything. Nate chose
70 instead, which is the 59th percentile - above the league median, not merely out of the
cellar. That is a deliberate commissioner's decision to make the characters better conditioned
than the average Prep player, and it is worth stating plainly rather than filing under
"corrected an outlier". Pass --floor to use a different one.

WHAT IT DOES NOT CLAIM
It is not known to change how often the AI coach benches a character. The one character who
cannot hold a rotation spot does have the lowest Stamina of the group, but he also has the
group's second-highest minutes PER GAME, which is the opposite of what a conditioning limit
would look like. Treat any change in playing time as a thing to measure, not a thing promised.

Preview by default. Pass --apply to write.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch          # noqa: E402
from commissioner.codec.league_dat import LeagueDat  # noqa: E402

FLOOR = 70
FIELD = "Stamina"


def _game_is_running():
    """True if any FBPB3 process exists, or None if we could not tell.

    CONVENTIONS forbids touching league.dat under a running game: it holds the league in memory
    and rewrites the file whenever it saves, so a write made now is overwritten without warning
    and a read can catch a half-written file. Every other writer in this project waits for the
    process to be gone; so does this one.
    """
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return "FBPB3.exe" in out.stdout


def plan(key, characters, floor=FLOOR):
    """[(name, dob, was, now)] for the characters in `key` that are under the floor."""
    L = LeagueDat(ch.save_path(key))
    rows = []
    for c in characters:
        if c.get("league") != key or c.get("status") != "active":
            continue
        name = f'{c["first_name"]} {c["last_name"]}'
        # codec_dob, like every other place a birthday reaches the codec: game_dob arrives from
        # Postgres as 2015-11-27 and the save compares 11/27/2015.
        dob = ch.codec_dob(c.get("game_dob") or (c.get("claimed_slot") or {}).get("dob"))
        try:
            pl = L.find(name, dob)
        except Exception as exc:
            print(f"  ! {name}: not found in the save ({exc})")
            continue
        was = pl.values[FIELD]
        if was < floor:
            rows.append((name, dob, was, floor))
        else:
            print(f"    {name}: {FIELD} {was}, already at or above {floor}")
    return L, rows


def raise_stamina(key, characters, floor=FLOOR, apply=False):
    """Set Stamina to `floor` for every character below it. Returns the rows it touched."""
    L, rows = plan(key, characters, floor)
    for name, _dob, was, now in rows:
        print(f"    {name}: {FIELD} {was} -> {now}")
    if not rows or not apply:
        return rows

    for name, dob, _was, now in rows:
        ch.apply_deltas(L, name, dob, {FIELD: now - _was})
    # commit saves, re-reads and proves every value landed, so a failed write raises here rather
    # than being discovered on the website a week later.
    ch.commit(L, [(name, dob, {FIELD: now}) for name, dob, _was, now in rows])
    print(f"  wrote {len(rows)} change(s) to {key} and verified them")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--floor", type=int, default=FLOOR)
    ap.add_argument("--league", default="all")
    ap.add_argument("--apply", action="store_true", help="write; without it this only previews")
    ap.add_argument("--sync-store", action="store_true",
                    help="also refresh the website's copy now instead of at the next sim. This "
                         "adds one extra point to every career graph, out of the weekly cadence "
                         "the rest of them follow, so it is off by default.")
    args = ap.parse_args(argv)

    running = _game_is_running()
    if running:
        print("FBPB3 is running. It holds league.dat and rewrites it on its own save, so a "
              "write made now would be silently lost. Close the game (or let the sim finish) "
              "and run this again.")
        return 2
    if running is None:
        print("note: could not check whether FBPB3 is running; make sure it is closed.")

    from commissioner import store as st
    characters = st.characters()
    keys = ["prep", "college", "pro"] if args.league == "all" else [args.league]

    touched = {}
    for key in keys:
        print(f"{key}:")
        try:
            rows = raise_stamina(key, characters, args.floor, apply=args.apply)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            return 1
        if rows:
            touched[key] = rows

    if not args.apply:
        total = sum(len(r) for r in touched.values())
        print(f"\npreview only: {total} character(s) would change. Re-run with --apply to write.")
        return 0

    if args.sync_store and touched:
        from commissioner.simweek import _snapshot_league
        s = st.get_settings()
        season, week = int(s.get("current_season", 0)), int(s.get("current_week", 0))
        for key in touched:
            n = _snapshot_league(key, st, season, week, print)
            print(f"  refreshed {n} sheet(s) in {key}")
    elif touched:
        print("\nThe save is correct now. The website still shows the old number until the next "
              "sim writes its snapshots; pass --sync-store to refresh it immediately.")
    print(json.dumps({k: [[n, w, v] for n, _d, w, v in r] for k, r in touched.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
