"""Schedule dates, proportional targets and stale-preview refusal; no game starts."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.calendarplan import ScheduleParser, plan, CalendarError
from commissioner import app as panel


def league(key, dates, current='2028-02-28'):
    return dict(key=key, season=2027, day=133, current_date=current, first=min(dates),
                last=max(dates), games=[dict(date=d, phase='Regular Season') for d in dates])


class CalendarTests(unittest.TestCase):
    def test_fraction_uses_games_and_includes_target_day(self):
        data = dict(token='t', leagues=[league('prep',['2028-02-28','2028-02-29','2028-03-02','2028-03-03']),
                     league('pro',['2028-02-28','2028-03-01','2028-03-05','2028-03-09'])])
        result = plan(data,'prep','2028-02-29')
        self.assertEqual(result['percent'],50)
        self.assertEqual([r['days'] for r in result['leagues']],[2,3])
        self.assertEqual(result['leagues'][1]['through'],'2028-03-01')

    def test_never_rewinds_a_league(self):
        data = dict(token='t',leagues=[league('prep',['2028-02-28','2028-03-01']),
                    league('pro',['2028-02-28','2028-03-01'],current='2028-03-02')])
        self.assertEqual(plan(data,'prep','2028-03-01')['leagues'][1]['days'],0)

    def test_rejects_past_invalid_and_beyond_regular_season(self):
        data=dict(token='t',leagues=[league('prep',['2028-02-28','2028-03-01'])])
        for target in ('2028-02-27','2028-03-02','garbage'):
            with self.assertRaises(CalendarError): plan(data,'prep',target)
        with self.assertRaises(CalendarError): plan(data,'unknown','2028-03-01')

    def test_parser_tracks_scores_dates_and_future_matchups(self):
        parser=ScheduleParser()
        parser.feed('<td class=tableheader>Regular Season</td><td class=header>&nbsp;2/28/2028</td>'
                    '<td class=main><a href="./boxes/box133-1.htm">@Cats 90, Dogs 80</a></td>'
                    '<td class=header>2028-02-29</td><td class=main><a>Cats</a> @ <a>Dogs</a></td>')
        self.assertEqual(parser.games[0]['teams'],['Cats','Dogs'])
        self.assertTrue(parser.games[0]['played'])
        self.assertFalse(parser.games[1]['played'])
        self.assertEqual(parser.games[1]['date'],'2028-02-29')
        self.assertEqual(parser.anchors,[date(2028,2,28)-timedelta(days=132)])

    def test_stale_plan_never_starts_worker(self):
        client=panel.app.test_client()
        with patch('commissioner.calendarplan.snapshot',return_value={'token':'new'}), patch.object(panel.threading,'Thread') as thread:
            response=client.post('/api/calendar/start',json={'token':'old','reference':'prep','target':'2028-03-01'})
        self.assertEqual(response.status_code,409)
        thread.assert_not_called()
        self.assertFalse(panel._SIM_LOCK.locked())

    def test_worker_uses_each_leagues_exact_days_and_stops_on_failure(self):
        run=panel.SimRun(['prep','pro'],5,False,kind='calendar')
        run.calendar_plan={'leagues':[dict(key='prep',days=2,through='2028-03-01',expected_day=10,season=2027,**{'from':'2028-02-28'}),
                                     dict(key='pro',days=5,through='2028-03-04',expected_day=20,season=2027,**{'from':'2028-02-28'})]}
        with patch.object(panel,'run_sim',return_value={'ok':True}) as sim, patch.object(panel,'_finish'):
            panel._calendar_worker(run)
        self.assertEqual(sim.call_count,1)
        self.assertEqual(sim.call_args.kwargs['days_by_league'],{'prep':2,'pro':5})
        self.assertEqual(sim.call_args.kwargs['expected_states']['pro'],(20,2027))
        self.assertEqual(sim.call_args.kwargs['start_dates']['prep'],'2028-02-28')
        with patch.object(panel,'run_sim',side_effect=RuntimeError('stopped')) as sim, patch.object(panel,'_finish'):
            panel._calendar_worker(run)
        self.assertEqual(sim.call_count,1)
        self.assertEqual(run.status,'error')

if __name__=='__main__': unittest.main()
