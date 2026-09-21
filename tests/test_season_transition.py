"""Next-season orchestration, including a partial game rollover. No real game or store."""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner import offseason, seasonflow, simweek
from commissioner import ageout
from commissioner.saveguard import SaveLock
from commissioner.publish import publish as publishing


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.paths={k:self.root/(k+'.dat') for k in ('prep','college','pro')}
        self.backups={k:self.root/(k+'.bak') for k in self.paths}
        for path in [*self.paths.values(),*self.backups.values()]: path.write_bytes(b'old')
        self.events=[];self.years={k:2027 for k in self.paths};self.fail_key=None
        def setting(key,value): self.events.append(('setting',key,value))
        self.store=SimpleNamespace(get_settings=lambda:{'current_season':2027,'auto_publish':False},
                                   characters=lambda **kw:[],set_setting=setting)
        patches=[patch.object(simweek,'MARKER',self.root/'journal.json'),
                 patch.object(simweek,'_SIM_LOCK',SaveLock(self.root/'lock')),
                 patch.object(offseason.ch,'save_path',side_effect=self.paths.get),
                 patch.object(offseason,'back_up_every_save',return_value=self.backups),
                 patch.object(offseason.notify,'post'),
                 patch.object(offseason,'_offseason_report',return_value='done'),
                 patch.object(simweek,'_snapshot_league'),
                 patch('commissioner.calendarplan.read_league',return_value={'season':2028,'played':0,'current_date':'2028-11-02','first':'2028-11-02'}),
                 patch.object(publishing,'publish'),patch.object(publishing,'git_push'),
                 patch.object(seasonflow,'readiness',return_value={'ready':True}),
                 patch.object(seasonflow,'archive_finished',side_effect=lambda *a:self.events.append(('archive',))),
                 patch.object(ageout,'apply',return_value={'retired':[],'arrived':[],'camp_cuts':[]}),
                 patch.object(ageout,'audit',return_value={'ok':True,'over_age':[],'sizes':{}}),
                 patch.object(simweek.FBPB3,'is_running',return_value=False)]
        for p in patches:self.stack.enter_context(p)
        def develop(store, **kw):
            self.assertFalse(kw['advance_settings'])
            self.events.append(('develop',));return {'season':2027}
        self.stack.enter_context(patch.object(offseason,'_run_offseason',side_effect=develop))
        owner=self
        class League:
            def __init__(self,path): self.key=Path(path).stem
            def season_day(self): return (15,owner.years[self.key])
        class Game:
            def __init__(self):self.app=None
            def launch(self):self.app=True
            def load_save(self,name):self.key=name.split('_')[1].lower()
            def roll_over_season(self,**kw):owner.events.append(('roll',self.key))
            def sim_preseason(self):pass
            def save_game(self,path):
                Path(path).write_bytes(b'next')
                if self.key!=owner.fail_key:owner.years[self.key]=2028
            def exit_game(self,**kw):self.app=None
            def output_mdb(self,*a):owner.events.append(('mdb',self.key))
            def html_output(self,*a):owner.events.append(('html',self.key))
            @staticmethod
            def is_running():return False
        self.stack.enter_context(patch.object(seasonflow,'LeagueDat',League))
        self.stack.enter_context(patch.object(seasonflow,'FBPB3',Game))

    def test_archives_then_develops_then_verifies_all_saves_before_changing_year(self):
        result=offseason.run_offseason(self.store,rollover=True,log=lambda m:None)
        self.assertEqual(self.events[:2],[('archive',),('develop',)])
        rolls=[e for e in self.events if e[0]=='roll']
        self.assertEqual(rolls,[('roll','prep'),('roll','college'),('roll','pro')])
        self.assertGreater(self.events.index(('setting','current_season',2028)),self.events.index(('html','pro')))
        self.assertEqual(result['next_season'],2028)
        self.assertIsNone(simweek.interrupted_run())

    def test_wrong_year_mid_rollover_keeps_journal_and_does_not_claim_completion(self):
        self.fail_key='college'
        with self.assertRaisesRegex(RuntimeError,'next season opening'):
            offseason.run_offseason(self.store,rollover=True,log=lambda m:None)
        self.assertFalse(any(e[0]=='setting' for e in self.events))
        self.assertEqual(self.paths['prep'].read_bytes(),b'next')
        self.assertTrue(simweek.interrupted_run())
        self.assertNotIn(('roll','pro'),self.events)

    def test_archive_failure_stops_before_player_or_engine_changes(self):
        with patch.object(seasonflow,'archive_finished',side_effect=RuntimeError('archive failed')):
            with self.assertRaisesRegex(RuntimeError,'archive failed'):
                offseason.run_offseason(self.store,rollover=True,log=lambda m:None)
        self.assertFalse(self.events)
        self.assertIsNone(simweek.interrupted_run())

    def test_retirement_closes_history_without_losing_slot_result(self):
        calls=[]
        character=dict(id='x', first_name='Old', last_name='Player', league='pro',
                       level_history=[{'to_season':None}])
        store=SimpleNamespace(record_level=lambda *a:None,
            set_character_field=lambda *a:calls.append(a),
            retire_character=lambda *a,**kw:calls.append(kw))
        result=offseason.retire(character,2027,'game retired',store,log=lambda m:None,slot_exists=False)
        self.assertFalse(result['slot_refilled'])
        self.assertEqual(calls[0][2][0]['to_season'],2027)
        self.assertEqual(calls[1],{'release_slot':False})

    def test_unfinished_playoffs_cannot_age_players(self):
        with patch.object(seasonflow,'readiness',return_value={'ready':False,'reasons':['finish playoffs']}):
            with self.assertRaisesRegex(offseason.OffseasonError,'finish playoffs'):
                offseason.run_offseason(self.store,rollover=True,log=lambda m:None)
        self.assertFalse(self.events)
        self.assertIsNone(simweek.interrupted_run())

    def test_lifetime_floor_restores_old_losses_and_keeps_new_growth(self):
        character = {
            'id':'kid', 'ratings':{'Jumping':11, 'Quickness':40},
            'potentials':{'3pShot':63, 'JumpShot':64},
        }
        history = [{
            'ratings':{'Jumping':25, 'Quickness':38},
            'potentials':{'3pShot':73, 'JumpShot':78},
        }]
        store = SimpleNamespace(snapshots=lambda cid: history)
        live = {'Jumping':12, 'Quickness':44, '3pShot':63,
                'Pot3pShot':63, 'PotJumpShot':64}
        floor = seasonflow._character_floor(store, character, live)
        self.assertEqual(floor['Jumping'], 25)
        self.assertEqual(floor['Quickness'], 44)
        self.assertEqual(floor['Pot3pShot'], 73)
        self.assertEqual(floor['PotJumpShot'], 78)

    def test_rollover_preserves_character_body_and_records_new_engine_ratings(self):
        character = dict(id='kid', first_name='Young', last_name='Player', status='active',
                         game_dob='2011-06-15', league_player_ids={'prep':7})
        self.store.characters = lambda **kw: [character] if kw.get('league') == 'prep' else []
        fields = {}
        self.store.set_character_field = lambda cid, key, value: fields.update({key:value})
        owner = self
        class League:
            def __init__(self, path):
                self.key = Path(path).stem
                fresh = owner.years[self.key] == 2028
                values = {key:(5 if fresh else 60) for key in seasonflow.RATINGS}
                values.update({key:(2 if fresh else 80) for key in offseason.POTENTIALS})
                if fresh:
                    values[seasonflow.RATINGS[0]] = 65
                    values[seasonflow.POTENTIALS[0]] = 85
                values.update(Height=79 if fresh else 74, Weight=200 if fresh else 180)
                self.players = [SimpleNamespace(name='Young Player', dob='6/15/1990' if fresh else '6/15/2011', values=values, id=91)]
            def find(self, name, dob):
                self.asserted_dob = dob
                return self.players[0]
            def season_day(self): return (15, owner.years[self.key])
            def set(self, pl, field, value): pl.values[field] = value
        checked = []
        def commit(league, checks):
            checked.extend(checks)
            self.assertEqual(league.players[0].values['Height'],74)
            self.assertEqual(league.players[0].values['Weight'],180)
            self.assertEqual(league.players[0].values['BirthYear'],2011)
            self.assertEqual(league.players[0].values[seasonflow.RATINGS[0]], 65)
            self.assertTrue(all(league.players[0].values[key] == 60 for key in seasonflow.RATINGS[1:]))
            self.assertEqual(league.players[0].values[seasonflow.POTENTIALS[0]], 85)
            self.assertTrue(all(league.players[0].values[key] == 80 for key in seasonflow.POTENTIALS[1:]))
            return league
        with patch.object(seasonflow,'LeagueDat',League), patch.object(seasonflow.ch,'commit',side_effect=commit):
            seasonflow.rollover_saves(self.store,2027,SimpleNamespace(_update_marker=lambda fn:None),lambda m:None)
        self.assertEqual(len(checked),1)
        self.assertEqual(fields['league_player_ids'],{'prep':91})
        self.assertEqual(fields['ratings'][seasonflow.RATINGS[0]], 65)
        self.assertTrue(all(fields['ratings'][key] == 60 for key in seasonflow.RATINGS[1:]))


class PromotionCommitTests(unittest.TestCase):
    def test_second_save_failure_restores_both_before_any_store_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths={key:Path(tmp)/(key+'.dat') for key in ('prep','college')}
            for key,path in paths.items(): path.write_bytes(key.encode())
            values={field:45 for field in [*offseason.RATINGS,*offseason.POTENTIALS]}
            values.update(Height=74,Weight=180,Team=1)
            player=SimpleNamespace(name='Reserve Player',dob='6/15/2011',values=values)
            slot=SimpleNamespace(name=player.name,dob=player.dob,team='x')
            class League:
                def __init__(self,path): self.path=path; self.data=path.read_bytes(); self.players=[player]
                def find(self,*args): return player
                def teams(self): return {1:None}
            mutations=[]
            store=SimpleNamespace(characters=lambda **kw:[],activate_character=lambda *a:mutations.append(a))
            character=dict(id='kid',league='prep',first_name='Young',last_name='Player',game_dob='2011-06-15',
                           claimed_slot={'name':player.name,'dob':player.dob})
            def commit(league,checks):
                league.path.write_bytes(b'changed')
                if league.path==paths['prep']: raise RuntimeError('second save failed')
            with patch.object(offseason,'LeagueDat',League), patch.object(offseason.ch,'save_path',side_effect=paths.get), \
                 patch.object(offseason,'_manifest',return_value={}), patch.object(offseason.ch,'free_slots',return_value=[slot]), \
                 patch.object(offseason.ch,'pick_slot',return_value=slot), patch.object(offseason.ch,'stamp_character'), \
                 patch.object(offseason,'refill',return_value=True), patch.object(offseason.ch,'commit',side_effect=commit):
                with self.assertRaisesRegex(RuntimeError,'second save failed'):
                    offseason.promote(character,'college',store,log=lambda m:None)
            self.assertEqual(mutations,[])
            for key,path in paths.items(): self.assertEqual(path.read_bytes(),key.encode())

class DriverArrivalTests(unittest.TestCase):
    def test_only_a_calendar_stall_is_treated_as_offseason_arrival(self):
        from commissioner.driver.fbpb3 import FBPB3, DriverError
        game = FBPB3()
        with patch.object(game, 'sim_days', side_effect=DriverError('a message box is open')):
            with self.assertRaisesRegex(DriverError, 'message box'):
                game.advance_to_offseason(log=lambda m: None)
        with patch.object(game, 'sim_days', side_effect=DriverError('SIM DAY did not advance the calendar')):
            self.assertTrue(game.advance_to_offseason(log=lambda m: None))

if __name__=='__main__':unittest.main()
