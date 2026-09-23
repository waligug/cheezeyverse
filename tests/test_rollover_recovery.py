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


if __name__ == "__main__":
    unittest.main()
