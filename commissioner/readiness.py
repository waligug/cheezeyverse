"""One place that answers "is it safe to do this right now", so nothing has to be remembered.

WHY THIS EXISTS. The preconditions for a run were real but scattered: the lock in
`app._acquire_or_refuse`, the interrupted run inside `simweek.run_sim`, the season boundary in
`_refuse_to_cross_the_season`, the save's health in `tools/verify_save.py`. Each one is checked by
whichever caller happens to remember it, and one of them was not checked at all.

THE CHECK THIS MODULE EXISTS FOR is `postseason is finished`, and it has two halves because the
first version only had one.

NOT EXPORTED. `seasonbonus.playoff_bracket` declines to hand last year's bracket to this year's
settlement, which is correct - the export keeps the old answer on disk until a new one replaces
it. But the decline was INVISIBLE: the playoff and title rows simply never appeared, so an
offseason run before the postseason was published paid nobody for either, in every league, with
nothing saying a thing was missing.

NOT FINISHED, which is the dangerous one. `playoff_bracket` returns `qualifiers or None, None`
the moment ANY series is undecided - so a NON-empty bracket with no champion is exactly what a
final sitting at 1-0 looks like. Testing `bracket is None` alone waved that through, while
`seasonflow._readiness` refused the same save with "finish the playoffs first" - two gates, one
question, opposite answers, and this was the one that said go. A rollover on an undecided final
is what the monotonic per-round clinch in `seasonbonus.playoff_bracket` exists to catch; a gate
that passes the state that function refuses to score is worse than no gate. Both halves refuse
now, and the second borrows seasonflow's exact words so the two cannot drift apart again.

Nothing here is new logic. Every row calls something that already existed; the contribution is
that they are in one list, in one order, with one answer.

    python -m commissioner.readiness --kind sim
    python -m commissioner.readiness --kind offseason
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from . import characters as ch
from . import simweek
from .universe import config as cfg

KINDS = ("sim", "offseason")


@dataclass(frozen=True)
class Row:
    ok: bool
    name: str
    detail: str = ""
    fix: str = ""


def _lock_is_free():
    """Take the lock and hand it straight back. Racy by nature, and that is fine.

    A readiness check is advice about a moment, not a reservation - `run_sim` takes the lock for
    real and refuses on its own if somebody got there first. Holding it here would mean a
    readiness check could itself block the run it was asked about.
    """
    if simweek._SIM_LOCK.acquire(blocking=False):
        simweek._SIM_LOCK.release()
        return True
    return False


def _season_of(settings):
    try:
        return int((settings or {}).get("current_season"))
    except (TypeError, ValueError):
        return None


def _shared(rows, kind):
    """What stops any run - plus the two that depend on which kind it is."""
    # ONE read, not two. Calling it twice meant the verdict and the reason came from separate
    # moments, so a refusal could print with no reason attached - or worse, a pass with one.
    free = _lock_is_free()
    rows.append(Row(free, "nothing else is running",
                    "" if free else "a sim or offseason is using the saves right now",
                    "wait for it to finish"))

    # A LIVE RUN IS NOT AN INTERRUPTED ONE, and telling them apart matters more than it looks.
    # `interrupted_run()` is file presence and nothing else - no pid, no heartbeat - and
    # `_mark_running` writes that file at the START of a healthy run, clearing it only on
    # success. Every previous caller read it before it was written, or under the lock. This is
    # the first to read it at an arbitrary moment, so during an ordinary panel Sim Week it saw
    # the marker and reported "A sim started at <time> never finished ... before clearing
    # universe/run_in_progress.json" - an instruction that, followed, deletes a RUNNING sim's
    # journal and kills the week at its next _update_marker.
    #
    # The save lock is the discriminator and it is OS-backed (saveguard: msvcrt.locking /
    # flock on a permanent file), so the operating system drops it when the holder exits,
    # crash included. Marker plus a held lock is a run in flight; marker plus a free lock is a
    # run whose process is gone.
    state = simweek.interrupted_run()
    if state and not free:
        rows.append(Row(True, "no interrupted run to reconcile",
                        "a run is in flight right now - its journal is supposed to be there"))
    else:
        rows.append(Row(not state, "no interrupted run to reconcile",
                        simweek.describe_interruption(state) if state else "",
                        "reconcile the saves, then clear universe/run_in_progress.json"))

    try:
        from .driver.fbpb3 import FBPB3
        running = FBPB3.is_running()
    except Exception as exc:                                            # noqa: BLE001
        rows.append(Row(True, "FBPB3 is closed", f"could not tell ({exc}); assuming it is"))
        return
    # A STRAY GAME STOPS AN OFFSEASON AND NOT A SIM, because `run_sim` already closes one
    # itself ("closing a stray FBPB3 first", simweek.py:1216). Refusing a sim on it meant one
    # leftover process - the 0xC000013A kill the panel's own supervisor exists for - would make
    # every unattended run refuse for ever, since nothing unattended will ever close the game.
    if kind == "sim":
        rows.append(Row(True, "FBPB3 is closed",
                        "the game is open; the run will close it first" if running else ""))
    else:
        rows.append(Row(not running, "FBPB3 is closed",
                        "the game is open; it holds league.dat" if running else "",
                        "close FBPB3"))


def _sim_rows(rows, keys):
    """Is there regular season left to sim, in each league we would touch."""
    for key in keys:
        try:
            left = simweek._regular_season_left(ch.save_path(key).parent)
        except Exception as exc:                                        # noqa: BLE001
            rows.append(Row(True, f"{key}: season boundary readable",
                            f"could not read it ({exc}); the run will guard itself"))
            continue
        if left is None:
            # NOT a failure. `_refuse_to_cross_the_season` says out loud that it cannot guard a
            # league whose boundary it cannot read, and carries on - so neither should this
            # refuse. Saying nothing is the only outcome that would be wrong.
            rows.append(Row(True, f"{key}: season boundary readable",
                            "cannot be read from the export; this league will not be guarded"))
        elif left > 0:
            rows.append(Row(True, f"{key}: regular season has days left", f"{left} day(s)"))
        else:
            rows.append(Row(True, f"{key}: regular season is over",
                            "the playoffs are what comes next - start the run 'into the "
                            "playoffs' rather than a plain week"))


def _offseason_rows(rows, keys, season):
    """The one that used to be a log line nobody had to read."""
    if season is None:
        rows.append(Row(False, "the season is known",
                        "current_season is unset or unreadable in settings",
                        "set current_season before rolling over"))
        return
    for key in keys:
        html = ch.save_path(key).parent / "html"
        if not html.exists():
            rows.append(Row(False, f"{key}: export exists", f"no html directory at {html}",
                            "publish this league before rolling over"))
            continue
        rounds = cfg.BY_KEY[key].playoff_rounds
        try:
            # LAZY, and deliberately. app.py imports this beside simweek inside one try/except;
            # at module level a syntax error in seasonbonus would take Sim Week, approvals,
            # character creation and run history down together under a banner blaming simweek.
            # simweek imports it lazily for the same reason.
            from . import seasonbonus
            bracket, champion = seasonbonus.playoff_bracket(html, season, rounds)
        except Exception as exc:                                        # noqa: BLE001
            rows.append(Row(False, f"{key}: {season} postseason is finished",
                            f"could not read the bracket ({exc})",
                            "sim the postseason and publish"))
            continue
        if bracket is None:
            rows.append(Row(
                False, f"{key}: {season} postseason is finished",
                f"this export holds no {season} bracket, so NOBODY in {key} can be paid for "
                f"making the playoffs or winning the league",
                "sim the postseason and publish, then run this again"))
        elif not champion:
            # AN UNDECIDED POSTSEASON. `playoff_bracket` returns `qualifiers or None, None` the
            # moment ANY series is undecided - so a NON-empty bracket with no champion is exactly
            # what a final sitting at 1-0 looks like. Reading only `bracket is None` passed it,
            # while seasonflow._readiness refused the same save with "finish the playoffs first".
            # Two gates, one question, opposite answers, and this was the one saying go.
            rows.append(Row(
                False, f"{key}: {season} postseason is finished",
                f"{len(bracket)} team(s) qualified but no series has decided it - the "
                f"postseason is still being played",
                "finish the playoffs first"))
        else:
            rows.append(Row(True, f"{key}: {season} postseason is finished",
                            f"{len(bracket)} team(s) qualified, {champion} won it"))


def check(kind, keys=None, settings=None, store=None, season=None):
    """[Row] for `kind`, in the order a person would want to read them.

    `store` is only read for the season; pass `settings` instead to check without touching
    Supabase, which is what the tests do.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    # `is None`, not `or`: an explicitly empty list means "check no leagues, just the shared
    # rows", and `keys or [...]` quietly turned that into all three of them.
    keys = [spec.key for spec in cfg.LEAGUES] if keys is None else list(keys)
    rows: list[Row] = []
    # A LEAGUE THAT DOES NOT EXIST IS NOT A PASS. `--league colege` used to raise a raw KeyError
    # out of the offseason path, and on the sim path the blanket except turned it into ok=True -
    # so a typo printed "YES. Nothing is in the way of a Sim Week" about a league nobody has.
    # An unreadable boundary being a pass is right; an unreadable NAME is a different thing.
    unknown = [k for k in keys if k not in cfg.BY_KEY]
    if unknown:
        rows.append(Row(False, "every league named exists",
                        f"no such league: {', '.join(unknown)}",
                        f"one of {', '.join(sorted(cfg.BY_KEY))}"))
        keys = [k for k in keys if k in cfg.BY_KEY]
    _shared(rows, kind)
    if kind == "sim":
        _sim_rows(rows, keys)
    else:
        if settings is None and store is not None:
            try:
                settings = store.get_settings()
            except Exception as exc:                                    # noqa: BLE001
                rows.append(Row(False, "settings are readable", str(exc)))
                settings = {}
        _offseason_rows(rows, keys, season if season is not None else _season_of(settings))
    return rows


def ready(rows):
    return all(r.ok for r in rows)


def refusal(rows):
    """One sentence naming every failure, or "" when there is none."""
    bad = [r for r in rows if not r.ok]
    if not bad:
        return ""
    return "; ".join(f"{r.name}: {r.detail}" if r.detail else r.name for r in bad)


def report(rows, out=print):
    for r in rows:
        out(f"  {'ok  ' if r.ok else 'FAIL'}  {r.name}" + (f"  -  {r.detail}" if r.detail else ""))
        if not r.ok and r.fix:
            out(f"        fix: {r.fix}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=KINDS, default="sim")
    ap.add_argument("--league", action="append", default=None)
    a = ap.parse_args(argv)

    store = None
    if a.kind == "offseason":
        try:
            store = simweek.store()
        except Exception as exc:                                        # noqa: BLE001
            print(f"could not read the store: {exc}")
            return 2
    rows = check(a.kind, keys=a.league, store=store)
    print(f"Can the universe run a {a.kind} right now?\n")
    report(rows)
    if ready(rows):
        print(f"\nYES. Nothing is in the way of {'a Sim Week' if a.kind == 'sim' else 'a rollover'}.")
        return 0
    print(f"\nNO. {refusal(rows)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
