"""The GOAT score's components (commissioner/publish/goat.py)."""
import unittest

from commissioner.publish import goat


def line(name, team, games=50, minutes=1500, points=800, **kw):
    row = {"name": name, "dob": "1/1/2000", "team": team, "Games": games, "Minutes": minutes,
           "Points": points, "Rebounds": 200, "Assists": 100, "Steals": 30, "Blocks": 20,
           "FGA": 700, "FGM": 330, "FTA": 150, "FTM": 110, "Turnovers": 80}
    row.update(kw)
    return row


class SeasonValues(unittest.TestCase):
    def test_a_season_is_scaled_to_its_own_top_ten(self):
        good = [line(f"P{i}", "A") for i in range(12)]
        broken = [line(f"P{i}", "A", points=400) for i in range(12)]   # a floored league
        a = goat.season_values(good)[("p0", "1/1/2000")][0]
        b = goat.season_values(broken)[("p0", "1/1/2000")][0]
        self.assertAlmostEqual(a, b, delta=1.0)   # equal standing in their own seasons

    def test_below_replacement_is_zero_not_negative(self):
        rows = [line(f"P{i}", "A") for i in range(10)] + [line("Bad", "A", points=10)]
        self.assertEqual(goat.season_values(rows)[("bad", "1/1/2000")][0], 0.0)


class Build(unittest.TestCase):
    def test_share_titles_and_foreign_honours(self):
        history = [(2030, {"players": [line("Star", "A", points=1500)] +
                           [line(f"Mate{i}", "A", points=500) for i in range(4)] +
                           [line(f"Other{i}", "B") for i in range(8)]})]
        honours = {"star": {"2030": ["title", "mvp"], "2025": ["mvp", "all_star"]}}
        with unittest.mock.patch.object(goat, "champions", return_value={}):
            out = goat.build("pro", history=history, playoff_history=[], honours=honours)
        star = next(p for p in out["players"] if p["name"] == "Star")
        self.assertGreater(star["load"], 0.4)                  # he is most of team A
        self.assertEqual(star["honours"], {"mvp": 1})           # 2025 was not his season here
        self.assertEqual([t["s"] for t in star["titles"]], [2030])


class Anomalies(unittest.TestCase):
    def test_a_broken_season_is_left_out(self):
        history = [(2031, {"players": [line(f"P{i}", "A") for i in range(12)]}),
                   (2032, {"players": [line(f"P{i}", "A") for i in range(12)]})]
        from commissioner import statsarchive
        with unittest.mock.patch.object(goat, "champions", return_value={}),              unittest.mock.patch.object(statsarchive, "anomaly", side_effect=lambda k, s: "broken" if s == 2031 else None):
            out = goat.build("pro", history=history, playoff_history=[], honours={})
        self.assertEqual(out["excluded"], [2031])
        self.assertTrue(all(x["s"] == 2032 for p in out["players"] for x in p["seasons"]))


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
