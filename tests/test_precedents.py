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


if __name__ == "__main__":
    unittest.main()
