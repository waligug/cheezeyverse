"""Tell the manifest who is actually on the rosters, and undo the defanging it caused.

WHY THIS EXISTS. `universe/manifest.json` is the list of bodies the universe calls its own, and
`tools/protect_rosters.py` defangs and evicts everybody it does not name. Nothing ever added
`ageout`'s intake to it: the age-out recycles a free-agent body, renames it and signs it to a
team, and the manifest never hears about it. So the next roster guard saw a stranger on a roster
and threw it off - which is what emptied the rosters down to a six-man team in prep - and floored
its ratings on the way past.

Two consequences, and this repairs both:

  * The guard finds the same strangers EVERY sim week, releases and re-signs them, and rewrites a
    5-10 MB save per league for no change at all. Registering them makes it short-circuit.
  * A third of prep and college was rated 2 across the board. A league of floor-rated bodies is
    not a league - the AI population exists so a character has somebody to face.

Bodies that still have real ratings are left exactly as they are. Characters and reserve seats
are never touched: both are protected, and the filler rows this works from exclude them.

Run it with the game closed and nothing simming:

    python tools/reconcile_manifest.py              # all three leagues
    python tools/reconcile_manifest.py prep         # one
    python tools/reconcile_manifest.py --dry-run    # say what would change, write nothing
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import ageout  # noqa: E402
from commissioner import characters as ch  # noqa: E402
from commissioner.saveguard import SAVE_LOCK  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402


def main(argv):
    keys = [a for a in argv if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]
    dry = "--dry-run" in argv

    # The same refusal protect_rosters makes, and for the same reason: a claimed reserve row
    # answers to a name the manifest does not know, so with no characters it looks like a
    # stranger and would be registered as an ordinary filler - which is a deleted character.
    from commissioner import simweek
    try:
        chars = simweek.store().characters()
    except Exception as exc:                                        # noqa: BLE001
        return (f"cannot read the characters from the store ({exc}). Refusing to run: without "
                "them a claimed reserve row looks like a stranger.")
    if not chars:
        return ("the store reports no characters. Refusing, for the same reason - pass them or "
                "fix the store first.")
    print(f"{len(chars)} characters read from the store")

    if ageout.FBPB3.is_running() if hasattr(ageout, "FBPB3") else False:
        return "close FBPB3 first."
    if not SAVE_LOCK.acquire(blocking=False):
        return "a sim, offseason or backup is using the saves; try again when it is finished."
    try:
        for key in keys:
            if dry:
                # Work on a throwaway copy so a dry run cannot write the save, and pass
                # save_path so the one manifest is left alone.
                tmp = Path(tempfile.mkdtemp()) / "league.dat"
                shutil.copy2(ch.save_path(key), tmp)
                ageout.reconcile(key, save_path=tmp, store=_Store(chars))
                shutil.rmtree(tmp.parent, ignore_errors=True)
            else:
                ageout.reconcile(key, store=_Store(chars))
    finally:
        SAVE_LOCK.release()
    return 0


class _Store:
    """ageout.protected_names only ever calls .characters(league=...)."""

    def __init__(self, characters):
        self._chars = characters

    def characters(self, league=None, **_kw):
        return [c for c in self._chars if league is None or c.get("league") == league]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
