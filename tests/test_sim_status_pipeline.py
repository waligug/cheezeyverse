"""Run the real orchestration against fake game/store/Discord and temporary save files."""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner import simweek
from commissioner.publish import publish as publishing
from tools import protect_rosters


class Status:
    instances = []

    def __init__(self, days, leagues):
        self.updates, self.result = [], None
        self.instances.append(self)

    def start(self):
        return self

    def update(self, **kw):
        self.updates.append(kw)

    def finish(self, ok, text):
        self.result = ok, text


class Store:
    def __init__(self, fail_log=False):
        self.records, self.fail_log = [], fail_log

    def get_settings(self):
        return {"current_season": 2027, "current_week": 5, "auto_publish": True}

    def characters(self):
        return []

    def grant_week_points(self, **kw):
        return 0

    def set_setting(self, *args):
        pass

    def record_run(self, row):
        if self.fail_log:
            raise OSError("history disk failure")
        self.records.append(row)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        Status.instances = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.store, self.day_failure = Store(), False
        self.paths = {}
        for key in ("prep", "college", "pro"):
            folder = self.temp / key
            (folder / "html").mkdir(parents=True)
            self.paths[key] = folder / "league.dat"
            self.paths[key].write_bytes(b"test save; not game data")
        owner = self

        class Game:
            @staticmethod
            def is_running():
                return False

            def launch(self):
                return self

            def load_save(self, *args, **kw):
                pass

            def sim_days(self, days, on_day=None):
                for day in range(1, days + 1):
                    on_day(day, days)
                    if owner.day_failure:
                        raise RuntimeError("the game stopped")

            def save_game(self, **kw):
                pass

            def html_output(self, name, **kw):
                return owner.temp / "prep" / "html"

            def output_mdb(self, *args):
                pass

            def exit_game(self, **kw):
                pass

        stubs = {
            "store": lambda: self.store, "FBPB3": Game, "SimStatus": Status,
            "BACKUPS": self.temp / "backups", "MARKER": self.temp / "marker.json",
            "LeagueDat": lambda path: object(),
            "_refuse_to_cross_the_season": lambda *a, **k: None,
            "_sync_teams": lambda *a: 0, "_dress_characters": lambda *a: [],
            "_activate_pending": lambda *a, **k: ([], [], []),
            "_apply_requests": lambda *a: ([], []),
            "_wait_for_game_to_exit": lambda: True, "_season_blocks": lambda *a: [],
            "_stage": lambda *a: "Regular season", "_champion": lambda *a: None,
            "_snapshot_league": lambda *a: 0, "publish": lambda *a: [],
        }
        for name, value in stubs.items():
            self.stack.enter_context(patch.object(simweek, name, value))
        self.stack.enter_context(patch.object(simweek.ch, "save_path", lambda key: self.paths[key]))
        self.stack.enter_context(patch.object(protect_rosters, "protect", lambda *a, **k: {}))
        self.push = self.stack.enter_context(patch.object(publishing, "git_push"))
        self.stack.enter_context(patch.object(simweek.notify, "post", side_effect=AssertionError("duplicate Discord post")))

    def test_success_one_card_actual_days_monotonic_progress(self):
        events = []
        result = simweek.run_sim(days=3, on_step=events.append)
        self.assertTrue(result["ok"])
        self.assertEqual(len(Status.instances), 1)
        self.assertTrue(Status.instances[0].result[0])
        percents = [e["pct"] for e in events]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)
        self.assertEqual(sum("day " in e["message"] and "completed" in e["message"] for e in events), 9)
        self.assertEqual(len(self.store.records), 1)
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_game_failure_finishes_same_card_at_partial_progress(self):
        self.day_failure = True
        with self.assertRaisesRegex(RuntimeError, "game stopped"):
            simweek.run_sim(days=3)
        self.assertEqual(len(Status.instances), 1)
        status = Status.instances[0]
        self.assertFalse(status.result[0])
        self.assertIn("game stopped", status.result[1])
        self.assertLess(max(u.get("percent", 0) for u in status.updates), 100)
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_failed_publish_is_visible_in_final_result(self):
        self.push.side_effect = RuntimeError("offline")
        self.assertTrue(simweek.run_sim(days=1)["ok"])
        self.assertIn("did NOT publish", Status.instances[0].result[1])

    def test_failed_log_does_not_leave_a_success_card_or_lock(self):
        self.store.fail_log = True
        with self.assertRaisesRegex(OSError, "history"):
            simweek.run_sim(days=1)
        self.assertFalse(Status.instances[0].result[0])
        self.assertIn("history", Status.instances[0].result[1])
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_stale_marker_survives_repeated_refusal_and_dry_run(self):
        simweek._mark_running(["prep"], 7, 2027)
        before = simweek.MARKER.read_bytes()
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "never finished"):
                simweek.run_sim(days=1)
            self.assertEqual(simweek.MARKER.read_bytes(), before)
            self.assertFalse(simweek._SIM_LOCK.locked())
        simweek.run_sim(days=1, dry_run=True)
        self.assertEqual(simweek.MARKER.read_bytes(), before)
        self.assertEqual(Status.instances, [])

    def test_dry_run_and_refusal_never_create_cards(self):
        simweek.run_sim(days=1, dry_run=True)
        self.assertEqual(Status.instances, [])
        with patch.object(simweek, "_refuse_to_cross_the_season", side_effect=simweek.SeasonEnd("refused")):
            with self.assertRaises(simweek.SeasonEnd):
                simweek.run_sim(days=999)
        self.assertEqual(Status.instances, [])
        self.assertFalse(simweek._SIM_LOCK.locked())


if __name__ == "__main__":
    unittest.main()
