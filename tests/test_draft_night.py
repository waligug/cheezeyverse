"""Draft night says what it is thinking, and everything it says is true.

`tests/test_draft.py` guards the pick itself - that the board is complete, repeatable, and that a
team never claims a hole it did not fill. This file guards the COMMENTARY that was built on top of
it, which is a different hazard: commentary is the part nobody notices is wrong, because a
sentence that reads well is assumed to be true.

THE ASSERTION THIS FILE EXISTS FOR is `test_a_character_is_never_left_undrafted`. The board now
carries FBPB3's own eighty-man prospect pool alongside our people, and the draft stops after two
rounds. A character who slides past the last pick is never promoted, never stamped onto a roster,
never paid - he simply stops existing between leagues, and nothing raises. The field absorbs the
cut instead, always.

`test_the_field_is_never_promoted` is the other one worth keeping. Those eighty are FBPB3's, and
the game runs its own draft over them; stamping one onto a roster here would invent a transaction
the save never made.

    python tests/test_draft_night.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import draft  # noqa: E402
from commissioner.codec.league_dat import POTENTIALS, RATINGS  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


def man(name, position="C", now=60, ceiling=70, height=78):
    return {"id": name, "first_name": "P", "last_name": name, "position": position,
            "height_inches": height,
            "ratings": {f: now for f in RATINGS},
            "potentials": {f: ceiling for f in RATINGS}}


def spread(n, start=0):
    """A jumbled field - alternating high-ceiling and high-floor - so profiles really disagree."""
    return [man(chr(65 + start + i), ["C", "PG", "SF", "PF", "SG"][i % 5],
                now=70 - i, ceiling=(95 - 3 * i if i % 2 else 60 + i)) for i in range(n)]


# ---- the sentence only claims what the arithmetic showed --------------------------------------
def test_the_runner_up_is_named_only_when_it_was_close():
    """Naming the second man on EVERY pick made a landslide and a coin-flip read identically."""
    print("the runner-up is a claim about the margin")
    detail = {"terms": {"now": 10.0, "ceiling": 40.0, "fit": 0.0}, "fits": False,
              "ceiling": 80.0, "now": 20.0, "need": None}
    blowout = draft.reason_for("LCH", "upside", detail, "Other Man",
                               {"score": 50.0, "margin": 25.0, "left": 6, "pick": 1})
    close = draft.reason_for("LCH", "upside", detail, "Other Man",
                             {"score": 50.0, "margin": 1.5, "left": 6, "pick": 1})
    hair = draft.reason_for("LCH", "upside", detail, "Other Man",
                            {"score": 50.0, "margin": 0.2, "left": 6, "pick": 1})
    check("a landslide does not pretend it was close", "Other Man" in blowout, False)
    check("a real contest says so", "the other name in the room" in close, True)
    check("and a dead heat says more", "coin-flip" in hair, True)


def test_one_man_left_is_not_described_as_the_best_of_the_board():
    """"Highest ceiling LEFT on the board" is true of a board of one and tells the room nothing."""
    print("a board with one name on it")
    detail = {"terms": {"now": 10.0, "ceiling": 40.0, "fit": 0.0}, "fits": False,
              "ceiling": 80.0, "now": 20.0, "need": None}
    only = draft.reason_for("LCH", "upside", detail, None, {"left": 1, "pick": 1, "score": 50.0})
    last = draft.reason_for("LCH", "upside", detail, None, {"left": 1, "pick": 9, "score": 50.0})
    check("the only declarant", "only name in this draft" in only, True)
    check("the last man taken", "last name on the board" in last, True)
    check("neither claims a board it did not beat", "Highest ceiling" in only + last, False)


def test_a_team_that_misses_its_hole_says_so():
    """The pick most worth explaining is the one where a team went against type.

    Before this, the catchphrase was suppressed - correctly, it would have been a lie - and
    NOTHING replaced it, so a "drafts the hole" team taking a centre when it needed a power
    forward read exactly like a team with no opinion at all.
    """
    print("a need-first team that did not fill its need")
    # He needs a PF, only centres are on the board.
    board = draft.build_board([man("A", "C", 70, 90), man("B", "C", 68, 88)],
                              ["LCH"], needs={"LCH": "PF"}, profiles={"LCH": "need_first"})
    first = board[0]["reason"]
    check("it says what they wanted", "wanted a PF" in first, True)
    check("and why they could not have it", "Nobody left on the board plays one" in first, True)
    check("it does not claim the hole", "thinnest at" in first, False)

    # Now a PF IS available but loses on the sum: the honest line is that they passed on him.
    board = draft.build_board([man("Star", "C", 95, 99), man("Fit", "PF", 20, 22)],
                              ["LCH"], needs={"LCH": "PF"}, profiles={"LCH": "need_first"})
    check("the PF was there and lost", board[0]["character"]["last_name"], "Star")
    check("so it names the man they passed on", "passed on P Fit" in board[0]["reason"], True)

    # And with no hole at all, the honest line is that there was nothing to fill.
    board = draft.build_board([man("A", "C", 70, 90)], ["LCH"], profiles={"LCH": "need_first"})
    check("no hole, and it says so", "Tonight they had none" in board[0]["reason"], True)


def test_best_available_is_allowed_to_say_its_own_line():
    """Its line is a NEGATIVE claim, so the flat "did my term win" test silenced it forever."""
    print("a best-available team has a catchphrase too")
    check("honest when ceiling decided it",
          draft._catchphrase_is_honest("best_available", "ceiling"), True)
    check("honest when now decided it",
          draft._catchphrase_is_honest("best_available", "now"), True)
    # "We do not draft for need" on a pick that fit decided would be the exact lie the module
    # exists to prevent.
    check("NOT honest when fit decided it",
          draft._catchphrase_is_honest("best_available", "fit"), False)
    for key, term in (("upside", "ceiling"), ("win_now", "now"), ("need_first", "fit")):
        check(f"{key} still only speaks on {term}",
              [draft._catchphrase_is_honest(key, t) for t in ("ceiling", "now", "fit")],
              [t == term for t in ("ceiling", "now", "fit")])


# ---- the roast carries a true number ----------------------------------------------------------
def test_the_snub_names_the_man_actually_sliding():
    """The first version counted picks-sat-through, which is the SAME for everybody available.

    On a full board every remaining prospect has sat through exactly as many picks as have been
    made, so the maximum was always whoever happened to be rated second - who was then taken
    moments later. The joke has to be about the gap between where the sheet put a man and where
    the room has got to, or it is not about anything.
    """
    print("the roast is about a real slide")
    teams = ["LCH", "TIL", "PLY", "ASI"]
    people = spread(14)
    needs = {t: ["C", "PG", "SF", "PF", "SG"][i % 5] for i, t in enumerate(teams)}
    board = draft.build_board(people, teams, needs=needs)
    snubbed = [p for p in board if p.get("snub")]
    check("somebody got roasted", bool(snubbed), True)
    check("nobody is roasted before it is funny",
          all(p["pick"] > draft.SNUB_AFTER for p in snubbed), True)
    # THE THING THAT MUST NOT HAPPEN: roasting the man who was just taken.
    for p in snubbed:
        taken = draft.describe(p["character"]).split("  ")[0]
        check(f'#{p["pick"]} does not roast the man it just took', taken in p["snub"], False)
    # Every roast names somebody who really is behind where the flat sheet put him.
    rank = draft.talent_rank(people)
    by_name = {draft.describe(c).split("  ")[0]: rank[id(c)] for c in people}
    for p in snubbed:
        who = next((n for n in by_name if n in p["snub"]), None)
        check(f'#{p["pick"]} roasts a named prospect', who is not None, True)
        if who:
            check(f'   {who} really has slid {draft.SNUB_AFTER}+',
                  p["pick"] - by_name[who] >= draft.SNUB_AFTER, True)


def test_a_short_draft_is_never_roasted():
    """Four picks with four prospects is not a slide, it is a draft. No joke has earned itself."""
    print("nothing to laugh at yet")
    board = draft.build_board(spread(4), ["A", "B", "C", "D"])
    check("no roasts", [p["snub"] for p in board], [None] * 4)


def test_the_slide_uses_the_whole_board():
    """Ranking the DRAFTED alone closes the gaps the undrafted left and erases the big slides."""
    print("the closing slide report")
    teams = ["LCH", "TIL", "PLY", "ASI"]
    board = draft.build_board(spread(14), teams,
                              needs={t: ["C", "PG", "SF", "PF", "SG"][i % 5]
                                     for i, t in enumerate(teams)})
    slid = draft.slides(board)
    check("somebody slid", bool(slid), True)
    check("worst first", [n for _p, _n, n in slid], sorted([n for _p, _n, n in slid],
                                                           reverse=True))
    check("and a one-pick wobble is not reported",
          all(n >= draft.MIN_SLIDE for _p, _n, n in slid), True)
    # Each pick carries the rank it was given against the whole board, so slides() never has to
    # rebuild one - which is what keeps an undrafted man from silently closing somebody's gap.
    check("every pick knows where the sheet put him",
          all(p.get("expected") for p in board), True)
    check("a board with no expectations reports nothing",
          draft.slides([{"pick": 9, "character": man("X")}]), [])


# ---- the field ---------------------------------------------------------------------------------
class FakePlayer:
    def __init__(self, name, pid, team, ovr, pot, position=1):
        self.name = name
        self.id = pid
        self.values = {"Team": team, "Position": position, "Height": 78, "Exp": 0}
        self.values.update({f: ovr for f in RATINGS})
        self.values.update({f: pot for f in POTENTIALS})


def fake_save(pool_pot):
    """A save with 300 rostered, 8 free agents and 12 in the draft pool at the given potential."""
    players = [FakePlayer(f"Rostered {i}", 100 + i, 1 + i % 20, 45, 50) for i in range(30)]
    players += [FakePlayer(f"Agent {i}", 200 + i, -1, 9, 5) for i in range(8)]
    players += [FakePlayer(f"Prospect {i}", 300 + i, draft.POOL_TEAM, 40, pool_pot)
                for i in range(12)]
    return SimpleNamespace(players=players)


def test_an_ungenerated_pool_is_not_a_draft_class():
    """Eighty blank records sit at team -2 all year. Drafting them ranks our people against noise.

    This is the live state of CV_Pro for most of the calendar: every pool record reads about 9
    overall with every potential pinned at 5, because FBPB3 fills the class in during its own
    offseason. Put that on the board and eighty identical nobodies are announced to Discord.
    """
    print("the pool before the game has filled it in")
    # Ratings of 40 with every potential pinned at 5: exactly what an ungenerated class looks
    # like, and the shape that slips past a guard reading sheet()'s ceiling, because sheet()
    # rescues an unreadable ceiling by falling back to what the man already is.
    check("a pinned-at-5 pool is no pool", len(draft.field_from_save(fake_save(5))), 0)
    check("and an empty save is no pool",
          len(draft.field_from_save(SimpleNamespace(players=[]))), 0)
    real = draft.field_from_save(fake_save(78))
    check("a generated class comes through", len(real), 12)
    check("only the pool, not the free agents", all(c["id"].startswith("pool-") for c in real),
          True)
    check("it is in character shape",
          sorted(set(real[0]) & {"first_name", "position", "ratings", "potentials"}),
          ["first_name", "position", "potentials", "ratings"])
    check("and it is marked as not ours", {c["role"] for c in real}, {"field"})
    check("best first", [round(sum(draft.sheet(c))) for c in real],
          sorted([round(sum(draft.sheet(c))) for c in real], reverse=True))
    check("a limit keeps the best", len(draft.field_from_save(fake_save(78), limit=5)), 5)


def test_the_field_is_on_the_board_but_is_never_ours():
    print("our people and the game's, on one board")
    ours = [man("Mine", "C", 80, 92)]
    field = draft.field_from_save(fake_save(78))
    board = draft.build_board(ours, ["A", "B", "C", "D"], field=field)
    mine = [p for p in board if p["is_character"]]
    check("exactly one of them is ours", len(mine), 1)
    check("and it is the right one", mine[0]["character"]["last_name"], "Mine")
    check("the rest are the field", all(p["character"]["role"] == "field"
                                        for p in board if not p["is_character"]), True)


def test_a_character_is_never_left_undrafted():
    """A character who slides past the last pick is never promoted, placed or paid.

    The board stops after DRAFT_ROUNDS - but only once every one of ours is off it. Here the
    character is the WORST player in the draft by a distance, so a board that honoured the round
    limit strictly would leave him on it.
    """
    print("the rounds are a floor, not a ceiling")
    hopeless = [man("Hopeless", "C", 5, 6)]
    field = draft.field_from_save(fake_save(90))
    board = draft.build_board(hopeless, ["A", "B"], field=field, rounds=1)
    check("the round limit would have stopped at 2", len(board) > 2, True)
    check("he was taken anyway", any(p["is_character"] for p in board), True)
    check("last, as he deserved", board[-1]["character"]["last_name"], "Hopeless")
    check("no character is ever undrafted",
          [c for c in draft.undrafted_from(hopeless, field, board) if c.get("role") != "field"],
          [])

    print("and a character who does not slide leaves the field to be cut")
    good = [man("Good", "C", 88, 96)]
    board = draft.build_board(good, ["A", "B"], field=field, rounds=2)
    check("the board is exactly the rounds", len(board), 4)
    check("ours went first", board[0]["character"]["last_name"], "Good")
    cut = draft.undrafted_from(good, field, board)
    check("the rest of the field goes home", len(cut), len(field) - 3)
    check("and none of them is ours", any(c.get("role") != "field" for c in cut), False)

    print("run_draft trims the field to the size of a real draft")
    # Eighty on the board is eighty announced picks. The field is cut to fill the rounds and no
    # more, which is what keeps draft night two rounds long.
    from commissioner import offseason
    asked = {}

    def fake_field(log=print, limit=None):
        asked["limit"] = limit
        return field[:limit] if limit else field

    real_needs, real_field = offseason._draft_needs, offseason._draft_field
    offseason._draft_needs = lambda log=print: {}
    offseason._draft_field = fake_field
    try:
        picks = offseason.run_draft(good, store=None, log=lambda m: None, dry_run=True,
                                    season=2030)
    finally:
        offseason._draft_needs, offseason._draft_field = real_needs, real_field
    teams = len(offseason.draft_order())
    check("it asked for a draft-sized field",
          asked["limit"], draft.DRAFT_ROUNDS * teams - len(good))
    check("and ours is still in it", len(picks), 1)


def test_two_identical_prospects_are_two_people():
    """Blank pool records compare EQUAL as dicts, and list.remove() goes by value.

    So the drafted man was left on the board and taken again, while some other prospect vanished
    without ever being picked. Nothing raises; the board just quietly contains one man twice.
    """
    print("a board of indistinguishable prospects")
    twins = [man("Same", "C", 50, 60) for _ in range(6)]
    check("they really are equal", twins[0] == twins[1], True)
    board = draft.build_board(twins, ["A", "B"], rounds=3)
    check("everybody is taken exactly once", len(board), 6)
    check("and no record is drafted twice",
          len({id(p["character"]) for p in board}), 6)


def test_run_draft_promotes_ours_and_only_ours():
    """The field is scenery. Promoting one would invent a transaction FBPB3 never made."""
    print("the write path")
    from commissioner import offseason
    ours = [man("Mine", "C", 80, 92)]
    field = draft.field_from_save(fake_save(78))
    real_needs, real_field = offseason._draft_needs, offseason._draft_field
    offseason._draft_needs = lambda log=print: {}
    offseason._draft_field = lambda log=print, limit=None: field[:limit] if limit else field
    try:
        lines = []
        picks = offseason.run_draft(ours, store=None, log=lines.append, dry_run=True,
                                    season=2030)
    finally:
        offseason._draft_needs, offseason._draft_field = real_needs, real_field
    check("only ours come back", len(picks), 1)
    check("and it is the character", picks[0]["character"]["last_name"], "Mine")
    check("the field was announced in the log",
          any("(field)" in line for line in lines), True)
    check("the log carries the thinking, not just the names",
          any("ceiling" in line or "ready to play" in line or "wanted a" in line
              for line in lines), True)


def test_the_broadcast_survives_all_of_it():
    """Every method swallows everything: a draft must never fail because Discord did."""
    print("the cast")
    from commissioner import draftcast
    sent = []

    class Transport:
        message_id = None

        def send(self, payload):
            sent.append(payload)

        def close(self):
            pass

    cast = draftcast.DraftCast(2030, transport=Transport(), delay=0, sleep=lambda s: None)
    teams = ["LCH", "TIL", "PLY", "ASI"]
    people = spread(14)
    board = draft.build_board(people, teams,
                              needs={t: ["C", "PG", "SF", "PF", "SG"][i % 5]
                                     for i, t in enumerate(teams)})
    cast.open(3, teams, board=len(board))
    opener = sent[0]["embeds"][0]["description"]
    check("it does not credit us with signups we do not have", "**3** players declared" in opener,
          True)
    check("and says how big the board really is", "14 names on the board" in opener, True)

    roasted = next(p for p in board if p.get("snub"))
    cast.on_the_clock(roasted)
    check("the clock message says what they want",
          "front office that" in sent[-1]["embeds"][0]["description"], True)
    cast.pick(roasted)
    fields = {f["name"]: f["value"] for f in sent[-1]["embeds"][0].get("fields", [])}
    check("the roast is broadcast", "Still waiting" in fields, True)

    cast.close(board, undrafted=[man("Nobody", "SF", 10, 12)])
    fields = {f["name"]: f["value"] for f in sent[-1]["embeds"][0].get("fields", [])}
    check("the slide is reported", "The slide" in fields, True)
    check("and the undrafted are named", "Undrafted (1)" in fields, True)
    check("by name", "P Nobody" in fields["Undrafted (1)"], True)

    # NOTHING the broadcast does may raise into the draft.
    class Boom:
        message_id = None

        def send(self, payload):
            raise RuntimeError("discord is down")

        def close(self):
            pass

    dead = draftcast.DraftCast(2030, transport=Boom(), delay=0, log=lambda m: None,
                               sleep=lambda s: None)
    check("open survives", dead.open(3, teams, board=14), False)
    check("the clock survives", dead.on_the_clock(board[0]), False)
    check("a pick survives", dead.pick(board[4]), False)
    check("the close survives", dead.close(board, undrafted=[man("X")]), False)


def main():
    test_the_runner_up_is_named_only_when_it_was_close()
    test_one_man_left_is_not_described_as_the_best_of_the_board()
    test_a_team_that_misses_its_hole_says_so()
    test_best_available_is_allowed_to_say_its_own_line()
    test_the_snub_names_the_man_actually_sliding()
    test_a_short_draft_is_never_roasted()
    test_the_slide_uses_the_whole_board()
    test_an_ungenerated_pool_is_not_a_draft_class()
    test_the_field_is_on_the_board_but_is_never_ours()
    test_a_character_is_never_left_undrafted()
    test_two_identical_prospects_are_two_people()
    test_run_draft_promotes_ours_and_only_ours()
    test_the_broadcast_survives_all_of_it()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  draft night: the commentary only claims what the board shows - the runner-up is "
          "named when it was close, a team that missed its hole says which kind of miss it was, "
          "the roast names a man who really is sliding - the game's own prospect field fills out "
          "the board without ever being promoted, and no character is left undrafted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
