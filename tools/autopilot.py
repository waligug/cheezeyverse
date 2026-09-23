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

from commissioner import notify                                        # noqa: E402
from commissioner import readiness                                     # noqa: E402
from commissioner import simweek                                       # noqa: E402
from commissioner.simweek import SeasonEnd, SimBusy, run_sim           # noqa: E402

# A week. The universe's own rhythm, and what `days` defaults to everywhere else.
DEFAULT_DAYS = 7


def _say(message, quiet=False, log=print):
    """To the terminal always; to Discord unless asked not to.

    Never raises. A scheduled task whose only failure mode is "could not report" is a task that
    looks fine in Task Scheduler and has done nothing for a month.
    """
    log(message)
    if quiet:
        return
    try:
        notify.post(message, log=lambda m: None)
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
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--dry-run", action="store_true",
                    help="say what it would do; take no lock, write nothing, post nothing")
    ap.add_argument("--quiet", action="store_true", help="terminal only, no Discord")
    a = ap.parse_args(argv)
    quiet = a.quiet or a.dry_run

    # ---- the gate -------------------------------------------------------------------------
    rows = readiness.check("sim")
    readiness.report(rows)
    if not readiness.ready(rows):
        _say(f"Autopilot did not run a week: {readiness.refusal(rows)}", quiet=quiet)
        return 1

    if a.dry_run:
        print(f"\nWould sim {a.days} day(s) in every league, publish, and grant points.")
        print("Nothing was written. No save was opened for writing, no message posted.")
        return 0

    # ---- the week -------------------------------------------------------------------------
    try:
        result = run_sim(days=a.days, on_step=lambda row: print(f"   {row['message']}"))
    except SeasonEnd as exc:
        # NOT A FAILURE. Nothing ran: no backup, no save touched. This is the season asking for
        # a human, and the preview is what it should be asked with.
        print(f"\nthe regular season is out of days: {exc}")
        try:
            text, _ = preview_offseason()
        except Exception as pexc:                                      # noqa: BLE001
            _say(f"The regular season is over, and the offseason preview would not run "
                 f"({type(pexc).__name__}: {pexc}). Nothing was changed.", quiet=quiet)
            return 1
        _say(text, quiet=quiet)
        return 0
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

    if not result.get("ok"):
        errors = "; ".join(str(e) for e in (result.get("errors") or [])[:3]) or "no reason given"
        _say(f"**Autopilot ran a week and it did not finish clean.** {errors}", quiet=quiet)
        return 1

    _say(f"Autopilot simmed {result.get('days')} day(s) across "
         f"{len(result.get('leagues') or [])} leagues in {result.get('seconds', '?')}s. "
         f"{result.get('applied', 0)} change(s) applied, "
         f"{result.get('activated', 0)} character(s) activated.", quiet=quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
