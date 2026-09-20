"""Complete-season orchestration. All callers hold the shared save lock and journal."""
from pathlib import Path
import shutil
from . import characters as ch, recovery
from .codec.league_dat import LeagueDat, RATINGS, find_season_day
from .driver.fbpb3 import FBPB3
from .universe import config as cfg


def readiness(season, already_locked=False):
    from .saveguard import SAVE_LOCK
    if not already_locked and not SAVE_LOCK.acquire(blocking=False):
        return {'ready': False, 'reasons': ['A save operation is running.'], 'leagues': []}
    try:
        return _readiness(season)
    finally:
        if not already_locked:
            SAVE_LOCK.release()


def _readiness(season):
    from .simweek import _champion, _regular_season_left, MARKER
    reasons, leagues = [], []
    journal = recovery.read(MARKER)
    if journal:
        reasons.append('An interrupted run needs reconciliation before a new season can start.')
    if FBPB3.is_running():
        return {'ready': False, 'reasons': ['Close the game before checking season readiness.'], 'leagues': []}
    for spec in cfg.LEAGUES:
        path = ch.save_path(spec.key)
        try:
            stamp = find_season_day(path.read_bytes())
            champion = _champion(path.parent, season)
            if not stamp or stamp[1] != season:
                reasons.append(f'{spec.name}: save season does not match {season}.')
            if not champion:
                reasons.append(f'{spec.name}: finish the playoffs first.')
            leagues.append({'key': spec.key, 'champion': champion, 'season': stamp[1] if stamp else None,
                            'regular_remaining': _regular_season_left(path.parent)})
        except OSError:
            reasons.append(f'{spec.name}: save is unavailable.')
    return {'ready': not reasons, 'reasons': reasons, 'leagues': leagues}


def archive_finished(store, season, backups, log):
    """Keep raw exports plus structured history before any character changes or rollover."""
    from . import headtohead, gamesarchive, statsarchive
    for spec in cfg.LEAGUES:
        key = spec.key
        source = ch.save_path(key).parent
        dest = Path(backups[key]).parent / 'finished-season'
        mdb = source / 'LeagueOutput.mdb'
        if not mdb.is_file() or not (source / 'html' / 'schedule.htm').is_file():
            raise RuntimeError(f'{key}: export the finished season before rolling over')
        dest.mkdir()
        shutil.copy2(mdb, dest / mdb.name)
        shutil.copytree(source / 'html', dest / 'html')
        rows = statsarchive.read_mdb(mdb)
        if not rows.get(season):
            raise RuntimeError(f'{key}: finished-season statistics are missing from the export')
        statsarchive.save(key, season, rows[season], source=str(mdb), overwrite=True)
        people = [c for c in store.characters(league=key) if c.get('status') in ('active', 'declared')]
        data = headtohead.from_mdb(mdb, people, runs=store.runs(limit=None), league=key, season=season)
        prior = dict(gamesarchive.archived_seasons(key)).get(season, {})
        present = {c["id"] for c in data.get("characters", [])}
        data["characters"].extend(c for c in prior.get("characters", []) if c["id"] not in present)
        gamesarchive.save(key, season, data)
        log(f'{key}: archived season {season} statistics and original exports')


def rollover_saves(store, season, journal, log):
    """Only mark the season complete after every saved league reports the next year."""
    out = []
    for spec in cfg.LEAGUES:
        key, path = spec.key, ch.save_path(spec.key)
        before = LeagueDat(path)
        expected = []
        for c in store.characters(league=key):
            if c.get('status') not in ('active', 'declared'):
                continue
            name = f"{c['first_name']} {c['last_name']}"
            pl = before.find(name, ch.codec_dob(c.get('game_dob')))
            expected.append((c, name, pl.dob, pl.values['Height'], pl.values['Weight']))
        journal._update_marker(lambda state: state.update(phase='rollover', league=key))
        log(f'{key}: advancing the game to season {season + 1}')
        game = FBPB3()
        try:
            game.launch()
            game.load_save(spec.save_name)
            game.roll_over_season(log=log)
            game.sim_preseason()
            game.save_game(path=path)
        finally:
            if game.app is not None:
                try:
                    game.exit_game(save=False)
                except Exception:
                    FBPB3.kill()
        if FBPB3.is_running():
            raise RuntimeError('Game did not close; refusing to inspect a live save')
        league = LeagueDat(path)
        stamp = league.season_day()
        if not stamp or stamp[1] != season + 1 or not 1 <= stamp[0] <= 40:
            raise RuntimeError(f'{key}: rollover did not produce the next season opening ({stamp})')
        checks, retained, retired = [], [], []
        for character, name, dob, height, weight in expected:
            matches = [p for p in league.players if p.name == name]
            if not matches and key == "pro":
                from .offseason import age_of, PRO_DECLINE_AGE, retire
                if age_of(character, season) >= PRO_DECLINE_AGE:
                    retired.append(retire(character, season, "retired by the game at season rollover",
                                          store, log=log, slot_exists=False))
                    continue
            if len(matches) != 1:
                raise RuntimeError(f'{key}: {name} cannot be verified after rollover')
            retained.append((character, name, dob, height, weight))
            pl = matches[0]
            month, day, year = map(int, dob.split('/'))
            values = dict(BirthMonth=month, BirthDay=day, BirthYear=year, Height=height, Weight=weight)
            for field, value in values.items():
                league.set(pl, field, value)
            checks.append((name, dob, values))
        if checks:
            league = ch.commit(league, checks)
        for character, name, dob, height, weight in retained:
            pl = league.find(name, dob)
            store.set_character_field(character["id"], "ratings", {f: pl.values[f] for f in RATINGS})
            store.set_character_field(character["id"], "potentials", ch.store_potentials(pl.values))
            ids = dict(character.get("league_player_ids") or {})
            ids[key] = pl.id
            store.set_character_field(character["id"], "league_player_ids", ids)
        # Export the verified next-season save; pages and MDB must describe the same year.
        game = FBPB3()
        try:
            game.launch()
            game.load_save(spec.save_name)
            game.output_mdb(spec.save_name)
            game.html_output(spec.save_name)
        finally:
            if game.app is not None:
                try:
                    game.exit_game(save=False)
                except Exception:
                    FBPB3.kill()
        from .calendarplan import read_league
        calendar = read_league(key)
        if calendar['season'] != season + 1 or calendar['played'] or calendar['current_date'] < calendar['first']:
            raise RuntimeError(f'{key}: the new regular season opening could not be verified')
        log(f'{key}: season {season + 1} opening verified and exported')
        out.append({'key': key, 'season': stamp[1], 'day': stamp[0], 'retired': retired})
    return out
