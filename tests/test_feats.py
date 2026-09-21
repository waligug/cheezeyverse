"""Isolated feats detection and delivery. Never sends a real Discord message."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner import feats, notify


class FeatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "leagues" / "prep"
        self.path.mkdir(parents=True)
        games = [dict(day=1, season=2027, pts=40, reb=3),
                 dict(day=10, season=2028, pts=12, reb=10, ast=10),
                 dict(day=11, season=2028, pts=30, reb=2),
                 dict(day=12, season=2028, pts=2, fga=12, fgm=0)]
        (self.path / "games.json").write_text(json.dumps({"characters": [
            {"id": "real-player", "name": "Real Player", "games": games}]}))

    def test_last_sim_excludes_old_games_and_does_not_reset_career_highs(self):
        events = feats.collect(self.root, 2028, {"prep": 11})
        self.assertEqual([e["day"] for e in events], [12, 11])
        self.assertEqual(events[1]["feats"], ["30 points"])
        self.assertIn("0-for-12 from the field", events[0]["feats"])

    def test_season_scan_includes_triple_double(self):
        events = feats.collect(self.root, 2028)
        self.assertEqual(len(events), 3)
        self.assertIn("triple-double", events[-1]["feats"])
        self.assertTrue(all(e["season"] == 2028 for e in events))

    def test_repeat_posts_skip_successfully_delivered_events(self):
        events = feats.collect(self.root, 2028)
        with patch.object(feats, "LEDGER", self.root / "sent.json"), patch.object(feats.settings, "get", return_value="configured"), patch.object(notify, "_send", return_value=True) as send:
            self.assertEqual(feats.send(events), 3)
            self.assertEqual(feats.send(events), 0)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.kwargs["setting"], "DISCORD_FEATS_WEBHOOK_URL")
            self.assertEqual(send.call_args.args[0]["allowed_mentions"], {"parse": []})

    def test_failed_delivery_does_not_mark_sent(self):
        with patch.object(feats, "LEDGER", self.root / "sent.json"), patch.object(feats.settings, "get", return_value="configured"), patch.object(notify, "_send", return_value=False):
            with self.assertRaisesRegex(ValueError, "delivery failed"):
                feats.send(feats.collect(self.root, 2028))
            self.assertFalse((self.root / "sent.json").exists())

    def test_last_sim_uses_earliest_checkpoint_in_run(self):
        from datetime import datetime
        for stamp in ("20260921-104654", "20260921-104704", "20260921-102420"):
            path = self.root / "backups" / (stamp + "-CV_Prep")
            path.mkdir(parents=True)
            (path / "league.dat").write_bytes(stamp.encode())
        start = datetime(2026, 9, 21, 10, 46, 52).astimezone().isoformat()
        end = datetime(2026, 9, 21, 10, 54, 13).astimezone().isoformat()
        with patch.object(feats, "find_season_day", return_value=(187, 2028)) as day:
            self.assertEqual(feats.last_run_bounds(self.root, {"started_at": start, "at": end, "leagues": ["prep"], "season": 2028}), {"prep": 187})
            day.assert_called_once_with(b"20260921-104654")

    def test_real_webhook_is_unavailable_in_tests(self):
        self.assertEqual(notify.url("DISCORD_FEATS_WEBHOOK_URL"), "")

if __name__ == "__main__":
    unittest.main()
