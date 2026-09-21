"""Real FBPB exports from the 2029 Prep boundary rehearsal, not reconstructed HTML."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner import calendarplan, simweek

BEFORE = (ROOT / 'fixtures/calendar-boundary/before.htm').read_text(encoding='latin-1')
AFTER = (ROOT / 'fixtures/calendar-boundary/after.htm').read_text(encoding='latin-1')


class BoundaryTests(unittest.TestCase):
    def check_boundary(self, after=AFTER, stored=(187, 2028), days=33):
        return calendarplan.verified_playoff_boundary(
            BEFORE, after, stored, (186, 2028), '2029-03-18', days)

    def test_real_final_day_skips_idle_day_without_playing_playoffs(self):
        self.assertTrue(self.check_boundary())

    def test_arbitrary_extra_day_or_wrong_season_is_not_accepted(self):
        for stored in ((188, 2028), (187, 2029), None):
            self.assertFalse(self.check_boundary(stored=stored))
        self.assertFalse(self.check_boundary(days=32))

    def test_missing_results_and_played_playoff_game_are_rejected(self):
        self.assertFalse(self.check_boundary(after=BEFORE))
        self.assertFalse(self.check_boundary(after=AFTER.replace('box185-', 'missing185-')))
        # One box-score link in the playoff section is enough to reject this export.
        marker = '<tr><td class=tableheader>&nbsp;Playoffs</td></tr>'
        self.assertIn(marker, AFTER)
        played = AFTER.replace(marker, marker +
            '<td class=header>4/21/2029</td><td class=main>'
            '<a href="boxes/box187-1.htm">Royals 40, @Tulips 50</a></td>')
        self.assertFalse(self.check_boundary(after=played))

    def test_export_probe_preserves_old_pages_and_rolls_back_failed_export(self):
        for fresh, accepted in ((AFTER, True), (BEFORE, False), (None, False)):
            with self.subTest(accepted=accepted, raises=fresh is None), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                html = root / 'html'
                html.mkdir()
                (html / 'schedule.htm').write_text(BEFORE, encoding='latin-1')
                (html / 'old-box.htm').write_text('historical box')

                class Game:
                    def html_output(self, *args, **kw):
                        (html / 'new-page.htm').write_text('new')
                        if fresh is None:
                            raise RuntimeError('export failed')
                        (html / 'schedule.htm').write_text(fresh, encoding='latin-1')
                        return html

                with patch.object(simweek.ch, 'save_path', return_value=root / 'league.dat'):
                    if fresh is None:
                        with self.assertRaisesRegex(RuntimeError, 'export failed'):
                            simweek._export_verified_boundary(Game(), 'prep', (187, 2028),
                                (186, 2028), '2029-03-18', 33)
                    else:
                        result = simweek._export_verified_boundary(Game(), 'prep', (187, 2028),
                            (186, 2028), '2029-03-18', 33)
                        self.assertEqual(bool(result), accepted)
                self.assertEqual((html / 'old-box.htm').read_text(), 'historical box')
                self.assertEqual((html / 'new-page.htm').exists(), accepted)
                self.assertEqual((html / 'schedule.htm').read_text(encoding='latin-1'),
                                 AFTER if accepted else BEFORE)


if __name__ == '__main__':
    unittest.main()
