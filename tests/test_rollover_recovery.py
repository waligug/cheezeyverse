"""The two things that broke the 2032 offseason, pinned.

1. Pro's rollover save stalled (no write in 90s) and the caller's `finally` threw away the whole
   rolled-over league. save_game now photographs the stall, clears dialogs and saves once more.
2. Dodger Manson came out of the rollover a free agent and the 20-team refusal stopped the
   put-back. On pro the placement is now rehearsed on a clone in FBPB3 before the live write.
"""
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commissioner import seasonflow
from commissioner.codec import league_dat
from commissioner.driver import fbpb3
from commissioner.driver.fbpb3 import DriverError, FBPB3


class SaveRetryTests(unittest.TestCase):
    def _game(self, outcomes, runtime=None):
        g = FBPB3.__new__(FBPB3)
        calls = []

        def once(wait, path):
            calls.append(path)
            result = outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        g._save_once = once
        g._save_stall_evidence = lambda path: {"runtime_error": runtime, "dialogs": []}
        g.dismiss_all = lambda: calls.append("dismiss")
        return g, calls

    def test_a_stalled_save_is_retried_once(self):
        g, calls = self._game([DriverError("save did not finish writing x within 90s"), None])
        with patch.object(fbpb3.time, "sleep"):
            g.save_game(path="x")
        self.assertEqual(calls, ["x", "dismiss", "x"])

    def test_a_second_stall_raises_with_the_evidence(self):
        g, _ = self._game([DriverError("save did not finish writing x within 90s")] * 2)
        with patch.object(fbpb3.time, "sleep"), self.assertRaisesRegex(DriverError, "second attempt"):
            g.save_game(path="x")

    def test_a_runtime_error_is_not_retried(self):
        g, calls = self._game([DriverError("save did not finish writing x within 90s")],
                              runtime="Run-time error '9'")
        with self.assertRaisesRegex(DriverError, "run-time error"):
            g.save_game(path="x")
        self.assertEqual(calls, ["x"])

    def test_other_failures_are_not_retried(self):
        g, calls = self._game([DriverError("save name dialog did not settle")])
        with self.assertRaisesRegex(DriverError, "did not settle"):
            g.save_game(path="x")
        self.assertEqual(calls, ["x"])


class RehearsedPlacementTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.live = self.root / "CV_Pro"
        self.live.mkdir()
        (self.live / "league.dat").write_bytes(b"live")
        (self.live / "saveinfo.dat").write_bytes(struct.pack("<2d", 0.5, 47000.0))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _fake_game(self, box_after_sim=None):
        test = self

        class Game:
            app = object()

            def launch(self): pass
            def ambiguous_saves(self): return {}

            def load_save(self, name):
                test.loaded = name
                clone = test.root / name
                test.clone_serial = struct.unpack_from("<2d", (clone / "saveinfo.dat").read_bytes())[1]
                test.clone_written = (clone / "league.dat").read_bytes()
                self.simmed = False

            def sim_days(self, n): self.simmed = True
            def _runtime_error(self): return box_after_sim if self.simmed else None
            def exit_game(self, save=False): test.exited = True
        return Game

    def _place(self, key, path, *a, **k):
        Path(path).write_bytes(b"placed")
        self.assertTrue(league_dat._REHEARSED, "the codec write must run inside rehearsed_writes")
        return "LCH"

    def test_clone_first_then_live(self):
        with patch.object(seasonflow, "_place_character", side_effect=self._place):
            got = seasonflow._place_rehearsed("pro", self.live / "league.dat", {}, "D M", "1/1/2013",
                                              2032, None, lambda m: None, self._fake_game())
        self.assertEqual(got, "LCH")
        self.assertTrue(self.loaded.startswith(seasonflow.REHEARSAL_PREFIX))
        self.assertEqual(self.clone_written, b"placed")
        self.assertEqual(self.clone_serial, 47000.0 - 30, "the clone must not tie with the original")
        self.assertEqual((self.live / "league.dat").read_bytes(), b"placed")
        self.assertFalse((self.root / self.loaded).exists(), "the clone is removed")
        self.assertFalse(league_dat._REHEARSED)

    def test_a_failed_rehearsal_never_touches_the_live_save(self):
        with patch.object(seasonflow, "_place_character", side_effect=self._place), \
             self.assertRaisesRegex(RuntimeError, "live save was not touched"):
            seasonflow._place_rehearsed("pro", self.live / "league.dat", {}, "D M", "1/1/2013",
                                        2032, None, lambda m: None,
                                        self._fake_game(box_after_sim="Run-time error '9'"))
        self.assertEqual((self.live / "league.dat").read_bytes(), b"live")
        self.assertFalse(any(p.name.startswith(seasonflow.REHEARSAL_PREFIX) for p in self.root.iterdir()))

    def test_twenty_team_saves_are_still_refused_outside_a_rehearsal(self):
        class L:
            def teams(self): return {i: {} for i in range(5, 25)}
            depth_exact = True
        with self.assertRaisesRegex(league_dat.CodecError, "20-team"):
            league_dat.LeagueDat._require_exact_depth(L(), "sign a player")
        with league_dat.rehearsed_writes():
            league_dat.LeagueDat._require_exact_depth(L(), "sign a player")


class MarketDealTests(unittest.TestCase):
    """A character with no deal left is priced off real comparables, never the $1M token."""

    def _league(self, rows):
        from types import SimpleNamespace
        from commissioner.characters import RATINGS
        players = []
        for i, (ovr, team, contract) in enumerate(rows):
            values = {f: ovr for f in RATINGS}
            values["Team"] = team
            players.append(SimpleNamespace(id=i, name=f"P{i}", values=values, contract=contract))
        return SimpleNamespace(players=players, contract_of=lambda p: p.contract)

    def test_token_deals_are_not_comparables(self):
        from commissioner import characters as ch
        rows = [(70, 18, [0] * 7)]                                      # the character
        rows += [(70, 5, [1_000_000, 0, 0, 0, 0, 0, 0])] * 20           # import tokens, same level
        rows += [(69 + k % 3, 6, [17_000_000 + k] * 4 + [0] * 3) for k in range(6)]
        L = self._league(rows)
        salary, years = ch.market_deal(L, L.players[0])
        self.assertGreater(salary, 16_000_000)
        self.assertEqual(years, 4)

    def test_no_market_means_no_price(self):
        from commissioner import characters as ch
        L = self._league([(70, 18, [0] * 7)] + [(70, 5, [1_000_000] + [0] * 6)] * 20)
        self.assertIsNone(ch.market_deal(L, L.players[0]))

    def test_free_agents_are_not_comparables(self):
        from commissioner import characters as ch
        rows = [(70, 18, [0] * 7)] + [(70, -1, [30_000_000] * 4 + [0] * 3)] * 10
        L = self._league(rows)
        self.assertIsNone(ch.market_deal(L, L.players[0]))


class DraftOnProTests(unittest.TestCase):
    def test_stamp_does_not_dress_on_a_twenty_team_save(self):
        """Dressing is refused on pro, and refusing here failed every draft pick."""
        import inspect
        from commissioner import characters as ch
        src = inspect.getsource(ch.stamp_character)
        self.assertIn("len(L.teams()) >= 20", src)
        self.assertLess(src.index("len(L.teams()) >= 20"), src.rindex("L.dress(pl)"))

    def test_the_draft_dresses_its_pro_class_rehearsed(self):
        import inspect
        from commissioner import offseason
        self.assertIn("dress_rehearsed", inspect.getsource(offseason))

    def test_dress_rehearsed_goes_through_the_clone(self):
        calls = []

        def fake(path, write, what, log, game_factory=None):
            calls.append(what)
            return ["X"]
        with patch.object(seasonflow, "rehearse", side_effect=fake):
            got = seasonflow.dress_rehearsed("p/league.dat", [("X", "1/1/2012")], log=lambda m: None)
        self.assertEqual(got, ["X"])
        self.assertIn("dressing X", calls[0])


class ProWeeklyPassTests(unittest.TestCase):
    """The weekly pass logged "could not dress" for both pros every sim, while both started
    every game. On the 20-team save it now leaves lineups and rosters to the game."""

    def _pro(self):
        from types import SimpleNamespace
        def dress(pl):
            raise AssertionError("dress must not be attempted on pro")
        return SimpleNamespace(teams=lambda: {i: {} for i in range(5, 25)}, dress=dress,
                               find=lambda *a: None)

    def test_weekly_redress_skips_pro_without_a_word(self):
        from types import SimpleNamespace
        from commissioner import simweek
        said = []
        st = SimpleNamespace(characters=lambda **kw: [{"status": "active", "first_name": "G",
                                                         "last_name": "J", "game_dob": "2012-01-01"}])
        self.assertEqual(simweek._dress_characters("pro", self._pro(), st, said.append), [])
        self.assertEqual(said, [])

    def test_roster_guard_leaves_pro_alone(self):
        from tools import protect_rosters
        with patch.object(protect_rosters, "LeagueDat", lambda path: self._pro()),              patch.object(protect_rosters, "ours", return_value=(set(), {"players": []})):
            got = protect_rosters.protect("pro")
        self.assertEqual(got["skipped"], "pro")
        self.assertEqual((got["released"], got["signed"], got["defanged"]), (0, 0, 0))


class OffseasonStateTests(RehearsedPlacementTests):
    """The 2033 offseason stopped twice: the draft-class rehearsal tried to sim a day on a save
    at END SEASON, and prep's age-out was refused at a 0.986 depth score."""

    def test_a_finished_season_is_rehearsed_by_loading_alone(self):
        from commissioner.driver.fbpb3 import OffseasonReached
        Game = self._fake_game()

        def ended(self, n):
            raise OffseasonReached("END SEASON is visible; SIM DAY has ended")
        Game.sim_days = ended
        with patch.object(seasonflow, "_place_character", side_effect=self._place):
            seasonflow._place_rehearsed("pro", self.live / "league.dat", {}, "D M", "1/1/2013",
                                        2033, None, lambda m: None, Game)
        self.assertEqual((self.live / "league.dat").read_bytes(), b"placed")

    def test_sixteen_team_saves_write_at_the_read_threshold(self):
        class L:
            def teams(self): return {i: {} for i in range(1, 17)}
            depth_exact = False
            depth_score = 0.986
        league_dat.LeagueDat._require_exact_depth(L(), "release players")

    def test_a_rehearsed_pro_write_goes_through_at_the_read_threshold(self):
        """Chris Zimmer, #1 pick, came out of the 2033 rollover teamless at a 0.998 score."""
        class L:
            def teams(self): return {i: {} for i in range(5, 25)}
            depth_exact = False
            depth_score = 0.998
        with self.assertRaises(league_dat.CodecError):
            league_dat.LeagueDat._require_exact_depth(L(), "sign a player")
        with league_dat.rehearsed_writes():
            league_dat.LeagueDat._require_exact_depth(L(), "sign a player")


class PopupRaceTests(unittest.TestCase):
    """A progress form closing mid-scan raised InvalidWindowHandle out of a rollover."""

    def _game(self, tops, kids):
        from types import SimpleNamespace
        g = FBPB3.__new__(FBPB3)
        g.main = SimpleNamespace(handle=1, descendants=kids)
        g.app = SimpleNamespace(windows=tops)
        return g

    def test_a_window_vanishing_mid_scan_reads_as_still_busy(self):
        def gone(**kw):
            raise RuntimeError("Handle 33556746 is not a vaild window handle")
        self.assertTrue(self._game(lambda **kw: [], gone)._progress_popup_visible())
        self.assertTrue(self._game(gone, lambda **kw: [])._progress_popup_visible())

    def test_nothing_up_is_nothing_up(self):
        self.assertFalse(self._game(lambda **kw: [], lambda **kw: [])._progress_popup_visible())


if __name__ == "__main__":
    unittest.main()
