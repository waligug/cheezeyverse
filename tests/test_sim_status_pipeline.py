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
        self.updates, self.result, self.waited = [], None, None
        self.instances.append(self)

    def start(self):
        return self

    def update(self, **kw):
        self.updates.append(kw)

    def finish(self, ok, text):
        self.result = ok, text

    def wait(self, timeout=None):
        # run_sim waits for the terminal card to actually go out: finish() only arms it and the
        # worker that sends it is a daemon thread, so a caller that returns and exits kills it
        # mid-flight. Without this the stub raised AttributeError on every run - swallowed, but
        # it meant the wait was never exercised here.
        self.waited = timeout
        return True


class Store:
    def __init__(self, fail_log=False):
        self.records, self.fail_log = [], fail_log

    def get_settings(self):
        return {"current_season": 2027, "current_week": 5, "auto_publish": True}

    def characters(self):
        return []

    def grant_week_points(self, **kw):
        self.paid = getattr(self, "paid", [])
        self.paid.append((kw.get("league"), kw.get("weeks")))
        return 0

    def set_setting(self, *args):
        pass

    def record_run(self, row):
        if self.fail_log:
            raise OSError("history disk failure")
        self.records.append(row)
        return row


class PipelineTests(unittest.TestCase):
    def setUp(self):
        Status.instances = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # AN ISOLATED LOCK, NEVER THE LIVE UNIVERSE'S. `_SIM_LOCK` is a cross-process file lock,
        # so running this suite while the panel is simming made it fail with "a sim is already
        # running" - a red test that means nothing is worse than no test, and this one cried wolf
        # three times in one evening. What it actually checks is that run_sim releases whatever
        # lock it was given; it does not need the real one. Same trick as test_season_boundary.
        from commissioner.saveguard import SaveLock
        self.stack.enter_context(patch.object(
            simweek, "_SIM_LOCK", SaveLock(self.temp / ".test-sim.lock")))
        self.store, self.day_failure = Store(), False
        self.simulated, self.fast = [], []
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
                owner.simulated.append(days)
                for day in range(1, days + 1):
                    on_day(day, days)
                    if owner.day_failure:
                        raise RuntimeError("the game stopped")
                    if getattr(owner, "season_ends_after", None) == day:
                        from commissioner.driver.fbpb3 import OffseasonReached
                        raise OffseasonReached("END SEASON is visible; SIM DAY has ended")

            def sim_to_date(self, days, start_date, on_day=None):
                owner.fast.append((days, start_date))
                return True

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
            "_stage": lambda *a: "Regular season", "_champion": lambda *a, **k: None,  # **k: _champion takes rounds=
            "_snapshot_league": lambda *a: 0, "publish": lambda *a: [],
        }
        for name, value in stubs.items():
            self.stack.enter_context(patch.object(simweek, name, value))
        self.stack.enter_context(patch.object(simweek.ch, "save_path", lambda key: self.paths[key]))
        self.stack.enter_context(patch.object(protect_rosters, "protect", lambda *a, **k: {}))
        self.push = self.stack.enter_context(patch.object(publishing, "git_push"))
        self.stack.enter_context(patch.object(simweek.notify, "post", side_effect=AssertionError("duplicate Discord post")))

    def test_a_finished_postseason_moves_on_to_the_next_league(self):
        # 2026-09-23: "Play the whole playoffs" asked for seventy days so each league plays to its
        # own champion. Prep's bracket ended first, END SEASON replaced SIM DAY, sim_days raised,
        # and the whole run died there - pro never played a game. It is the stop, not a failure.
        self.season_ends_after = 4
        result = simweek.run_sim(days=70, allow_season_end=True, on_step=lambda e: None)
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.simulated), 3, "every league must get its turn")
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_a_finished_postseason_is_still_an_error_without_season_end(self):
        # The guard refuses any run that would cross the season boundary, so reaching it on an
        # ordinary week means that guard miscounted - which must not pass silently.
        from commissioner.driver.fbpb3 import OffseasonReached
        self.season_ends_after = 2
        with self.assertRaises(OffseasonReached):
            simweek.run_sim(days=7, on_step=lambda e: None)
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_points_pay_for_the_days_played_not_the_days_asked(self):
        # 2031's one-shot playoff run asked for 55 days and paid every character EIGHT weeks,
        # for a postseason that is about a week long in college. Nine days played is one week.
        calls = {"n": 0}

        def season_day(_data):
            calls["n"] += 1
            return (200, 2027) if calls["n"] % 2 else (209, 2027)   # before, then after
        with patch.object(simweek, "_played_day", side_effect=season_day):
            result = simweek.run_sim(days=70, allow_season_end=True, on_step=lambda e: None)
        self.assertTrue(result["ok"])
        weeks = {league: n for league, n in self.store.paid}
        self.assertEqual(weeks, {"prep": 1, "college": 1, "pro": 1},
                         f"paid {weeks} for nine days played out of seventy asked")

    def test_success_one_card_actual_days_monotonic_progress(self):
        events = []
        result = simweek.run_sim(days=3, on_step=events.append)
        self.assertTrue(result["ok"])
        self.assertEqual(len(Status.instances), 1)
        self.assertTrue(Status.instances[0].result[0])
        self.assertIn("GitHub Pages deployment pending", Status.instances[0].result[1])
        self.assertNotIn("site is live", Status.instances[0].result[1])
        percents = [e["pct"] for e in events]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)
        self.assertEqual(sum("day " in e["message"] and "completed" in e["message"] for e in events), 9)
        self.assertEqual(len(self.store.records), 1)
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_recap_persists_observed_dates_points_and_growth(self):
        from commissioner import calendarplan, seasonflow
        before = {"current_date": "2029-04-19", "games": [{"played": False}]}
        after = {"current_date": "2029-04-21", "games": [{"played": True}]}
        with patch.object(calendarplan, "read_league", side_effect=[before, after]), \
             patch.object(seasonflow, "capture_character_progress", return_value={"c": {"JumpShot": 40}}), \
             patch.object(seasonflow, "protect_character_progress", return_value=[
                 {"id": "c", "name": "Real Player", "values": {"JumpShot": 42}, "changed": {}}]):
            result = simweek.run_sim(leagues=["prep"], days=1)
        recap = self.store.records[-1]["summary"]
        self.assertEqual(recap["dates"]["prep"]["to"], "2029-04-21")
        self.assertEqual(recap["dates"]["prep"]["games_played"], 1)
        self.assertEqual(recap["growth"][0]["ratings"], {"JumpShot": [40, 42]})
        self.assertEqual(recap["points"], [{"league": "prep", "per_player": 1, "players": 0}])
        self.assertTrue(result["ok"])

    def test_game_failure_finishes_same_card_at_partial_progress(self):
        self.day_failure = True
        with self.assertRaisesRegex(RuntimeError, "game stopped"):
            simweek.run_sim(days=3)
        self.assertEqual(len(Status.instances), 1)
        status = Status.instances[0]
        self.assertFalse(status.result[0])
        self.assertIn("game stopped", status.result[1])
        self.assertIsNotNone(simweek.interrupted_run())
        self.assertLess(max(u.get("percent", 0) for u in status.updates), 100)
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_calendar_counts_share_one_pipeline_and_status(self):
        result = simweek.run_sim(days=5, days_by_league={"prep":2,"college":3,"pro":5})
        self.assertEqual(self.simulated,[2,3,5])
        self.assertEqual(result["days_by_league"],{"prep":2,"college":3,"pro":5})
        self.assertEqual(len(Status.instances),1)
        self.assertEqual(self.store.records[0]["days_by_league"]["prep"],2)

    def test_calendar_jump_that_stops_short_is_finished_with_exact_daily_steps(self):
        # Initial stale-plan check says day 10, the fast save lands on 12, and one SIM DAY
        # reaches the requested 13. The run must continue instead of rejecting correctable FBPB
        # SIM TO GAME semantics as a corrupt save.
        with patch("commissioner.codec.league_dat.find_season_day",
                   side_effect=[(10, 2028), (12, 2028), (13, 2028)]):
            result = simweek.run_sim(
                leagues=["prep"], days=3, days_by_league={"prep": 3},
                expected_states={"prep": (10, 2028)},
                start_dates={"prep": "2029-04-17"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.fast, [(3, "2029-04-17")])
        self.assertEqual(self.simulated, [1])

    def test_calendar_jump_that_overshoots_restores_and_replays_exactly(self):
        with patch("commissioner.codec.league_dat.find_season_day",
                   side_effect=[(10, 2028), (14, 2028), (13, 2028)]):
            result = simweek.run_sim(
                leagues=["prep"], days=3, days_by_league={"prep": 3},
                expected_states={"prep": (10, 2028)},
                start_dates={"prep": "2029-04-17"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.fast, [(3, "2029-04-17")])
        self.assertEqual(self.simulated, [3])

    def test_verified_playoff_setup_skip_continues_without_replaying(self):
        with patch('commissioner.codec.league_dat.find_season_day',
                   side_effect=[(153, 2028), (185, 2028), (187, 2028)]), \
                patch.object(simweek, '_export_verified_boundary',
                             return_value=self.temp / 'prep' / 'html') as verify:
            result = simweek.run_sim(
                leagues=['prep'], days=33, days_by_league={'prep':33},
                expected_states={'prep':(153,2028)}, start_dates={'prep':'2029-03-18'})
        self.assertTrue(result['ok'])
        self.assertEqual(self.simulated, [1])
        verify.assert_called_once()
        self.assertEqual(result['calendar_landings']['prep']['actual_day'],187)
        self.assertFalse(simweek.MARKER.exists())

    def test_unverified_boundary_still_restores_and_blocks(self):
        with patch('commissioner.codec.league_dat.find_season_day',
                   side_effect=[(153,2028),(185,2028),(187,2028),(187,2028)]), \
                patch.object(simweek, '_export_verified_boundary', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'restored to its pre-sim checkpoint'):
                simweek.run_sim(leagues=['prep'], days=33, days_by_league={'prep':33},
                    expected_states={'prep':(153,2028)}, start_dates={'prep':'2029-03-18'})
        self.assertTrue(simweek.MARKER.exists())
        self.push.assert_not_called()

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
        self.assertIsNotNone(simweek.interrupted_run())
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_silent_history_failure_retains_journal(self):
        with patch.object(self.store, "record_run", return_value=None):
            with self.assertRaisesRegex(OSError, "not recorded"):
                simweek.run_sim(days=1)
        self.assertIsNotNone(simweek.interrupted_run())
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
