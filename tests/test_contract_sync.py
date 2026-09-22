"""What the game pays a character reaches the website, or it does not exist.

FBPB3 has carried seven years of salary per player since the universe was created and none of it
has ever left the save. The offseason payout reads it, spends it and throws it away - so a player
sees `+36  contract payout: star` in his ledger with no way to find out what he earns, how long
his deal runs, or what "star" meant. `simweek._sync_contracts` is what records it.

THE ASSERTION THIS FILE EXISTS FOR is `test_a_league_with_no_scale_says_so_rather_than_guessing`.
With Finances off every contract in a league is the same $1,000,000 token. There is no
distribution to rank anybody against, and a page that answered "league minimum" - or "star" -
would be inventing a verdict out of one repeated number. `scale: False` and a null band is the
only honest output, and the site is built to read it.

`test_the_band_is_recorded_not_recomputed` is the other one worth keeping. The band is a
percentile against a league's own salaries and `points.payout_band` is the one implementation of
that rule. If the browser derived it instead, it would be a second copy that drifts the first time
somebody tunes a band - and the site would confidently show a player a payout he does not get.

    python tests/test_contract_sync.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import points  # noqa: E402
from commissioner import simweek  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class FakePlayer:
    def __init__(self, name, team, salary, years):
        self.name = name
        self.dob = "1/1/2008"
        self.values = {"Team": team}
        self.contract = [salary] * years + [0] * (7 - years)


class FakeSave:
    """Enough LeagueDat to exercise the decisions: players, contract_of and find."""

    def __init__(self, players):
        self.players = players

    def contract_of(self, pl):
        return list(pl.contract)

    def find(self, name, dob=None):
        for p in self.players:
            if p.name == name:
                return p
        raise KeyError(name)


class FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []

    def characters(self, league=None):
        return [r for r in self.rows if league is None or r.get("league") == league]

    def set_character_field(self, cid, field, value):
        self.writes.append((cid, field))
        for r in self.rows:
            if r["id"] == cid:
                r[field] = value


def character(cid, name, league="pro", to_season=None):
    first, _, last = name.partition(" ")
    return {"id": cid, "first_name": first, "last_name": last, "league": league,
            "status": "active", "game_dob": "1/1/2008",
            "level_history": [{"level": league, "to_season": to_season}]}


def finances_of(row):
    for h in reversed(row.get("level_history") or []):
        if h.get("to_season") is None and h.get("finances"):
            return h["finances"]
    return None


def a_real_league(ours_salary=9_000_000, ours_years=3):
    """A league with a genuine spread, plus one of ours in it."""
    players = [FakePlayer(f"Filler {i}", 1 + i % 20, s, 1) for i, s in enumerate(
        [1_000_000] * 40 + [2_500_000] * 20 + [6_000_000] * 12
        + [15_000_000] * 6 + [30_000_000] * 3 + [45_000_000])]
    players.append(FakePlayer("Dodger Manson", 5, ours_salary, ours_years))
    return FakeSave(players)


def test_it_records_what_the_game_pays():
    print("the salary reaches the store")
    save = a_real_league()
    store = FakeStore([character("c1", "Dodger Manson")])
    written = simweek._sync_contracts("pro", save, store, print)
    check("one character recorded", written, 1)
    f = finances_of(store.rows[0])
    check("the salary", f["salary"], 9_000_000)
    check("the term", f["years_left"], 3)
    check("there is a scale", f["scale"], True)
    check("and a band", f["band"], "starter")
    # Computed against THIS league's own salaries, which is the whole rule - not against some
    # other list, which is what the first version of this assertion did.
    rostered = [c[0] for c in (pl.contract for pl in save.players) if c[0]]
    check("and what it will pay him", f["payout"],
          points.annual_payout(9_000_000, points.salary_distribution(rostered)))
    check("the median is carried so the page can explain the band",
          isinstance(f["league_median"], int), True)


def test_the_band_is_recorded_not_recomputed():
    """The stored band and payout must be exactly what points.py says, for every band."""
    print("the band matches points.py exactly")
    for salary in (1_000_000, 6_000_000, 30_000_000, 45_000_000):
        save = a_real_league(ours_salary=salary, ours_years=2)
        store = FakeStore([character("c1", "Dodger Manson")])
        simweek._sync_contracts("pro", save, store, print)
        f = finances_of(store.rows[0])
        rostered = [c[0] for c in (p.contract for p in save.players) if c[0]]
        bounds = points.salary_distribution(rostered)
        check(f"${salary:,} band", f["band"], points.payout_band(salary, bounds))
        check(f"${salary:,} payout", f["payout"], points.annual_payout(salary, bounds))
        # And the label never disagrees with the money, which is the trap payout_band already hit.
        check(f"${salary:,} label is sane", f["band"] in points.BAND_LABELS, True)


def test_a_league_with_no_scale_says_so_rather_than_guessing():
    """Finances off: one distinct salary across the league. That is not a ranking."""
    print("a league with finances switched off")
    players = [FakePlayer(f"Filler {i}", 1 + i % 20, 1_000_000, 1) for i in range(300)]
    players.append(FakePlayer("Dodger Manson", 5, 1_000_000, 1))
    store = FakeStore([character("c1", "Dodger Manson")])
    simweek._sync_contracts("pro", FakeSave(players), store, print)
    f = finances_of(store.rows[0])
    check("it still records the money", f["salary"], 1_000_000)
    check("but refuses to name a band", f["band"], None)
    check("and says there is no scale", f["scale"], False)
    check("no median to compare against", f["league_median"], None)
    check("and it pays the floor, not a guess", f["payout"], points.PAYOUT_FLOOR)


def test_prep_and_college_are_not_given_a_pro_promise():
    """Only pro is paid by contract. A band anywhere else promises something nothing honours."""
    print("the other two levels")
    for league in ("prep", "college"):
        save = a_real_league()
        store = FakeStore([character("c1", "Dodger Manson", league=league)])
        simweek._sync_contracts(league, save, store, print)
        f = finances_of(store.rows[0])
        check(f"{league} records the deal", f["salary"], 9_000_000)
        check(f"{league} is given no band", f["band"], None)
        check(f"{league} is promised no payout", f["payout"], None)


def test_a_bare_contract_is_recorded_as_bare():
    """45 of pro's 300 rostered players have no contract. That is the release-on-load case."""
    print("a character with no contract at all")
    save = a_real_league(ours_salary=0, ours_years=0)
    store = FakeStore([character("c1", "Dodger Manson")])
    simweek._sync_contracts("pro", save, store, print)
    f = finances_of(store.rows[0])
    check("no salary", f["salary"], 0)
    check("no years", f["years_left"], 0)
    # He must NOT be called a star. payout_band fell through to the last label on a zero salary,
    # which would have printed "star" on the page of a man about to be released on load.
    check("and he is not called a star", f["band"], points.BAND_LABELS[0])
    check("he is paid the floor", f["payout"], points.PAYOUT_FLOOR)


def test_it_does_not_spend_a_write_every_week():
    """A Supabase write per character per week, forever, for a number that rarely changes."""
    print("unchanged is not rewritten")
    save = a_real_league()
    store = FakeStore([character("c1", "Dodger Manson")])
    check("the first week writes", simweek._sync_contracts("pro", save, store, print), 1)
    check("the second does not", simweek._sync_contracts("pro", save, store, print), 0)
    check("only one write all told", len(store.writes), 1)
    # A real change must still land.
    save.players[-1].contract = [22_000_000] + [0] * 6
    check("a new deal writes again", simweek._sync_contracts("pro", save, store, print), 1)
    check("and it is the new money", finances_of(store.rows[0])["salary"], 22_000_000)


def test_a_closed_level_is_never_written_to():
    """`finances` belongs on the level he is standing in. A finished level is a record."""
    print("only the open level row")
    save = a_real_league()
    row = character("c1", "Dodger Manson")
    row["level_history"] = [{"level": "college", "to_season": 2030, "finances": {"salary": 1}},
                            {"level": "pro", "to_season": None}]
    store = FakeStore([row])
    simweek._sync_contracts("pro", save, store, print)
    check("the closed college row is untouched",
          row["level_history"][0]["finances"], {"salary": 1})
    check("the open pro row got it", row["level_history"][1]["finances"]["salary"], 9_000_000)

    # No open row at all: nothing to hang it on, and it must not invent one.
    shut = character("c2", "Dodger Manson")
    shut["level_history"] = [{"level": "pro", "to_season": 2031}]
    store2 = FakeStore([shut])
    check("a retired character is skipped",
          simweek._sync_contracts("pro", save, store2, print), 0)
    check("and no row was added", len(shut["level_history"]), 1)


def test_nothing_here_can_take_down_a_week():
    """A salary is something the website shows. It is not something the sim depends on."""
    print("it is never fatal")

    class Broken(FakeSave):
        def contract_of(self, pl):
            raise RuntimeError("codec is having a day")

    store = FakeStore([character("c1", "Dodger Manson")])
    said = []
    check("an unreadable league returns 0",
          simweek._sync_contracts("pro", Broken(a_real_league().players), store, said.append), 0)
    check("and it said why", any("salaries" in s for s in said), True)

    class MissingMan(FakeSave):
        def find(self, name, dob=None):
            raise KeyError(name)

    store = FakeStore([character("c1", "Dodger Manson")])
    check("a character not in the save is skipped, not raised",
          simweek._sync_contracts("pro", MissingMan(a_real_league().players), store, print), 0)

    class RefusingStore(FakeStore):
        def set_character_field(self, cid, field, value):
            raise RuntimeError("supabase said no")

    said = []
    check("a store that refuses does not raise",
          simweek._sync_contracts("pro", a_real_league(),
                                  RefusingStore([character("c1", "Dodger Manson")]),
                                  said.append), 0)
    check("and it said so", any("could not record" in s for s in said), True)


def test_a_pending_character_is_left_alone():
    print("only people who are actually playing")
    save = a_real_league()
    row = character("c1", "Dodger Manson")
    row["status"] = "pending"
    store = FakeStore([row])
    check("nobody recorded", simweek._sync_contracts("pro", save, store, print), 0)


def main():
    test_it_records_what_the_game_pays()
    test_the_band_is_recorded_not_recomputed()
    test_a_league_with_no_scale_says_so_rather_than_guessing()
    test_prep_and_college_are_not_given_a_pro_promise()
    test_a_bare_contract_is_recorded_as_bare()
    test_it_does_not_spend_a_write_every_week()
    test_a_closed_level_is_never_written_to()
    test_nothing_here_can_take_down_a_week()
    test_a_pending_character_is_left_alone()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  contract sync: what the game pays a character reaches the store with the band "
          "points.py computed, a league with no salary scale says so instead of inventing a "
          "verdict, a bare contract is never labelled a star, only the open level row is "
          "written, unchanged weeks cost nothing, and nothing in here can take down a sim week")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
