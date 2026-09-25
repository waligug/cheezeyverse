"""An economy change that starts at an offseason, not at a deploy.

Nate, 2026-09-24: "reduce it like crazy for next season". The rest of 2034 keeps its rates; the
2034 offseason applies `scheduled_settings` before it pays anybody, so its own payouts, the draft's
rookie deals and the weekly rates from then on are all on the new numbers.
"""
import unittest
from types import SimpleNamespace

from commissioner import offseason, points, seasonbonus

CUT = {"points_per_week_pro": 1, "rookie_scale": [[60, 1]], "rookie_floor": 1,
       "offseason_points": 2, "contract_payout_points": [1, 2, 3, 4], "bonus_cap_pro": 4}


class Store:
    def __init__(self, settings, characters=()):
        self.settings = dict(settings)
        self.chars = [dict(c) for c in characters]
        self.fields = {}

    def get_settings(self):
        return dict(self.settings)

    def set_setting(self, key, value):
        self.settings[key] = value

    def characters(self, league=None):
        return [c for c in self.chars if league is None or c.get("league") == league]

    def set_character_field(self, cid, field, value):
        self.fields[(cid, field)] = value


def rookie(pick, rate):
    return {"id": f"p{pick}", "first_name": "R", "last_name": str(pick), "league": "pro",
            "level_history": [{"level": "pro", "to_season": None,
                               "contract": {"pick": pick, "rate": rate}}]}


class RatesFromSettings(unittest.TestCase):
    def test_rookie_scale_row(self):
        self.assertEqual(points.rookie_rate(1), points.ROOKIE_SCALE[0][1])
        self.assertEqual(points.rookie_rate(1, CUT), 1)
        self.assertEqual(points.rookie_rate(None, CUT), 1)

    def test_payout_row(self):
        bounds = [1_000_000, 2_000_000, 10_000_000, 20_000_000]
        self.assertEqual(points.annual_payout(30_000_000, bounds, CUT), 4)
        self.assertEqual(points.annual_payout(500_000, bounds, CUT), 1)
        self.assertEqual(points.annual_payout(0, bounds, CUT), 1)
        self.assertEqual(points.annual_payout(30_000_000, bounds), points.PAYOUT_BANDS[-1][1])

    def test_per_level_cap_row(self):
        self.assertEqual(seasonbonus.cap_for("pro", CUT), 4)
        self.assertEqual(seasonbonus.cap_for("pro"), seasonbonus.BONUS_CAP_BY_LEVEL["pro"])


class Schedule(unittest.TestCase):
    def test_not_due_yet_changes_nothing(self):
        st = Store({"scheduled_settings": {"2034": CUT}, "points_per_week_pro": 2})
        got = offseason.apply_scheduled_settings(st, st.get_settings(), 2033, log=lambda m: None)
        self.assertEqual(got["points_per_week_pro"], 2)
        self.assertEqual(st.settings["points_per_week_pro"], 2)

    def test_dry_run_overlays_without_writing(self):
        st = Store({"scheduled_settings": {"2034": CUT}, "points_per_week_pro": 2})
        got = offseason.apply_scheduled_settings(st, st.get_settings(), 2034, log=lambda m: None,
                                                 dry_run=True)
        self.assertEqual(got["points_per_week_pro"], 1)
        self.assertEqual(st.settings["points_per_week_pro"], 2)
        self.assertIn("2034", st.settings["scheduled_settings"])

    def test_the_real_run_writes_pops_and_rerates_open_deals(self):
        st = Store({"scheduled_settings": {"2034": CUT, "2036": {"x": 1}}, "points_per_week_pro": 2},
                   [rookie(1, 3), rookie(9, 2)])
        got = offseason.apply_scheduled_settings(st, st.get_settings(), 2034, log=lambda m: None)
        self.assertEqual(got["offseason_points"], 2)
        self.assertEqual(st.settings["points_per_week_pro"], 1)
        self.assertEqual(st.settings["scheduled_settings"], {"2036": {"x": 1}})
        rates = {cid: v[0]["contract"]["rate"] for (cid, f), v in st.fields.items()}
        self.assertEqual(rates, {"p1": 1, "p9": 1})

    def test_the_offseason_applies_it_after_the_already_run_guard(self):
        import inspect
        src = inspect.getsource(offseason._run_offseason)
        self.assertLess(src.index("has already been run"), src.index("apply_scheduled_settings"))
        self.assertLess(src.index("apply_scheduled_settings"), src.index("run_draft("))


if __name__ == "__main__":
    unittest.main()
