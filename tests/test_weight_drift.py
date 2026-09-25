"""Weight no longer climbs every offseason for life.

The 2034 offseason died when the database refused Tim Turner at 293 lbs (6'9", net 288). He had
gone 240 -> 293 over four summers without growing: weight_offset() re-measures his "build" off
fields the offseason itself rewrites, and the catch-up gap read that as "under his frame" every
year - +12 lbs a year for every adult. Only real change moves a weight now.
"""
import unittest

from commissioner import growth


def stored(height, weight):
    """What the store holds after an offseason: this year's height and weight, both rewritten."""
    return {"height_inches": height, "weight_lbs": weight, "build": "big"}


class WeightDrift(unittest.TestCase):
    def test_an_adult_who_did_not_grow_keeps_his_weight(self):
        c = stored(81, 281)
        self.assertEqual(growth.weight_step(c, 281, 81, 81, 22), 281)

    def test_ten_summers_without_growing_change_nothing(self):
        w = 240
        for age in range(19, 29):
            w = growth.weight_step(stored(80, w), w, 80, 80, age)
        self.assertEqual(w, 240)

    def test_an_inch_still_adds_its_weight(self):
        c = stored(80, 240)
        gained = growth.weight_step(c, 240, 80, 81, 22) - 240
        self.assertEqual(gained, round(growth.WEIGHT_PER_INCH))

    def test_a_teenager_still_fills_out(self):
        c = stored(72, 170)
        self.assertEqual(growth.weight_step(c, 170, 72, 72, 16) - 170, growth.MATURATION_LBS)

    def test_the_ceiling_is_the_database_net(self):
        # supabase/weight_range.sql: round((h - 60) * 4.6 + 96) + 95
        self.assertEqual(growth.weight_ceiling(81), 288)
        self.assertEqual(growth.weight_ceiling(87), round((87 - 60) * 4.6 + 96) + 95)

    def test_growth_clamps_to_the_ceiling(self):
        import inspect
        from commissioner import offseason
        src = inspect.getsource(offseason.apply_growth)
        self.assertIn("growth.weight_ceiling(now)", src)
        self.assertLess(src.index("weight_ceiling"), src.index("wants = {}"))


if __name__ == "__main__":
    unittest.main()
