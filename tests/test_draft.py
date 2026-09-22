"""Draft night picks for reasons, and the reason it prints is the reason it used.

The old draft sorted everybody by one `_promise` score and handed pick *i* to
`order[i % len(order)]`. Every team wanted the same player in the same order, so nothing about a
team entered into it - not its roster, not what it already had four of.

THE INVARIANT THIS FILE EXISTS FOR is the honesty one. `evaluate` returns the score AND the term
that produced it, and `reason_for` builds its sentence from that term - so a team can never claim
it filled a hole on a pick that was really about ceiling. That is checked directly below, because
it is the one property that rots silently the moment somebody tunes a weight.

    python tests/test_draft.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import draft  # noqa: E402
from commissioner.codec.league_dat import RATINGS  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def man(name, position, now, ceiling, height=78):
    return {"id": name, "first_name": name, "last_name": "", "position": position,
            "height_inches": height,
            "ratings": {f: now for f in RATINGS},
            "potentials": {f: ceiling for f in RATINGS}}


def run():
    print("a team's temperament is stable, and they are not all the same")
    check("the same team twice", draft.team_profile("LCH"), draft.team_profile("LCH"))
    profiles = {t: draft.team_profile(t) for t in
                ("LCH", "TIL", "PLY", "ASI", "MTY", "THP", "MNS", "PAR", "SWI", "ALP")}
    check("more than one kind of front office", len(set(profiles.values())) > 1, True)
    check("every profile is a real one",
          all(p in draft.PROFILES for p in profiles.values()), True)

    print("upside and win-now disagree about the same two players")
    ready = man("Ready", "C", 70, 72)       # good now, nearly finished
    raw = man("Raw", "C", 55, 95)           # worse now, far more to come
    up, _ = draft.evaluate(raw, None, "upside")
    up_ready, _ = draft.evaluate(ready, None, "upside")
    now_raw, _ = draft.evaluate(raw, None, "win_now")
    now_ready, _ = draft.evaluate(ready, None, "win_now")
    check("upside prefers the raw one", up > up_ready, True)
    check("win-now prefers the ready one", now_ready > now_raw, True)

    print("a hole matters to a need-first team, and only when he fills it")
    fit, detail = draft.evaluate(ready, "C", "need_first")
    nofit, _ = draft.evaluate(ready, "PG", "need_first")
    check("filling the hole scores higher", fit > nofit, True)
    check("and it is recorded as a fit", detail["fits"], True)
    ba_fit, _ = draft.evaluate(ready, "C", "best_available")
    ba_nofit, _ = draft.evaluate(ready, "PG", "best_available")
    check("need-first cares more than best-available about the same hole",
          (fit - nofit) > (ba_fit - ba_nofit), True)

    print("THE REASON IS THE REASON: a team never claims a hole it did not fill")
    board = draft.build_board([ready, raw, man("Third", "PG", 60, 65)],
                              ["LCH", "TIL", "PLY"], needs={"LCH": "PG"})
    for p in board:
        said_hole = "thinnest at" in p["reason"]
        really_fit = str(p["character"]["position"]) == str(p.get("need") or "")
        check(f'#{p["pick"]} {p["team"]}: claims a hole only if it filled one',
              said_hole and not really_fit, False)
        catchphrase = draft.PROFILES[p["profile"]]["line"].format(team=p["team"])
        if catchphrase in p["reason"]:
            check(f'#{p["pick"]} its catchphrase names the term that won',
                  draft.PROFILE_TERM[p["profile"]] is not None, True)

    print("the board is a board")
    many = [man(f"P{i}", "C", 70 - i, 80 - i) for i in range(7)]
    board = draft.build_board(many, ["A", "B", "C"])
    check("everybody is picked", len(board), 7)
    check("nobody twice", len({p["character"]["id"] for p in board}), 7)
    check("picks are numbered from one", [p["pick"] for p in board], list(range(1, 8)))
    check("the order wraps", [p["team"] for p in board[:4]], ["A", "B", "C", "A"])
    check("and the round turns over", board[3]["round"], 2)
    check("every pick carries a reason", all(p["reason"] for p in board), True)

    print("it is deterministic - a preview and the real night agree")
    again = draft.build_board(list(many), ["A", "B", "C"])
    check("same board twice", [p["character"]["id"] for p in again],
          [p["character"]["id"] for p in board])

    print("a man with nothing on his sheet is still drafted, not dropped")
    empty = {"id": "Empty", "first_name": "Empty", "last_name": "", "position": "SF"}
    board = draft.build_board([empty, ready], ["A", "B"])
    check("both picked", len(board), 2)
    check("and the good one goes first", board[0]["character"]["id"], "Ready")

    print("an unreadable ceiling never sinks a man below what he already is")
    now, ceiling = draft.sheet({"ratings": {f: 60 for f in RATINGS}, "potentials": {}})
    check("ceiling falls back to now", ceiling >= now, True)
    check("and now is his own average", round(now), 60)

    print("run_draft builds the board, narrates it, and announces nothing on a dry run")
    from commissioner import offseason
    calls = []

    class Cast:
        def open(self, n, order): calls.append(("open", n))
        def on_the_clock(self, p): calls.append(("clock", p["pick"]))
        def pick(self, p, contract=None): calls.append(("pick", p["pick"]))
        def close(self, picks): calls.append(("close", len(picks)))

    real_needs = offseason._draft_needs
    offseason._draft_needs = lambda log=print: {"LCH": "C"}
    try:
        lines = []
        picks = offseason.run_draft([ready, raw], store=None, log=lines.append,
                                    dry_run=True, season=2030, cast=Cast())
    finally:
        offseason._draft_needs = real_needs

    check("everyone declared is on the board", len(picks), 2)
    check("the log names the reason, not just the pick",
          any("ceiling" in l or "ready to play" in l or "thinnest" in l for l in lines), True)
    # A DRY RUN ANNOUNCES NOTHING AT ALL. The preview has to show the room what will happen
    # without telling the room it happened, and a pick posted to the server cannot be taken back.
    check("nothing was announced", calls, [])
    check("nothing was written", all("error" not in p for p in picks), True)

    print("a team with no free row does not send the whole overflow to one roster")
    # `team` wins outright, which is right - a man taken first overall by LCH joins LCH. But
    # when LCH has no free reserve row, pick_slot falls to a second pool, and that pool ignored
    # `busy`. One promotion never noticed. A draft promotes sixty in a row, so every pick that
    # overflowed went to whichever team happened to sort first, which is the stacking bug all
    # over again through the one branch that skipped the fix.
    from types import SimpleNamespace
    from commissioner import characters as chmod
    teams = [f"T{i:02d}" for i in range(16)]
    divisions = {t: i // 4 for i, t in enumerate(teams)}
    # LCH is the drafting team on every pick and has no row at all.
    slots = [SimpleNamespace(name=f"{t}-res{n}", dob="1/1/2012", team=t)
             for t in teams for n in range(3)]
    busy = {t: 0 for t in teams}
    busy["LCH"] = 0
    landed = []
    for _ in range(6):
        slot = chmod.pick_slot(slots, None, team="LCH", busy=busy, divisions=divisions)
        check("a slot was found", slot is not None, True)
        slots = [s for s in slots if s is not slot]
        busy[slot.team] = busy.get(slot.team, 0) + 1
        landed.append(slot.team)
    check("six overflow picks, six different rosters", len(set(landed)), 6)

    print("and the drafting team still wins when it does have one")
    slots = [SimpleNamespace(name=f"{t}-res{n}", dob="1/1/2012", team=t)
             for t in teams for n in range(3)]
    # T09 is buried in the busy ordering, so only `team` can put him there.
    busy = {t: 0 for t in teams}
    busy["T09"] = 99
    slot = chmod.pick_slot(slots, None, team="T09", busy=busy, divisions=divisions)
    check("he joins the team that drafted him", slot.team, "T09")

    print("run_draft carries one tally rather than re-reading the store sixty times")
    import inspect
    src = inspect.getsource(offseason.run_draft)
    check("it passes a tally to promote", "busy=busy" in src, True)
    check("and counts each placement into it", "busy[landed]" in src, True)

    print("an empty draft is not a draft")
    check("nobody declared", offseason.run_draft([], store=None, log=lambda m: None,
                                                 dry_run=True, season=2030), [])

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  draft: teams differ, a hole only counts when it is filled, the stated reason is "
          "the one the arithmetic used, and the board is complete and repeatable")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
