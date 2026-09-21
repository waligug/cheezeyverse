"""Failure-injection checks: only temporary saves and fake stores; no game or network."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner import recovery, simweek, offseason
from commissioner.saveguard import SaveLock
from tools import offsite_backup as backup


# A season is passed EXPLICITLY because `_store()` has no `get_settings()`.
#
# It used to be load-bearing for a different reason: run_offseason resolved a missing season only
# on the `rollover=True` path, so a None season reached takeaways.season_records() and raised
# int(None) BEFORE the fault each test below injects - which is how four of these tests spent a
# while erroring instead of testing anything. That gap is fixed (`cd679e008` resolves the season
# at the top of the try) and `tests/test_offseason_hardening.py` pins it, so this is no longer
# working around a bug - it is just giving the stub what the real store would have.
SEASON = 2028


def _store(**attrs):
    """A stand-in store for run_offseason.

    It grew a `characters()` requirement when `takeaways.season_records(season, store)` was added
    to run_offseason, and a bare SimpleNamespace then raised AttributeError BEFORE the injected
    fault each test below is actually asserting - so four recovery tests errored out and stopped
    checking that a failed offseason restores the saves and releases the lock. The stub has to
    carry every attribute the real path touches, or the tests pass the wrong reason or none.
    """
    attrs.setdefault("characters", lambda **_kw: [])
    attrs.setdefault("pending_characters", lambda: [])
    return SimpleNamespace(**attrs)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(simweek, 'MARKER', self.root / 'journal.json'))
        self.stack.enter_context(patch.object(simweek, '_SIM_LOCK', SaveLock(self.root / 'lock')))
        self.stack.enter_context(patch.object(simweek.FBPB3, 'is_running', return_value=False))
        self.stack.enter_context(patch.object(offseason.notify, 'post'))
        self.paths, self.copies = {}, {}
        for key in ('prep', 'college', 'pro'):
            self.paths[key] = self.root / (key + '.dat')
            self.copies[key] = self.root / (key + '.bak')
            self.paths[key].write_bytes(b'before')
            self.copies[key].write_bytes(b'before')
        self.stack.enter_context(patch.object(offseason, 'back_up_every_save', return_value=self.copies))
        self.stack.enter_context(patch.object(offseason.ch, 'save_path', side_effect=self.paths.get))

    def test_atomic_write_failure_preserves_old_journal(self):
        simweek._mark_running(['prep'], 7, 2027)
        old = simweek.MARKER.read_bytes()
        with patch.object(recovery.os, 'replace', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                simweek._mark_saved('prep', 'backup')
        self.assertEqual(old, simweek.MARKER.read_bytes())
        self.assertFalse(list(self.root.glob('*.tmp')))
        for text in ('{', '[]', '{}', '{"started_at":"now","unconfirmed":{}}'):
            simweek.MARKER.write_text(text)
            self.assertTrue(simweek.interrupted_run()['read_error'])

    def test_early_offseason_failure_restores_and_releases(self):
        def fail(store, **kw):
            self.paths['prep'].write_bytes(b'grown')
            raise RuntimeError('growth')
        with patch.object(offseason, '_run_offseason', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'growth'):
                offseason.run_offseason(_store(), season=SEASON, log=lambda m: None)
        self.assertEqual(self.paths['prep'].read_bytes(), b'before')
        self.assertIsNone(simweek.interrupted_run())
        self.assertFalse(simweek._SIM_LOCK.locked())

    def test_late_offseason_failure_keeps_both_sides_and_blocks_force(self):
        settings = {}
        def put(key, value):
            settings[key] = value
            if key == 'current_season':
                raise OSError('reply lost after commit')
        def fail(store, **kw):
            self.paths['prep'].write_bytes(b'grown')
            store.set_setting('last_offseason', 2027)
            store.set_setting('current_season', 2028)
        with patch.object(offseason, '_run_offseason', side_effect=fail):
            with self.assertRaises(OSError):
                offseason.run_offseason(_store(set_setting=put), season=SEASON, log=lambda m: None)
        self.assertEqual(settings['current_season'], 2028)
        self.assertEqual(self.paths['prep'].read_bytes(), b'grown')
        self.assertTrue(simweek.interrupted_run()['store_writes_started'])
        before = simweek.MARKER.read_bytes()
        with self.assertRaisesRegex(offseason.OffseasonError, 'blocked'):
            offseason.run_offseason(_store(), season=SEASON, force=True)
        self.assertEqual(before, simweek.MARKER.read_bytes())

    def test_swallowed_database_failure_still_blocks_retries(self):
        def bad(*args):
            raise OSError('lost reply')
        def caught(store, **kw):
            try:
                store.set_character_field('id', 'college_years', 2)
            except OSError:
                pass
            return {}
        with patch.object(offseason, '_run_offseason', side_effect=caught):
            with self.assertRaisesRegex(offseason.OffseasonError, 'database write failed'):
                offseason.run_offseason(_store(set_character_field=bad), season=SEASON, log=lambda m: None)
        self.assertIsNotNone(simweek.interrupted_run())

    def test_incomplete_manifest_is_never_healthy(self):
        dest = self.root / 'snapshots'
        folder = dest / '20990101'
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text(json.dumps({
            'taken_at': '2099-01-01T00:00:00+00:00',
            'files': [{'label': 'repo/.env', 'bytes': 1, 'sha256': 'abc'}]}))
        self.assertEqual(backup.check(dest, log=lambda m: None), 1)

    def test_successful_offseason_notification_cannot_undo_completion(self):
        def finish(store, **kw):
            self.paths['prep'].write_bytes(b'grown')
            store.set_setting('current_season', 2028)
            return {}
        with patch.object(offseason, '_run_offseason', side_effect=finish), patch.object(offseason, '_offseason_report', side_effect=RuntimeError('notification')):
            offseason.run_offseason(_store(set_setting=lambda *a: None), season=SEASON)
        self.assertIsNone(simweek.interrupted_run())
        self.assertEqual(self.paths['prep'].read_bytes(), b'grown')

    def test_backup_refuses_missing_busy_or_open_saves(self):
        items = [(k + '/league.dat', p) for k, p in self.paths.items()]
        with patch.object(backup, 'sources', return_value=items), patch.object(backup, 'required_saves', return_value={k for k, p in items}), patch.object(backup, 'SAVE_LOCK', simweek._SIM_LOCK):
            dest = self.root / 'snapshots'
            good = backup.take(dest, log=lambda m: None)
            self.assertIsNotNone(good)
            self.paths['pro'].unlink()
            self.assertIsNone(backup.take(dest, keep=1, log=lambda m: None))
            self.assertTrue(good.exists())
            self.paths['pro'].write_bytes(b'before')
            simweek._SIM_LOCK.acquire()
            try:
                self.assertIsNone(backup.take(dest, log=lambda m: None))
            finally:
                simweek._SIM_LOCK.release()
            with patch.object(backup.FBPB3, 'is_running', return_value=True):
                self.assertIsNone(backup.take(dest, log=lambda m: None))
            real = backup.shutil.copy2
            def mutate(src, dst):
                real(src, dst)
                self.paths['prep'].write_bytes(b'changed')
            with patch.object(backup.shutil, 'copy2', side_effect=mutate):
                self.assertIsNone(backup.take(dest, log=lambda m: None))
            self.assertEqual(len(backup.snapshots(dest)), 1)

    def test_process_lock_excludes_other_process_and_releases(self):
        lock = simweek._SIM_LOCK
        program = 'from commissioner.saveguard import SaveLock; import sys; l=SaveLock(sys.argv[1]); ok=l.acquire(); print(ok); l.release() if ok else None'
        def attempt():
            return subprocess.check_output([sys.executable, '-B', '-c', program, str(lock.path)], cwd=Path(__file__).resolve().parents[1], text=True).strip()
        self.assertTrue(lock.acquire())
        try:
            self.assertEqual(attempt(), 'False')
        finally:
            lock.release()
        self.assertEqual(attempt(), 'True')

    @unittest.skipUnless(os.environ.get("CV_TEST_SAVE"), "requires an explicit copied save fixture")
    def test_real_codec_partial_stamp_restores_exact_bytes(self):
        from commissioner.codec.league_dat import LeagueDat, RATINGS
        league = LeagueDat(Path(os.environ["CV_TEST_SAVE"]))
        player = next(p for p in league.players if p.values["Team"] >= 1)
        slot = SimpleNamespace(name=player.name, dob=player.dob, team='ABC', position='C')
        character = dict(id='bad', first_name='Recovery', last_name='Probe',
                         height_inches=70, weight_lbs=160, league='prep', position='C',
                         ratings={RATINGS[0]:40}, potentials={'NotARating':60})
        store = SimpleNamespace(pending_characters=lambda:[character], characters=lambda **kw:[])
        original = bytes(league.data)
        identities = [(p.id, p.name, p.dob) for p in league.players]
        messages = []
        with patch.object(simweek, '_manifest', return_value={}), patch.object(simweek.ch, 'free_slots', return_value=[slot]):
            done, expected, writes = simweek._activate_pending('prep', league, store, messages.append)
        self.assertFalse(done)
        self.assertTrue(any('potentials keyed' in m for m in messages), messages)
        self.assertEqual(bytes(league.data), original)
        self.assertEqual([(p.id, p.name, p.dob) for p in league.players], identities)

    def test_failed_stamp_cannot_contaminate_next_success(self):
        rows = [SimpleNamespace(name='Original', values={'Team': 1}, id=1), SimpleNamespace(name='Reserve', values={'Team': 1}, id=2)]
        league = SimpleNamespace(data=bytearray(b'00'), players=rows, by_id={p.id:p for p in rows}, season_day=lambda: (1,2027), find=lambda *a: rows[0])
        slots = [SimpleNamespace(name=p.name, dob='01/01/2000', team='ABC') for p in rows]
        chars = [dict(id=str(i), first_name=n, last_name='Player', height_inches=70, weight_lbs=160, league='prep') for i,n in enumerate(('Bad','Good'))]
        store = SimpleNamespace(pending_characters=lambda: chars, characters=lambda **kw: [])
        def stamp(target, slot, c):
            index = 0 if c['first_name'] == 'Bad' else 1
            target.data[index] = 49
            target.players[index].name = c['first_name']
            if index == 0:
                raise ValueError('invalid potential after earlier mutations')
        with patch.object(simweek, '_manifest', return_value={}), patch.object(simweek.ch, 'free_slots', return_value=slots), patch.object(simweek.ch, 'pick_slot', side_effect=lambda available,*a,**kw: available[0]), patch.object(simweek.ch, 'codec_dob', side_effect=lambda x:x), patch.object(simweek.ch, 'stamp_character', side_effect=stamp):
            done, expected, writes = simweek._activate_pending('prep', league, store, lambda m: None)
        self.assertEqual([c['first_name'] for c in done], ['Good'])
        self.assertEqual(league.data, b'01')
        self.assertEqual(league.players[0].name, 'Original')
        self.assertIs(league.by_id[1], league.players[0])

if __name__ == '__main__':
    unittest.main()
