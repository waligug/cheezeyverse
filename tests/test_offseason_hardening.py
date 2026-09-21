"""Read-only regression tests for phase detection, reports and export fields."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner import takeaways, offseason
from commissioner.driver.fbpb3 import FBPB3, DriverError, OffseasonReached, HOTSEAT_END_SEASON, HOTSEAT_SIM_DAY

class HardeningTests(unittest.TestCase):
    def test_end_season_does_not_click_repurposed_sim_day(self):
        game = FBPB3()
        game.click = Mock()
        game._wait_until_still = Mock(return_value=True)
        game._date_signature = Mock(return_value=b"date")
        game._button_text = Mock(return_value="ENDSEASON")
        with self.assertRaises(OffseasonReached): game.sim_days(1)
        self.assertFalse(any(c.args[0] == HOTSEAT_SIM_DAY for c in game.click.call_args_list))

    def test_wrong_button_never_gets_clicked(self):
        game = FBPB3()
        game._message_boxes = Mock(return_value=[])
        game._button_text = Mock(return_value="DRAFTLOTTERY")
        game._progress_popup_visible = Mock(return_value=False)
        with self.assertRaises(DriverError): game._expect_button(HOTSEAT_SIM_DAY, "SIM DAY", timeout=.01)

    def test_stage_change_waits_for_progress_popup(self):
        game = FBPB3()
        game._message_boxes = Mock(return_value=[])
        game._progress_popup_visible = Mock(side_effect=[True, False, False])
        game._stage_signature = Mock(return_value=b"next")
        game._wait_until_still = Mock(return_value=True)
        with patch("commissioner.driver.fbpb3.time.sleep"):
            game._wait_stage_change(b"old", timeout=1)
        self.assertEqual(game._progress_popup_visible.call_count, 3)
        game._wait_until_still.assert_called_once()

    def test_export_matching_values_are_not_retyped(self):
        box = Mock(); box.window_text.return_value = "#FFFFFF"
        FBPB3()._set_export_text(box, "#FFFFFF")
        box.set_edit_text.assert_not_called(); box.type_keys.assert_not_called()

    def test_export_native_edit_verified_without_keystrokes(self):
        box = Mock(); box.window_text.side_effect = ["old", "new", "new"]
        FBPB3()._set_export_text(box, "new")
        box.set_edit_text.assert_called_once_with("new"); box.type_keys.assert_not_called()

    def test_archived_team_records_survive_next_season_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); league = root / "leagues" / "prep"; league.mkdir(parents=True)
            old = {"season":2028, "table":[{"name":"Berries","w":17,"l":13}],
                   "players":[{"name":"Chris Zimmer","team":"Berries"}]}
            (root / "overview-prep-2028.json").write_text(json.dumps(old))
            (league / "stats.json").write_text(json.dumps(dict(old, season="Season 2029", table=[])))
            (league / "games.json").write_text(json.dumps({"characters":[{"name":"Chris Zimmer", "games":[
                {"season":2028,"min":10,"won":True}, {"season":2028,"min":0,"won":False},
                {"season":2029,"min":10,"won":False}]}]}))
            store = SimpleNamespace(characters=lambda **kw: [{"first_name":"Chris", "last_name":"Zimmer"}])
            with patch.object(takeaways,"SITE",root), patch.object(takeaways,"ARCHIVE",root), patch.object(offseason,"RESULT_PATH",root/"result.json"):
                rows = takeaways.season_records(2028,store)
                self.assertEqual(len(rows),1)
                self.assertEqual((rows[0]["team_w"],rows[0]["team_l"]),(17,13))
                self.assertEqual((rows[0]["wins"],rows[0]["losses"]),(1,0))
                offseason.save_result({"season":2028,"season_records":rows})
                self.assertEqual(offseason.saved_result()["season_records"], rows)

    def test_panel_keeps_records_and_loads_archived_overview(self):
        from commissioner import app
        result = {"season":2028, "archive_only":True, "season_records":[{"name":"Chris", "team_w":17, "team_l":13}]}
        self.assertEqual(app._offseason_view(result)["season_records"], result["season_records"])
        with patch.object(app,"saved_result",return_value=result), patch.object(app,"_HISTORY_HINT",[]):
            response = app.app.test_client().get("/api/offseason/result")
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.get_json()["result"]["archive_only"])

    def test_report_prefers_frozen_takeaways(self):
        with patch.object(takeaways,"for_season",side_effect=AssertionError("new season was reread")):
            report = offseason._offseason_report({"season":2028,"season_takeaways":["Berries 17-13"]})
        self.assertIn("Berries 17-13", report)

    def test_a_preview_without_a_season_resolves_it_before_reading_records(self):
        """A preview posts NO season on purpose, and that used to be fatal.

        `app.py`'s `_season_arg` documents None as "use the store's season", so POSTing a preview
        without one is a supported call. `run_offseason` resolved the season only inside its
        `if rollover:` branch, while the two takeaways reads below run on every path - so
        `season_records(None)` hit `int(None)` and the endpoint returned an error instead of a
        preview. `for_season(None)` hid it by catching per league and logging, which is why only
        one of the two ever showed up.

        The real rollover path was never affected: it passes rollover=True, which resolved the
        season first. That is exactly why this went unnoticed.
        """
        seen = {}
        store = SimpleNamespace(get_settings=lambda: {"current_season": 2029})
        lock = SimpleNamespace(acquire=lambda blocking=False: True, release=lambda: None,
                               locked=lambda: False)
        with patch.object(offseason, "_run_offseason", lambda *a, **k: {"ok": True}),              patch.object(takeaways, "for_season",
                          lambda season, **k: seen.setdefault("takeaways", season) and None),              patch.object(takeaways, "season_records",
                          lambda season, _store: seen.setdefault("records", season) or []),              patch("commissioner.simweek._SIM_LOCK", lock):
            result = offseason.run_offseason(store, season=None, dry_run=True)
        self.assertEqual(seen.get("records"), 2029, "season_records still got None")
        self.assertEqual(seen.get("takeaways"), 2029, "for_season still got None")
        self.assertTrue(result.get("ok"))

if __name__ == "__main__": unittest.main()
