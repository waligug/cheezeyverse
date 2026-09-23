"""Run one Sim Week unattended, and stop rather than guess when the season ends.

WHAT THIS IS FOR. Everything a week needs already happens inside `simweek.run_sim` - apply, back
up, sim, export, snapshot, publish, grant points. What was missing was anything to START it
without a person clicking a button, and anywhere to look when it did not work. This is a
scheduled entry point with a gate in front and a Discord post behind.

THE ROLLOVER IS NEVER AUTOMATIC, and that is the whole design. An offseason promotes people
between leagues, drafts them, pays them and advances the season, through an irreversible sequence
that has failed mid-flight before. When the regular season runs out, `run_sim` refuses with
`SeasonEnd` - and a refusal is not a failure: nothing ran, no backup was taken, no save was
touched. Autopilot treats that refusal as its cue to run the offseason PREVIEW, post what WOULD
happen, and stop. A human presses the button.

IT NEVER RETRIES. A sim that failed halfway leaves a recovery journal, and a second attempt
against an unreconciled universe is how you turn one bad night into two. One run, one report,
exit.

    python tools/autopilot.py --dry-run      # says what it would do; writes and posts nothing
    python tools/autopilot.py                # one real week
    python tools/autopilot.py --days 7
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch                              # noqa: E402
from commissioner import notify                                        # noqa: E402
from commissioner import readiness                                     # noqa: E402
from commissioner import simweek                                       # noqa: E402
from commissioner.simweek import SeasonEnd, SimBusy, run_sim           # noqa: E402
from commissioner.universe import config as cfg                        # noqa: E402

# A week. The universe's own rhythm, and what `days` defaults to everywhere else.
DEFAULT_DAYS = 7


def days_left(keys=None):
    """{league: regular-season days left}, leaving out any league we cannot read.

    THE NUMBER AUTOPILOT MUST DECIDE FROM, and the reason it is read here instead of inferred
    from an exception later. `simweek` raises the SAME `SeasonEnd` class for two completely
    different situations - "this league has no days left to sim" and "seven days would run three
    past the end" - so the exception cannot tell them apart. Reading only its type is how an
    overshoot gets announced as the end of the season.

    That is not hypothetical: the leagues are different lengths (prep plays 30 games, pro 58), so
    the short one drops under a week first and would post "the season is over" every Sunday for
    the rest of the year while pro still had a month of regular season left - returning 0 each
    time, so Task Scheduler would record success and nothing would ever be simmed again.
    """
    out = {}
    for spec in cfg.LEAGUES:
        if keys and spec.key not in keys:
            continue
        try:
            left = simweek._regular_season_left(ch.save_path(spec.key).parent)
        except Exception:                                              # noqa: BLE001
            continue                    # unreadable is not zero; leave it out entirely
        if left is not None:
            out[spec.key] = int(left)
    return out



def _say(message, quiet=False, log=print):
    """To the terminal always; to Discord unless asked not to.

    Never raises. A scheduled task whose only failure mode is "could not report" is a task that
    looks fine in Task Scheduler and has done nothing for a month.
    """
    log(message)
    if quiet:
        return
    try:
        # notify.post NEVER RAISES - it catches and logs. Swallowing its log with a no-op meant
        # a deleted webhook was 100% silent: nothing posted, nothing printed, exit 0, green in
        # Task Scheduler. The real log is the only evidence that a post failed.
        notify.post(message, log=log)
    except Exception as exc:                                           # noqa: BLE001
        log(f"   (could not post to Discord: {exc})")


def preview_offseason(log=print):
    """A dry-run offseason, rendered as a few lines. Writes nothing, opens no save for writing."""
    from commissioner.offseason import run_offseason
    lines = []
    result = run_offseason(simweek.store(), season=None, log=lines.append, dry_run=True)
    season = result.get("season")
    promoted = result.get("promoted") or []
    drafted = result.get("drafted") or []
    retired = result.get("retired") or []
    grants = result.get("promotion_grants") or {}
    granted = sum(amount for rows in grants.values() for _why, amount in rows)
    out = [f"**The {season} season is over.** Here is what a rollover would do.",
           f"- {len(promoted)} promoted, {len(drafted)} drafted, {len(retired)} retired",
           f"- {result.get('grown', 0)} grew"]
    if granted:
        out.append(f"- {granted} points in promotion grants")
    for p in drafted[:10]:
        who = p.get("character") or {}
        out.append(f"  #{p.get('pick')} {p.get('team')} take "
                   f"{who.get('first_name','')} {who.get('last_name','')}".rstrip())
    # The warnings the offseason emits about itself are the reason to read this at all.
    for line in lines:
        if line.strip().startswith("!"):
            out.append(f"- {line.strip()}")
    out.append("Nothing was written. Run the offseason from the panel when you are ready.")
    return "\n".join(out), result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"days to sim, 1-400 (default {DEFAULT_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what it would do; take no lock, write nothing, post nothing")
    ap.add_argument("--quiet", action="store_true", help="terminal only, no Discord")
    a = ap.parse_args(argv)
    quiet = a.quiet or a.dry_run

    # THE SAME RANGE THE PANEL ENFORCES (app.py:1121), and it was missing here. `--days 0` is
    # not a no-op: run_sim backs up all three saves, applies pending work, opens the game,
    # exports, publishes, and then pays everybody, because `league_weeks = max(1, round(0/7))`
    # is 1. A full week's points for no basketball, and points are cumulative.
    if not 1 <= a.days <= 400:
        print(f"--days must be between 1 and 400; got {a.days}")
        return 2

    # ---- the gate -------------------------------------------------------------------------
    # WRAPPED, because this ran before any _say and was the only statement in main() with no
    # handler: a half-written journal produced a traceback to a detached console and not one
    # word anywhere a person looks.
    try:
        rows = readiness.check("sim")
    except Exception as exc:                                           # noqa: BLE001
        traceback.print_exc()
        _say(f"**Autopilot could not check whether it was safe to run** "
             f"({type(exc).__name__}: {exc}). Nothing was attempted.", quiet=quiet)
        return 1
    readiness.report(rows)
    if not readiness.ready(rows):
        _say(f"Autopilot did not run a week: {readiness.refusal(rows)}", quiet=quiet)
        return 1

    if a.dry_run:
        print(f"\nWould sim {a.days} day(s) in every league, publish, and grant points.")
        print("Nothing was written. No save was opened for writing, no message posted.")
        return 0

    # ---- the week -------------------------------------------------------------------------
    # HOW MUCH BASKETBALL IS LEFT, decided here from the saves rather than inferred from an
    # exception afterwards. See days_left for why the exception cannot be trusted with this.
    left = days_left()
    done = [k for k, n in left.items() if n <= 0]
    playing = {k: n for k, n in left.items() if n > 0}
    days = a.days
    if left and not playing:
        print("")
        print("every league is out of regular season.")
        try:
            text, _ = preview_offseason()
        except Exception as pexc:                                      # noqa: BLE001
            _say(f"The regular season is over everywhere, and the offseason preview would not "
                 f"run ({type(pexc).__name__}: {pexc}). Nothing was changed.", quiet=quiet)
            return 1
        _say(text, quiet=quiet)
        return 0
    if done:
        # SOME finished, others not. The way on is `allow_season_end`, which
        # `_refuse_to_cross_the_season` calls "the deliberate way into the PLAYOFFS" - and a
        # deliberate decision is what a scheduled task must not make on its own. This says
        # what is true and nothing more: the season is NOT over.
        _say(f"Autopilot stopped short: {', '.join(sorted(done))} finished the regular season, "
             f"but " + ", ".join(f"{k} has {n} day(s) left" for k, n in sorted(playing.items()))
             + ". Taking a league into the playoffs is a deliberate run - start it from the "
               "panel.", quiet=quiet)
        return 0
    if playing:
        # Never overshoot the shortest league: `_refuse_to_cross_the_season` guards on the one
        # with the FEWEST days, so that is the number it would refuse on.
        days = min(days, min(playing.values()))
        if days != a.days:
            print(f"simming {days} day(s) rather than {a.days}: "
                  f"{min(playing, key=playing.get)} has only that many left.")

    try:
        result = run_sim(days=days, on_step=lambda row: print(f"   {row['message']}"))
    except SeasonEnd as exc:
        # A BACKSTOP THAT ASSERTS NOTHING IT HAS NOT CHECKED. The block above should make this
        # unreachable; if it fires, the boundary moved under us - and the one thing that must
        # not happen is announcing the end of a season off an exception class that also means
        # "three days too many".
        _say(f"Autopilot stood down at the season boundary: {exc}", quiet=quiet)
        return 1
    except SimBusy as exc:
        # Something took the lock between the gate and here. Normal, and not worth a fuss.
        _say(f"Autopilot stood down: {exc}", quiet=quiet)
        return 1
    except Exception as exc:                                           # noqa: BLE001
        traceback.print_exc()
        _say(f"**Autopilot failed.** {type(exc).__name__}: {exc}\n"
             "Nothing will be retried automatically. Check the panel before the next run.",
             quiet=quiet)
        return 1

    # BELT AND BRACES. `run_sim` currently raises rather than returning ok=False, so this is
    # unreachable today - kept because the contract is not written down anywhere, and a week
    # that quietly reported failure by return value must not be announced as a success.
    if not result.get("ok"):
        errors = "; ".join(str(e) for e in (result.get("errors") or [])[:3]) or "no reason given"
        _say(f"**Autopilot ran a week and it did not finish clean.** {errors}", quiet=quiet)
        return 1

    # TO THE TERMINAL ONLY. `run_sim` already opens and closes its own SimStatus card in
    # Discord for this exact event; a second post said the same thing in weaker words - and
    # quoted a `seconds` that run_sim never puts on the dict it returns, so it always read "?".
    print(f"Autopilot simmed {result.get('days')} day(s) across "
          f"{len(result.get('leagues') or [])} leagues. "
          f"{result.get('applied', 0)} change(s) applied, "
          f"{result.get('activated', 0)} character(s) activated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
