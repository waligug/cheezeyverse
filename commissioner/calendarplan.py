"""Actual exported schedules and forward-only plans shared by the panel and worker."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, date, timedelta
from html.parser import HTMLParser
import hashlib
import json
import math
import re
from . import characters as ch
from .codec.league_dat import find_season_day
from .driver.fbpb3 import FBPB3
from .saveguard import SAVE_LOCK
from .universe import config as cfg


class CalendarError(ValueError):
    pass


def parse_date(text):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise CalendarError('Unrecognized game date: ' + text)


class ScheduleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self.games, self.anchors = [], [], []
        self.phase, self.day = None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'td':
            self.cells.append({'class': attrs.get('class'), 'text': '', 'hrefs': []})
        elif tag == 'a' and self.cells:
            self.cells[-1]['hrefs'].append(attrs.get('href', ''))

    def handle_data(self, data):
        if self.cells:
            self.cells[-1]['text'] += data

    def handle_endtag(self, tag):
        if tag != 'td' or not self.cells:
            return
        cell = self.cells.pop()
        text = ' '.join(cell['text'].split())
        if cell['class'] == 'tableheader' and text in ('Preseason', 'Regular Season', 'Playoffs'):
            self.phase = text
            self.day = None
        elif cell['class'] == 'header' and re.fullmatch(r'\d{1,4}[-/]\d{1,2}[-/]\d{1,4}', text):
            self.day = parse_date(text)
        elif cell['class'] == 'main' and self.day and self.phase and text:
            box = next((re.search(r'box(\d+)-', h) for h in cell['hrefs'] if re.search(r'box(\d+)-', h)), None)
            if box:
                self.anchors.append(self.day - timedelta(days=int(box[1]) - 1))
            # Completed rows are 'Away 60, @Home 42'; upcoming rows are 'Away @ Home'.
            teams = [re.sub(r'\s+\d+$', '', x.strip().lstrip('@')).strip()
                     for x in (text.split(',') if ',' in text else text.split('@'))]
            self.games.append({'date': self.day.isoformat(), 'phase': self.phase,
                               'label': text, 'played': bool(box), 'teams': teams})


def read_league(key):
    path = ch.save_path(key)
    stamp = find_season_day(path.read_bytes())
    if not stamp:
        raise CalendarError(f'{key}: cannot read the save calendar')
    parser = ScheduleParser()
    parser.feed((path.parent / 'html' / 'schedule.htm').read_text(encoding='latin-1'))
    if not parser.games:
        raise CalendarError(f'{key}: no exported schedule; export this league first')
    anchors = set(parser.anchors)
    if not anchors:
        from .headtohead import query
        days = [int(r['Day']) for r in query(path.parent / 'LeagueOutput.mdb',
                                            'SELECT DISTINCT Day FROM Schedule ORDER BY Day')]
        dates = sorted({date.fromisoformat(g['date']) for g in parser.games})
        if len(days) != len(dates):
            raise CalendarError(f'{key}: export and schedule database disagree')
        anchors = {d - timedelta(days=n - 1) for d, n in zip(dates, days)}
    if len(anchors) != 1:
        raise CalendarError(f'{key}: schedule dates cannot be aligned with game days')
    opener = anchors.pop()
    if opener.year != stamp[1]:
        raise CalendarError(f'{key}: schedule export belongs to another season')
    current = opener + timedelta(days=stamp[0] - 1)
    if any(g['played'] and date.fromisoformat(g['date']) >= current for g in parser.games):
        raise CalendarError(f'{key}: schedule export is ahead of the save; refresh the export')
    team_names = {t.nickname for t in cfg.BY_KEY[key].teams}
    for game in parser.games:
        if game["phase"] == "Regular Season" and not set(game["teams"]) <= team_names:
            game["phase"] = "All-star"
    if any(g['phase'] == 'Regular Season' and not g['played'] and g['date'] < current.isoformat()
           for g in parser.games):
        raise CalendarError(f'{key}: export is behind the save; refresh the export before planning')
    counts = Counter()
    for game in parser.games:
        game['numbers'] = {}
        if game['phase'] == 'Regular Season':
            for team in game['teams']:
                counts[team] += 1
                game['numbers'][team] = counts[team]
    regular = [g for g in parser.games if g['phase'] == 'Regular Season']
    if not regular:
        raise CalendarError(f'{key}: regular-season schedule is missing')
    return {'key': key, 'name': cfg.BY_KEY[key].name, 'season': stamp[1], 'day': stamp[0],
            'current_date': current.isoformat(), 'opener': opener.isoformat(),
            'first': min(g['date'] for g in regular), 'last': max(g['date'] for g in regular),
            'games': parser.games, 'teams': sorted(counts),
            'played': sum(g['played'] for g in regular), 'total': len(regular)}


def snapshot():
    if not SAVE_LOCK.acquire(blocking=False):
        raise CalendarError('A save operation is running. Calendar will refresh when it finishes.')
    try:
        if FBPB3.is_running():
            raise CalendarError('Close the game before refreshing the calendar.')
        leagues = [read_league(s.key) for s in cfg.LEAGUES]
        token = hashlib.sha256(json.dumps(leagues, sort_keys=True).encode()).hexdigest()
        return {'leagues': leagues, 'token': token}
    finally:
        SAVE_LOCK.release()


def verified_playoff_boundary(before_html, after_html, stored, wanted, start_date, days):
    """Accept the observed one-day playoff setup skip, only with full schedule evidence.

    FBPB advances April 19 -> April 21 after the final regular-season games. April 20
    is never a playable stop. Do not turn that exception into a generic date tolerance.
    """
    if not stored or stored != (wanted[0] + 1, wanted[1]) or not start_date:
        return False
    before, after = ScheduleParser(), ScheduleParser()
    before.feed(before_html)
    after.feed(after_html)
    target = date.fromisoformat(str(start_date)) + timedelta(days=days - 1)
    regular = [g for g in before.games if g['phase'] == 'Regular Season']
    final = [g for g in after.games if g['phase'] == 'Regular Season']
    playoffs = [g for g in after.games if g['phase'] == 'Playoffs']
    # Completed exports print the winner first; upcoming rows print away then home.
    identity = lambda g: (g['date'], tuple(sorted(g['teams'])))
    if not regular or max(g['date'] for g in regular) != target.isoformat():
        return False
    if Counter(map(identity, regular)) != Counter(map(identity, final)):
        return False
    if not all(g['played'] for g in final) or not playoffs or any(g['played'] for g in playoffs):
        return False
    anchors = set(after.anchors)
    if len(anchors) != 1:
        return False
    opener = anchors.pop()
    current = opener + timedelta(days=stored[0] - 1)
    return (opener.year == stored[1]
            and current == target + timedelta(days=2)
            and min(g['date'] for g in playoffs) == current.isoformat()
            and not any(g['played'] and g['date'] > target.isoformat() for g in after.games))


def plan(data, reference, target):
    try:
        selected = date.fromisoformat(target)
        ref = next(l for l in data['leagues'] if l['key'] == reference)
    except (TypeError, ValueError, StopIteration):
        raise CalendarError('Choose a league and a valid target date')
    if len({l['season'] for l in data['leagues']}) != 1:
        raise CalendarError('Leagues are in different seasons; reconcile the rollover first')
    if target < ref['current_date'] or target > ref['last'] or target < ref['first']:
        raise CalendarError('Choose a remaining regular-season date')
    games = [g for g in ref['games'] if g['phase'] == 'Regular Season']
    fraction = sum(g['date'] <= target for g in games) / len(games)
    rows = []
    for league in data['leagues']:
        regular = sorted((g for g in league['games'] if g['phase'] == 'Regular Season'), key=lambda g:g['date'])
        goal = max(1, math.ceil(fraction * len(regular) - 1e-9))
        end = target if league['key'] == reference else regular[goal - 1]['date']
        # Play THROUGH the selected day, then stop on the following morning.
        days = max(0, (date.fromisoformat(end) - date.fromisoformat(league['current_date'])).days + 1)
        rows.append({'key': league['key'], 'from': league['current_date'], 'through': end,
                     'days': days, 'expected_day': league['day'], 'season': league['season'],
                     'games': sum(league['current_date'] <= g['date'] <= end for g in regular) if days else 0})
    return {'reference': reference, 'target': target, 'percent': round(fraction * 100, 1),
            'leagues': rows, 'token': data['token']}
