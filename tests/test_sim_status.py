"""Offline coverage of one-card delivery, rate limits, truthful progress and the game callback."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from commissioner import simstatus as ss
from commissioner import simweek
from commissioner.driver.fbpb3 import FBPB3, DriverError


class Response:
    def __init__(self, status=200, body=None):
        self.status_code, self.headers = status, {}
        self.body = body if body is not None else {"id": "123", "channel_id": "456"}
        self.closed = False

    def json(self):
        return self.body

    def close(self):
        self.closed = True


class Session:
    def __init__(self, replies=()):
        self.replies, self.calls = list(replies), []

    def request(self, method, target, **kw):
        self.calls.append((method, target, kw))
        reply = self.replies.pop(0) if self.replies else Response()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        pass


class Transport:
    def __init__(self, failures=(), gate=None):
        self.message_id = None
        self.calls, self.times, self.failures = [], [], list(failures)
        self.called, self.gate = threading.Event(), gate
        self.closed = False

    def send(self, payload):
        self.calls.append(payload)
        self.times.append(time.monotonic())
        self.called.set()
        if self.gate:
            self.gate.wait(2)
        if self.failures:
            failure = self.failures.pop(0)
            if failure:
                raise failure
        self.message_id = "123"

    def close(self):
        self.closed = True


class StatusTests(unittest.TestCase):
    def test_unknown_stage_or_unselected_league_preserves_progress(self):
        progress = ss.RunProgress(["prep", "college"], 28)
        before = progress.at("sim", "prep", 0.5)
        for stage, league in (("sim", "pro"), ("publish", "prep"), ("new-stage", None)):
            self.assertEqual(progress.at(stage, league), before)
        self.assertGreater(progress.at("save", "prep"), before)

    def test_single_message_and_thread_query(self):
        session = Session()
        message = ss.WebhookMessage("https://discord.test/api/webhooks/1/secret?thread_id=9&wait=false", session)
        payload = ss.render({}, days=7, leagues=["prep"], elapsed=0)
        message.send(payload)
        message.send(payload)
        self.assertEqual([c[0] for c in session.calls], ["POST", "PATCH"])
        self.assertEqual(parse_qs(urlsplit(session.calls[0][1]).query), {"thread_id": ["9"], "wait": ["true"]})
        self.assertTrue(urlsplit(session.calls[1][1]).path.endswith("/messages/123"))
        self.assertEqual(parse_qs(urlsplit(session.calls[1][1]).query), {"thread_id": ["9"]})
        self.assertEqual(session.calls[1][2]["json"]["allowed_mentions"], {"parse": []})
        self.assertEqual(session.calls[0][2]["timeout"], ss.TIMEOUT)

    def test_retry_after_and_invalid_creation(self):
        reply = Response(429, {"retry_after": 1.25})
        message = ss.WebhookMessage("https://discord.test/secret", Session([reply]))
        with self.assertRaises(ss.RateLimited) as caught:
            message.send({})
        self.assertEqual(caught.exception.delay, 1.25)
        self.assertTrue(reply.closed)
        self.assertIsNone(message.message_id)
        message = ss.WebhookMessage("https://discord.test/secret", Session([Response(body={})]))
        with self.assertRaises(ValueError):
            message.send({})

    def test_progress_is_monotonic_and_reserves_completion(self):
        p = ss.RunProgress(["prep", "college", "pro"], 35)
        values = [p.at("prepare", "prep"), p.at("load", "prep")]
        values += [p.at("sim", "prep", d / 35) for d in range(36)]
        values += [p.at("prepare", "prep"), p.at("upload"), p.at("finish", fraction=1)]
        self.assertEqual(values, sorted(values))
        self.assertLess(max(values), 100)
        card = ss.render({"percent": 55, "terminal": True, "ok": False, "detail": "Stopped saving"},
                         days=35, leagues=["prep"], elapsed=65)
        self.assertIn("55%", card["embeds"][0]["description"])
        self.assertIn("stopped", card["embeds"][0]["title"])
        card = ss.render({"percent": 100, "terminal": True, "ok": True},
                         days=35, leagues=["prep"], elapsed=65)
        self.assertIn("🟩" * 16, card["embeds"][0]["description"])

    def test_long_calendar_run_shows_progress_during_roster_preparation(self):
        progress = ss.RunProgress(["prep", "college", "pro"],
                                  {"prep": 32, "college": 36, "pro": 38})
        self.assertGreater(progress.at("prepare", "prep", .15), 0)
        after_prep = progress.at("prepare", "prep", .75)
        self.assertGreater(after_prep, 1)
        self.assertGreater(progress.at("prepare", "college", .15), after_prep)

    def test_offseason_card_names_the_transition_and_never_claims_games_are_playing(self):
        card = ss.render({"percent": 55, "stage": "ageout", "detail": "Prep: new class"},
                         days=0, leagues=["prep", "college", "pro"], elapsed=754,
                         kind="offseason", label="Season 2027 -> 2028")
        embed = card["embeds"][0]
        self.assertEqual(embed["title"], "🏀 Offseason in progress")
        self.assertEqual(embed["fields"][2]["value"], "Season 2027 -> 2028")
        self.assertIn("Moving the league's old players on", embed["description"])
        self.assertNotIn("Playing the games", embed["description"])
        done = ss.render({"percent": 100, "terminal": True, "ok": True,
                          "detail": "7 grew, 51 aged out"}, days=0, leagues=["prep"],
                         elapsed=1200, kind="offseason", label="Season 2027 -> 2028")
        self.assertEqual(done["embeds"][0]["title"], "✅ Offseason complete")

    def test_slow_discord_never_blocks_sim_updates(self):
        gate = threading.Event()
        transport = Transport(gate=gate)
        status = ss.SimStatus(35, ["prep"], transport=transport, interval=.01).start()
        try:
            self.assertTrue(transport.called.wait(1))
            started = time.monotonic()
            for i in range(1000):
                status.update(percent=42, stage="sim", detail=f"day {i}", league="prep")
            status.finish(True, "All done")
            self.assertLess(time.monotonic() - started, .3)
        finally:
            gate.set()
        self.assertTrue(status.wait(2))
        self.assertEqual(len(transport.calls), 2)
        self.assertIn("All done", transport.calls[-1]["embeds"][0]["description"])
        self.assertIn("100%", transport.calls[-1]["embeds"][0]["description"])

    def test_rate_limit_coalesces_updates_and_keeps_final_result(self):
        transport = Transport([ss.RateLimited(.08)])
        status = ss.SimStatus(7, ["prep"], transport=transport, interval=.01).start()
        self.assertTrue(transport.called.wait(1))
        for i in range(30):
            status.update(percent=i, detail=str(i))
        status.finish(False, "Game stopped; check the panel")
        self.assertTrue(status.wait(2))
        self.assertEqual(len(transport.calls), 2)
        self.assertGreaterEqual(transport.times[1] - transport.times[0], .075)
        self.assertIn("Game stopped", transport.calls[-1]["embeds"][0]["description"])
        self.assertNotIn("100%", transport.calls[-1]["embeds"][0]["description"])

    def test_ambiguous_post_is_not_duplicated_or_logged_with_secret(self):
        logs = []
        transport = Transport([requests.Timeout("https://discord.test/SECRET")])
        status = ss.SimStatus(7, ["prep"], transport=transport, interval=.01, log=logs.append).start()
        self.assertTrue(status.wait(1))
        status.finish(False, "Stopped")
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("SECRET", " ".join(logs))
        self.assertTrue(transport.closed)

    def test_deleted_message_is_not_replaced(self):
        transport = Transport([None, ss.Refused(404)])
        status = ss.SimStatus(7, ["prep"], transport=transport, interval=.01, log=lambda _: None).start()
        self.assertTrue(transport.called.wait(1))
        status.finish(True, "Done")
        self.assertTrue(status.wait(1))
        self.assertEqual(len(transport.calls), 2)

    def test_no_webhook_no_worker(self):
        with patch.object(ss.notify, "url", return_value=""):
            status = ss.SimStatus(7, ["prep"]).start()
        status.update(percent=22)
        status.finish(True, "Done")
        self.assertIsNone(status._thread)
        self.assertTrue(status.wait(.01))

    def test_day_callback_requires_a_confirmed_advance(self):
        game = FBPB3()
        game.click = lambda *args: None
        game._wait_until_still = lambda **kw: None
        game._date_signature = lambda: b"date"
        game._message_boxes = lambda: []
        game._wait_for_new_day = lambda *args: True
        seen = []
        game.sim_days(3, on_day=lambda day, total: seen.append((day, total)))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])
        game.sim_days(1, on_day=lambda *_: (_ for _ in ()).throw(RuntimeError("observer")))
        game._wait_for_new_day = lambda *args: False
        seen.clear()
        with self.assertRaises(DriverError):
            game.sim_days(1, on_day=lambda *args: seen.append(args))
        self.assertEqual(seen, [])

    def test_calendar_sim_selects_exact_stop_and_waits_for_busy_button(self):
        class Picture:
            def __init__(self, value): self.value = value
            def tobytes(self): return self.value

        game = FBPB3()
        clicks, seen = [], []
        states = iter((b"ready", b"busy", b"ready", b"ready"))
        game.click = lambda xy, *args: clicks.append(xy)
        game._wait_until_still = lambda **kw: True
        game._wait_for = lambda fn, *_: fn()
        game._calendar_cell_selected = lambda *_: True
        game._message_boxes = lambda: []
        game._grab = lambda *_args, **_kw: Picture(next(states))
        signatures = iter((b"start", b"next"))
        game._date_signature = lambda: next(signatures)
        self.assertTrue(game.sim_to_date(
            2, "2029-03-18", on_day=lambda day, total: seen.append((day, total))))
        self.assertIn((793, 392), clicks)  # March 19: March 18-19 is two inclusive dates.
        self.assertIn((794, 651), clicks)
        # The screen can provide lower-bound progress, but only the saved season day can claim
        # exact completion; run_sim emits that after it validates league.dat.
        self.assertEqual(seen, [(1, 2)])

    def test_calendar_sim_cancels_playoff_warning_without_claiming_progress(self):
        class Picture:
            def tobytes(self): return b"ready"

        game = FBPB3()
        dismissed, seen = [], []
        game.click = lambda *args: None
        game._wait_until_still = lambda **kw: True
        game._wait_for = lambda fn, *_: fn()
        game._calendar_cell_selected = lambda *_: True
        game._grab = lambda *_args, **_kw: Picture()
        game._date_signature = lambda: b"start"
        game._message_boxes = lambda: ["Schedule Warning"]
        game.dismiss_message = lambda *args, **kw: dismissed.append(kw.get("button"))
        self.assertFalse(game.sim_to_date(
            2, "2029-04-28", on_day=lambda *args: seen.append(args)))
        self.assertEqual(dismissed, ["No"])
        self.assertEqual(seen, [])

    def test_wrong_calendar_landing_restores_post_prepare_checkpoint(self):
        class Game:
            closed = False
            def exit_game(self, save=False):
                self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint, live = root / "prepared.dat", root / "league.dat"
            checkpoint.write_bytes(b"ratings and requests already applied")
            live.write_bytes(b"wrong simulated date")
            game = Game()
            simweek._restore_calendar_checkpoint(game, checkpoint, live)
            self.assertTrue(game.closed)
            self.assertEqual(live.read_bytes(), checkpoint.read_bytes())


if __name__ == "__main__":
    unittest.main()
