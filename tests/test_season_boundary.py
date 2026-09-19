"""A sim must not be able to run past the last day of the regular season by accident.

NOTHING IN THIS PROJECT HAS EVER CROSSED THAT LINE. `sim_days` clicks SIM DAY blind - no stage
check, no dialog check between clicks - and `offseason.py` moves everybody on by editing
league.dat through the codec, without ever driving FBPB3 through its own playoffs or rollover.
So what the game does on the day after the last one is unknown: a modal that eats every later
click, playoff games, its own aging and re-signing. Any of those is FBPB3 taking over a rollover
that `offseason.py` owns, on the live universe, with seven real people in it.

THREE THINGS THIS TEST EXISTS TO CATCH, all of which the first version of the guard got wrong:

  * THE DATE FORMAT BELONGS TO THE MACHINE. FBPB3 is VB6 and renders the Windows short date, so
    the same code exports `2030-10-15` on one box and `10/20/2026` on another. The first guard
    parsed `%m/%d/%Y`, which was read off SERVERPC's published pages, and would have been
    silently inert on any machine set the other way - including this desktop. Both formats are
    fixtured here and must give the same answer.
  * THE LOCK. `run_sim` takes `_SIM_LOCK` before its `try`, and the `finally` is the only thing
    that releases it. The first guard raised above that `try`, so one refusal held the lock for
    the life of the process - and `offseason.py` deliberately shares it, so a mistyped number
    would have bricked the rollover too.
  * THE DRY RUN. It never launches the game and never clicks anything, and it is the natural way
    to ask what a long run would do. Refusing it defeats the only safe way to find out.

The fixture is written here rather than copied: `fixtures/saves/` is gitignored and SERVERPC is
a clone, and SERVERPC is the only machine that can actually run a sim.

    python tests/test_season_boundary.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch  # noqa: E402
from commissioner import simweek  # noqa: E402


def _row(date, played, n):
    """One date block, shaped like the real export: a nested table, a box-score link if played.

    A played row in the raw export is
        <td class=main>&nbsp;<a class=linkmain href=./boxes/box15-1.htm>@Tulips 46, Berries 31</a>
    and an unplayed one carries no link and no score.
    """
    inner = (f'<a class=linkmain href=./boxes/box{n}-1.htm>@Tulips 46, Berries 31</a>'
             if played else 'Miners @ Berries')
    return (f'<table width=250><tr><td class=main>&nbsp;{date}</td></tr>'
            f'<tr><td class=main>&nbsp;{inner}</td></tr></table>')


def _schedule(played_to, last, iso):
    dates = ([f"2031-03-{d:02d}" for d in range(1, last + 1)] if iso
             else [f"3/{d}/2031" for d in range(1, last + 1)])
    rows = "".join(_row(d, i < played_to, i) for i, d in enumerate(dates))
    return ("<html><body>"
            "<table><tr><td>Preseason</td></tr></table>"
            f"<table><tr><td>Regular Season</td></tr></table>{rows}"
            "<table><tr><td>Playoffs</td></tr></table>"
            "</body></html>")


def _universe(tmp, played_to, last, iso=False, keys=("prep", "college", "pro"), champion=None):
    for key in keys:
        d = Path(tmp) / key / "html"
        d.mkdir(parents=True, exist_ok=True)
        (d / "schedule.htm").write_text(_schedule(played_to, last, iso), encoding="latin-1")
        if champion:
            # the real page's shape, entities and all: each series prints BOTH teams with their
            # series wins, and the champion is simply the one that won the most of them
            (d / "playoffs.htm").write_text(
                "<html><body>2026 Playoff Brackets 1st Round Conference Finals League Finals"
                f" #1 &#160; {champion} 1 &#160; #4 &#160; Spirits 0"
                f" #1 &#160; {champion} 1 &#160; #3 &#160; Clams 0"
                f" #1 &#160; {champion} 2 &#160; #4 &#160; Kings 0"
                " #2 &#160; Boulders 0 &#160; #3 &#160; Berries 1"
                " #3 &#160; Berries 0 &#160; #4 &#160; Kings 1"
                " #1 &#160; Generals 0 &#160; #4 &#160; Kings 1</body></html>",
                encoding="latin-1")
    return Path(tmp)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="boundary-"))
    real = ch.save_path
    real_post = None
    real_store = None
    try:
        # ---- the same season in both date formats must give the same answer -----------------
        for iso in (False, True):
            root = _universe(Path(tempfile.mkdtemp(prefix="fmt-")), played_to=20, last=31, iso=iso)
            got = simweek._regular_season_left(root / "prep")
            assert got == 11, f"{'ISO' if iso else 'M/D/Y'} export gave {got}, expected 11"
            shutil.rmtree(root, ignore_errors=True)

        root = _universe(tmp, played_to=20, last=31)
        ch.save_path = lambda key: root / key / "league.dat"
        simweek.ch.save_path = ch.save_path
        keys = ["prep", "college", "pro"]

        for days in (1, 7, 10, 11):
            simweek._refuse_to_cross_the_season(keys, days, lambda *a, **k: None)
        for days in (12, 28, 35):
            try:
                simweek._refuse_to_cross_the_season(keys, days, lambda *a, **k: None)
            except simweek.SeasonEnd as exc:
                assert "11 more day" in str(exc), exc
                # and it must point at the way through, not just say no: the playoffs are a
                # thing people legitimately want, and a refusal with no route reads as broken
                assert "into the playoffs" in str(exc), exc
            else:
                raise AssertionError(f"{days} days was allowed past the end of the season")

        # ---- INTO THE PLAYOFFS. The flag is the deliberate way across ------------------------
        # The rehearsal on a copy settled what is on the other side: no dialog at the boundary,
        # SIM DAY plays playoff games one a day, and sim_days stops by itself when the calendar
        # stops moving, which is what the 6/21 offseason panel looks like.
        said = []
        for days in (12, 28, 35):
            simweek._refuse_to_cross_the_season(keys, days, lambda *a, **k: said.append(a),
                                                allow_season_end=True)
        assert said, "crossing into the playoffs said nothing about crossing"
        assert "into the playoffs" in said[0][1], said[0]
        assert "prep" in said[0][1], "it must name the league whose season ends mid-run"
        # a run that does NOT reach the boundary says nothing extra, flag or no flag
        quiet = []
        simweek._refuse_to_cross_the_season(keys, 3, lambda *a, **k: quiet.append(a),
                                            allow_season_end=True)
        assert not quiet, quiet

        # ---- A DECIDED SEASON IS REFUSED, FLAG OR NO FLAG -----------------------------------
        # After the final the only thing left is FBPB3's own rollover behind END SEASON, and
        # offseason.py does the rollover through the codec. Nothing has ever run both.
        won = _universe(Path(tempfile.mkdtemp(prefix="won-")), played_to=31, last=31,
                        champion="Derricks")
        ch.save_path = lambda key: won / key / "league.dat"
        simweek.ch.save_path = ch.save_path
        for flag in (False, True):
            try:
                simweek._refuse_to_cross_the_season(["prep"], 1, lambda *a, **k: None,
                                                    allow_season_end=flag)
            except simweek.SeasonEnd as exc:
                assert "Derricks" in str(exc) and "rollover" in str(exc), exc
            else:
                raise AssertionError(f"a finished season was allowed to sim (flag={flag})")
        assert simweek._champion(won / "prep") == "Derricks", "the champion must come off the bracket"
        shutil.rmtree(won, ignore_errors=True)
        ch.save_path = lambda key: root / key / "league.dat"
        simweek.ch.save_path = ch.save_path

        # ---- the tightest league decides, because a run sims all three ----------------------
        (root / "college" / "html" / "schedule.htm").write_text(
            _schedule(played_to=28, last=31, iso=False), encoding="latin-1")
        try:
            simweek._refuse_to_cross_the_season(keys, 5, lambda *a, **k: None)
        except simweek.SeasonEnd as exc:
            assert "college" in str(exc), exc
        else:
            raise AssertionError("the league with least room did not stop the run")
        simweek._refuse_to_cross_the_season(keys, 3, lambda *a, **k: None)

        # ---- a season with nothing left says so, instead of "sim 0 days or fewer" -----------
        done = _universe(Path(tempfile.mkdtemp(prefix="done-")), played_to=31, last=31,
                         keys=("prep",))
        assert simweek._regular_season_left(done / "prep") == 0
        ch.save_path = lambda key: done / key / "league.dat"
        simweek.ch.save_path = ch.save_path
        try:
            simweek._refuse_to_cross_the_season(["prep"], 1, lambda *a, **k: None)
        except simweek.SeasonEnd as exc:
            assert "no days left" in str(exc) and "0 days or fewer" not in str(exc), exc
        else:
            raise AssertionError("a finished season allowed another day")
        shutil.rmtree(done, ignore_errors=True)
        ch.save_path = lambda key: root / key / "league.dat"
        simweek.ch.save_path = ch.save_path

        # ---- no opinion beats a wrong opinion, and it must SAY so ---------------------------
        assert simweek._regular_season_left(root / "nothing-here") is None
        blank = _universe(Path(tempfile.mkdtemp(prefix="blank-")), played_to=0, last=31)
        assert simweek._regular_season_left(blank / "prep") is None, "unplayed season blocked a sim"
        said = []
        ch.save_path = lambda key: blank / key / "league.dat"
        simweek.ch.save_path = ch.save_path
        simweek._refuse_to_cross_the_season(keys, 99, lambda *a, **k: said.append(a))
        assert said, "the guard went blind and said nothing"
        shutil.rmtree(blank, ignore_errors=True)
        ch.save_path = lambda key: root / key / "league.dat"
        simweek.ch.save_path = ch.save_path

        # ---- THE LOCK. One refusal must not brick the panel and the offseason with it -------
        # Stub record_run FIRST, for the same reason the notifier is stubbed below: this block
        # calls run_sim for real, and run_sim's `finally` logs every attempt - including a
        # refused one - to universe/sim_runs.json, which is the LIVE run log. Running this suite
        # had already filled that file with 999-day failures: on SERVERPC 18 of the newest 20
        # entries were this test's, and on the desktop all 39 were. That log is not decoration -
        # head-to-head derives each character's debut from it, so a test polluting it silently
        # rewrote who owned which games.
        real_store = simweek.store

        class _FakeStore:
            """Closed, not a proxy. A forwarding stub still let every OTHER call through to the
            live Supabase project with the service key - including the settings read run_sim
            does - so the test reached a real service by any route it had not thought of."""

            def get_settings(self):
                return {"current_season": 2026, "current_week": 0, "auto_publish": False}

            def characters(self, **_k):
                return []

            def pending_characters(self):
                return []

            def record_run(self, *_a, **_k):
                return None

            def __getattr__(self, name):
                raise AssertionError(f"the test reached the store: {name}()")

        simweek.store = lambda: _FakeStore()

        # Stub the GAME and the live backups folder. run_sim's first act is to close a stray
        # FBPB3 - taskkill /IM FBPB3.exe /F - so running this suite while the panel was mid-sim
        # would have killed a real week in progress, the panel being a separate process with its
        # own lock. The backup step then mkdir'd into the real backups/, which is where ten empty
        # 20260919-*-CV_Prep folders came from.
        real_fbpb3, real_backups = simweek.FBPB3, simweek.BACKUPS

        class _NoGame:
            @staticmethod
            def is_running():
                return False

            @staticmethod
            def kill():
                raise AssertionError("the test tried to kill FBPB3")

            def __init__(self, *_a, **_k):
                raise AssertionError("the test tried to launch FBPB3")

        simweek.FBPB3 = _NoGame
        simweek.BACKUPS = Path(tmp) / "backups"

        # Stub the notifier too. This block calls run_sim for real, and on any machine with a
        # webhook configured - SERVERPC has one - the failure path posted "Sim stopped - 999
        # days would run past..." into the live Discord server. A test must never be able to
        # reach a real service, and the assertion below pins the other half of that fix: a
        # refusal must not announce itself at all, because nothing ran.
        posted = []
        real_post = simweek.notify.post
        simweek.notify.post = lambda text, log=None: posted.append(text)

        assert not simweek._SIM_LOCK.locked(), "the lock was already held before the test"
        try:
            simweek.run_sim(leagues=["prep"], days=999)
        except simweek.SeasonEnd:
            pass
        else:
            raise AssertionError("run_sim allowed 999 days")
        assert not simweek._SIM_LOCK.locked(), "a refusal leaked _SIM_LOCK; the panel is bricked"
        assert not simweek._RUNNING.get("active"), "a refusal left _RUNNING active"

        # ---- a dry run asks what WOULD happen; it must not be refused -----------------------
        try:
            simweek.run_sim(leagues=["prep"], days=999, dry_run=True)
        except simweek.SeasonEnd:
            raise AssertionError("the dry run was refused; it never clicks anything")
        except Exception:
            pass          # anything else (no store, no saves) is not this test's business
        assert not simweek._SIM_LOCK.locked(), "the dry run leaked _SIM_LOCK"

        assert not posted, f"a refused run talked to Discord: {posted}"

        # and the escape hatch has to exist, or the day the path IS designed this is in its way
        import inspect
        assert "allow_season_end" in inspect.signature(simweek.run_sim).parameters
    finally:
        # in the finally, not after the assertions: a failure above must not leave the real
        # notifier replaced by a stub for whatever runs next in the same process
        if real_post is not None:
            simweek.notify.post = real_post
        if real_store is not None:
            simweek.store = real_store
        simweek.FBPB3, simweek.BACKUPS = real_fbpb3, real_backups
        ch.save_path = real
        simweek.ch.save_path = real
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK  season boundary: both date formats, the tightest league, and the lock survives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
