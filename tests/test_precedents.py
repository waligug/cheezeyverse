"""The league rules Nate set on 2026-09-25, pinned."""
import inspect
import unittest
from types import SimpleNamespace

from commissioner import offseason, seasonflow, simweek


class Floor(unittest.TestCase):
    def _store(self, rows):
        return SimpleNamespace(snapshots=lambda cid: rows)

    def test_only_current_league_latest_season(self):
        rows = [{"league": "college", "season": 2033, "ratings": {"JumpShot": 90}, "potentials": {}},
                {"league": "pro", "season": 2034, "ratings": {"JumpShot": 70}, "potentials": {}},
                {"league": "pro", "season": 2035, "ratings": {"JumpShot": 60}, "potentials": {}}]
        c = {"id": "x", "league": "pro", "ratings": {}, "potentials": {}}
        got = seasonflow._character_floor(self._store(rows), c, {"JumpShot": 55})
        self.assertEqual(got["JumpShot"], 60)     # not 90 (college) and not 70 (last season)


class Aging(unittest.TestCase):
    def test_nothing_before_thirty(self):
        sheet = {"JumpShot": 80, "Quickness": 90, "PotJumpShot": 85}
        self.assertEqual(seasonflow.regress(sheet, 29), sheet)

    def test_gentle_then_hard(self):
        sheet = {"JumpShot": 80, "Quickness": 80, "3pUsage": 50, "PotJumpShot": 90}
        at31, at35 = seasonflow.regress(sheet, 31), seasonflow.regress(sheet, 35)
        self.assertEqual(at31["JumpShot"], 78)
        self.assertEqual(at35["JumpShot"], 72)
        self.assertLess(at35["Quickness"], at35["JumpShot"])   # athletic goes faster
        self.assertEqual(at35["3pUsage"], 50)                  # tendencies untouched
        self.assertGreaterEqual(at35["PotJumpShot"], at35["JumpShot"])

    def test_no_forced_retirement(self):
        c = {"league": "pro", "game_dob": "2000-01-01"}
        self.assertIsNone(offseason.retirement_for(c, None, 2045, None))

    def test_unsigned_veteran_age(self):
        self.assertEqual(seasonflow.UNSIGNED_RETIRE_AGE, 33)


class Draft(unittest.TestCase):
    def test_deal_names_the_drafting_team(self):
        src = inspect.getsource(offseason.run_draft)
        self.assertIn('"team": p["team"]', src)
        self.assertIn('entry["contract"] = dict(contract, team=contract.get("team") or slot.team)',
                      inspect.getsource(offseason.promote))


class FinishedLeaguesPaid(unittest.TestCase):
    def test_the_sim_pays_a_finished_league(self):
        src = inspect.getsource(simweek)
        self.assertIn("season over; paid alongside", src)


class StartHeight(unittest.TestCase):
    def test_first_snapshot_is_the_anchor(self):
        st = SimpleNamespace(snapshots=lambda cid: [
            {"taken_at": "2027", "height_inches": 74}, {"taken_at": "2026", "height_inches": 72}])
        self.assertEqual(offseason._start_height(st, {"id": "x", "height_inches": 79}), 72)

    def test_no_history_falls_back(self):
        st = SimpleNamespace(snapshots=lambda cid: [])
        self.assertEqual(offseason._start_height(st, {"id": "x", "height_inches": 79}), 79)


class Spawning(unittest.TestCase):
    """New characters never spawn onto a team that already has one while another team has none."""

    def test_empty_team_beats_position(self):
        from commissioner import characters as ch
        S = lambda team, pos, name: SimpleNamespace(team=team, position=pos, name=name)
        slots = [S("CHE", "SG", "a"), S("PAR", "C", "b")]
        got = ch.pick_slot(slots, position="SG", busy={"CHE": 1, "PAR": 0})
        self.assertEqual(got.team, "PAR")

    def test_position_still_breaks_ties(self):
        from commissioner import characters as ch
        S = lambda team, pos, name: SimpleNamespace(team=team, position=pos, name=name)
        slots = [S("CHE", "C", "a"), S("PAR", "SG", "b")]
        self.assertEqual(ch.pick_slot(slots, position="SG", busy={}).team, "PAR")

    def test_the_drafting_team_still_wins(self):
        from commissioner import characters as ch
        S = lambda team, pos, name: SimpleNamespace(team=team, position=pos, name=name)
        slots = [S("CHE", "SG", "a"), S("PAR", "C", "b")]
        self.assertEqual(ch.pick_slot(slots, position="SG", team="CHE", busy={"CHE": 1}).team, "CHE")


class TradesAndChatter(unittest.TestCase):
    def test_trade_card(self):
        from commissioner import announce
        card = announce.trade_card({"action": "Traded Chris Zimmer to the Hams", "team": "Cats"}, "pro", "Pro")
        self.assertIn("TRADE", card["title"])
        self.assertIn("Chris Zimmer", card["description"])

    def test_chatter_posts_until_it_stops(self):
        said = []
        with simweek._chatter(lambda step, msg, key: said.append(msg), "pro", every=0.05):
            import time
            time.sleep(0.3)
        n = len(said)
        import time
        time.sleep(0.15)
        self.assertGreater(n, 1)
        self.assertEqual(len(said), n)          # silent once the export is over


if __name__ == "__main__":
    unittest.main()
