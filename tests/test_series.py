"""Playoff series pages: the archive, the summaries, and the bracket links.

    python tests/test_series.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.publish import series, restyle  # noqa: E402


def line(pid, name, team, pts, **kw):
    base = {"id": pid, "name": name, "team": team, "start": True, "min": 30, "pts": pts, "reb": 5,
            "oreb": 1, "ast": 3, "stl": 1, "blk": 0, "to": 2, "pf": 2, "fgm": pts // 2, "fga": pts,
            "ftm": 0, "fta": 0, "tpm": 0, "tpa": 0, "pm": 0}
    base.update(kw)
    return base


def game(day, home, away, hs, as_, lines, box=None):
    return {"day": day, "box": box or f"{day}-1", "home": home, "away": away, "hs": hs, "as": as_,
            "lines": lines}


def best_of_three_final():
    """A 2-1 final between two #1 seeds, Bravo winning, plus a one-game semi."""
    games = [
        game(10, "Alpha", "Delta", 80, 60, [line(1, "Ann", "Alpha", 30), line(4, "Dee", "Delta", 10)]),
        game(10, "Bravo", "Charlie", 70, 65, [line(2, "Bob", "Bravo", 20), line(3, "Cy", "Charlie", 25)]),
        # the final: Alpha home in game 1
        game(12, "Alpha", "Bravo", 90, 80, [line(1, "Ann", "Alpha", 40), line(2, "Bob", "Bravo", 12),
                                           line(5, "Bea", "Bravo", 30)]),
        game(13, "Bravo", "Alpha", 85, 70, [line(1, "Ann", "Alpha", 10), line(2, "Bob", "Bravo", 25),
                                           line(5, "Bea", "Bravo", 20)]),
        game(14, "Alpha", "Bravo", 60, 75, [line(1, "Ann", "Alpha", 20), line(2, "Bob", "Bravo", 22),
                                           line(5, "Bea", "Bravo", 18)]),
    ]
    return {"league": "prep", "season": 2040, "games": games,
            "seeds": {"Alpha": 1, "Bravo": 1, "Charlie": 2, "Delta": 2}}


class Summarise(unittest.TestCase):
    def setUp(self):
        # prep's real shape: best-of-one, best-of-one, best-of-three final
        self.out = series.summarise(best_of_three_final(), lengths=(0, 1, 1, 3))

    def test_rounds_and_names(self):
        by_id = {s["id"]: s for s in self.out["series"]}
        self.assertEqual(by_id["alpha~delta"]["round"], 1)
        self.assertEqual(by_id["alpha~bravo"]["round"], 2)
        self.assertEqual(self.out["rounds"], 3)          # lengths say three rounds, whatever was played
        self.assertEqual(by_id["alpha~bravo"]["round_name"], "Conference Finals")

    def test_final_winner_mvp_and_order(self):
        final = next(s for s in self.out["series"] if s["id"] == "alpha~bravo")
        self.assertEqual(final["length"], 1)             # round 2 of (1, 1, 3) is best-of-one...
        # ...so with round numbering from the lengths, the 2-1 series is decided at its first win
        self.assertIsNotNone(final["winner"])

    def test_three_game_series_with_its_real_length(self):
        out = series.summarise(best_of_three_final(), lengths=(0, 0, 1, 3))
        final = next(s for s in out["series"] if s["id"] == "alpha~bravo")
        self.assertEqual(final["length"], 3)
        self.assertEqual(final["winner"], "Bravo")
        # equal seeds: the winner is listed first, not game 1's home team
        self.assertEqual([t["name"] for t in final["teams"]], ["Bravo", "Alpha"])
        self.assertEqual([t["wins"] for t in final["teams"]], [2, 1])
        # game MVP comes from the team that won the game
        self.assertEqual(final["games"][0]["mvp"]["name"], "Ann")
        self.assertEqual(final["games"][1]["mvp"]["team"], "Bravo")
        # series MVP: best TOTAL game score on the series winner (Bea 30+20+18 beats Bob 12+25+22)
        self.assertEqual(final["mvp"]["name"], "Bea")
        self.assertEqual(final["mvp"]["gp"], 3)
        self.assertEqual(out["champion"], "Bravo")

    def test_undecided_series_has_no_winner_or_mvp(self):
        payload = best_of_three_final()
        payload["games"] = payload["games"][:3]      # the final at 1-0 of a best-of-three
        out = series.summarise(payload, lengths=(0, 0, 1, 3))
        final = next(s for s in out["series"] if s["id"] == "alpha~bravo")
        self.assertIsNone(final["winner"])
        self.assertIsNone(final["mvp"])
        self.assertIsNone(out["champion"])

    def test_player_totals(self):
        out = series.summarise(best_of_three_final(), lengths=(0, 0, 1, 3))
        final = next(s for s in out["series"] if s["id"] == "alpha~bravo")
        bob = next(p for p in final["players"]["Bravo"] if p["name"] == "Bob")
        self.assertEqual((bob["gp"], bob["pts"]), (3, 59))

    def test_game_score(self):
        # 20 pts, 10-20 FG, 5 reb (1 off), 3 ast, 1 stl, 2 to, 2 pf
        self.assertAlmostEqual(series.game_score(line(1, "x", "t", 20)),
                               20 + 4 - 14 + 0.7 + 1.2 + 1 + 2.1 - 0.8 - 2, places=1)


class Archive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = series.ARCHIVE
        series.ARCHIVE = Path(self.tmp.name)

    def tearDown(self):
        series.ARCHIVE = self.old
        self.tmp.cleanup()

    def test_never_shrinks(self):
        full = best_of_three_final()
        self.assertIsNotNone(series.archive(full))
        partial = dict(full, games=full["games"][:2])
        self.assertIsNone(series.archive(partial))
        kept = json.loads(series.path_for("prep", 2040).read_text(encoding="utf-8"))
        self.assertEqual(len(kept["games"]), 5)

    def test_does_not_share_statsarchives_file_name(self):
        # playoffs-<league>-<season>.json is statsarchive's postseason TOTALS. Using that name
        # once overwrote them; the per-game archive must live under its own name.
        self.assertTrue(series.path_for("pro", 2037).name.startswith("series-"))
        from commissioner import statsarchive
        self.assertNotEqual(series.path_for("pro", 2037).name,
                            statsarchive.path_for("pro", 2037, "playoffs").name)


BRACKET = """<html><body><td class=plainheader>2040 Playoff Brackets</td><table>
<tr><td align=center bgcolor=#F2F2F2>#1</td>
<td bgcolor=#F9F9F9>&#160;<a class="linkmain" href="./rosters/roster1.htm">Alpha</a></td>
<td align=center bgcolor=#F9F9F9>1</td></tr>
<tr><td rowspan=2 align=center bgcolor=#F2F2F2>#4</td>
<td rowspan=2 bgcolor=#F9F9F9>&#160;<a class="linkmain" href="./rosters/roster4.htm">Delta Force</a></td>
<td rowspan=2 align=center bgcolor=#F9F9F9>0</td></tr>
</table></body></html>"""


class BracketLinks(unittest.TestCase):
    def test_entries(self):
        self.assertEqual(restyle.bracket_entries(BRACKET), [
            {"seed": 1, "team": "Alpha", "wins": 1}, {"seed": 4, "team": "Delta Force", "wins": 0}])

    def test_wins_become_series_links(self):
        out = restyle._link_series(BRACKET, "prep")
        self.assertIn('href="../../series.html?league=prep&amp;season=2040&amp;a=Alpha&amp;b=Delta%20Force"', out)
        self.assertIn('a=Delta%20Force&amp;b=Alpha"', out)
        self.assertEqual(out.count('class="cv-series-link"'), 2)
        # nothing else on the page moved
        self.assertEqual(restyle.bracket_entries(BRACKET), [
            {"seed": 1, "team": "Alpha", "wins": 1}, {"seed": 4, "team": "Delta Force", "wins": 0}])

    def test_page_without_a_bracket_is_left_alone(self):
        page = "<html><body>no playoffs yet</body></html>"
        self.assertEqual(restyle._link_series(page, "pro"), page)


if __name__ == "__main__":
    unittest.main()
