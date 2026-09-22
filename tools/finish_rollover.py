"""Finish an offseason that died at the game-side rollover, WITHOUT re-running what happened.

WHY THIS EXISTS. The 2029 rollover got all the way through `_run_offseason` - the characters were
promoted into the college save, the season bonuses and the promotion grant were paid, the age-out
ran and wrote `universe/manifest.json` - and then died in `seasonflow.rollover_saves`, the
game-side END SEASON, with:

    END SEASON at (794, 552) was readable the whole time, but a progress popup never cleared
    within 15s. No further click was sent.

That was `_progress_popup_visible` counting any visible ThunderRT6 window as a progress form
(fixed in 507ac0e02). The damage was not the failure, it was the SHAPE of the failure: the store
had moved and the saves had not.

RUNNING THE OFFSEASON AGAIN IS THE WRONG REPAIR. Growth is cumulative and leaves no trace - a
second pass grows everybody another inch and re-pays every bonus, and nothing in the save could
tell afterwards that it happened twice. So this does only the tail of `run_offseason`: the game
rollover, the settings advance, the opening-day snapshots, clearing the journal, and the publish.

IT REFUSES unless the journal is actually parked at the rollover phase, which is the one state
this is a correct repair for. Check before running that the failure was on the FIRST league - if
a later one failed, some saves have already rolled and `rollover_saves` would take them round
twice.

    python tools/finish_rollover.py            # finish it
    python tools/finish_rollover.py --check     # say what it would do, touch nothing
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import seasonflow, simweek  # noqa: E402
from commissioner.codec.league_dat import LeagueDat  # noqa: E402
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402
from commissioner.saveguard import SAVE_LOCK  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402
from commissioner import characters as ch  # noqa: E402


def main(argv):
    check_only = "--check" in argv
    log = print

    journal = simweek.interrupted_run()
    if not journal:
        return "there is no interrupted run; nothing to finish."
    if journal.get("kind") != "offseason" or journal.get("phase") != "rollover":
        return (f"the journal is a {journal.get('kind')!r} at phase {journal.get('phase')!r}, not "
                "an offseason parked at 'rollover'. This tool is only a repair for that one state.")

    season = int(journal.get("season") or 0)
    if not season:
        return "the journal has no season; refusing to guess."

    st = simweek.store()
    settings = st.get_settings()
    if int(settings.get("current_season", 0)) != season:
        return (f"the store is on season {settings.get('current_season')} but the journal is for "
                f"{season}; the rollover may already have finished. Refusing.")

    # Every save must still be on the season that is ending. If one has already rolled, the run
    # got further than this tool assumes and taking it round again would lose a year.
    for spec in cfg.LEAGUES:
        stamp = LeagueDat(ch.save_path(spec.key)).season_day()
        if int(stamp[1]) != season:
            return (f"{spec.key} is already on season {stamp[1]}, not {season} - part of the "
                    "rollover has run. Refusing; this needs a person.")
        log(f"  {spec.key}: season {stamp[1]}, day {stamp[0]} - not yet rolled")

    if FBPB3.is_running():
        return "FBPB3 is running; close it first."

    log(f"\nwould finish the {season} offseason: game rollover for all three leagues, "
        f"settings to {season + 1}, opening-day snapshots, clear the journal, publish.")
    if check_only:
        return 0

    if not SAVE_LOCK.acquire(blocking=False):
        return "a sim, offseason or backup is using the saves; try again when it finishes."
    try:
        log("\n>>> rollover_saves: advancing all three games")
        result = seasonflow.rollover_saves(st, season, simweek, log)
        log(f">>> rolled: {result}")

        log(">>> settings")
        st.set_setting("last_offseason", season)
        st.set_setting("current_season", season + 1)
        st.set_setting("current_week", 0)

        log(">>> opening-day snapshots")
        for key in ("prep", "college", "pro"):
            simweek._snapshot_league(key, st, season + 1, 0, log)

        simweek._clear_marker()
        log(">>> journal cleared")
    finally:
        SAVE_LOCK.release()

    log("\n>>> publishing the new season")
    from commissioner.publish.publish import publish, git_push
    publish([s.key for s in cfg.LEAGUES])
    try:
        git_push(f"Season {season + 1} opening")
        log(">>> site deployed")
    except Exception as exc:                                            # noqa: BLE001
        log(f">>> the season is saved, but the site did NOT publish ({exc}); retry the publish")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
