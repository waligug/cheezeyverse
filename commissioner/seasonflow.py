"""Complete-season orchestration. All callers hold the shared save lock and journal."""
from pathlib import Path
import shutil
from . import characters as ch, recovery
from .codec.league_dat import LeagueDat, POTENTIALS, RATINGS, find_season_day
from .driver.fbpb3 import FBPB3
from .universe import config as cfg


def _character_floor(store, character, *sheets):
    """Highest value ever recorded for every protected field.

    Snapshots are the durable career record.  A rollover backup can already contain damage from
    an earlier season, so protecting only the immediately preceding save is not enough.
    """
    values = {}
    for sheet in sheets:
        for field in RATINGS + POTENTIALS:
            if sheet and field in sheet:
                values[field] = max(values.get(field, 0), int(sheet[field]))
    stored_ratings = character.get("ratings") or {}
    stored_potentials = character.get("potentials") or {}
    for field in RATINGS:
        if field in stored_ratings:
            values[field] = max(values.get(field, 0), int(stored_ratings[field]))
    for rating, potential in ch.POT_BY_RATING.items():
        if rating in stored_potentials:
            values[potential] = max(values.get(potential, 0), int(stored_potentials[rating]))
    snapshot_reader = getattr(store, "snapshots", lambda _character_id: [])
    for row in snapshot_reader(character["id"]):
        for field, value in (row.get("ratings") or {}).items():
            if field in RATINGS and value is not None:
                values[field] = max(values.get(field, 0), int(value))
        for rating, value in (row.get("potentials") or {}).items():
            potential = ch.POT_BY_RATING.get(rating, rating if rating in POTENTIALS else None)
            if potential and value is not None:
                values[potential] = max(values.get(potential, 0), int(value))
    # A ceiling below the ability it governs is internally inconsistent and lets the native
    # game pull that ability down on its next development pass.
    for rating, potential in ch.POT_BY_RATING.items():
        if rating in values:
            values[potential] = max(values.get(potential, 0), values[rating])
    return values


def _league_characters(store, key):
    try:
        rows = store.characters(league=key)
    except TypeError:  # small test/dry-run stores may only expose characters()
        rows = store.characters()
    return [c for c in rows if c.get("league") == key and
            c.get("status") in ("active", "declared")]


def capture_character_progress(store, key, live_path=None):
    """Capture historical and current floors immediately before the native game runs."""
    characters = _league_characters(store, key)
    if not characters:
        return {}
    live = LeagueDat(Path(live_path) if live_path else ch.save_path(key))
    floors = {}
    for character in characters:
        name = f'{character["first_name"]} {character["last_name"]}'
        dob = ch.codec_dob(character.get("game_dob") or
                           (character.get("claimed_slot") or {}).get("dob"))
        player = live.find(name, dob)
        floors[character["id"]] = _character_floor(store, character, player.values)
    return floors


def protect_character_progress(store, key, floors=None, live_path=None, dry_run=False):
    """Restore any rating or potential the native game lowered, retaining every increase."""
    characters = _league_characters(store, key)
    if not characters:
        return []
    live = LeagueDat(Path(live_path) if live_path else ch.save_path(key))
    planned, expected = [], []
    for character in characters:
        name = f'{character["first_name"]} {character["last_name"]}'
        dob = ch.codec_dob(character.get("game_dob") or
                           (character.get("claimed_slot") or {}).get("dob"))
        player = live.find(name, dob)
        floor = _character_floor(store, character, (floors or {}).get(character["id"]), player.values)
        wanted = {field: max(player.values[field], floor.get(field, player.values[field]))
                  for field in RATINGS + POTENTIALS}
        changed = {field: (player.values[field], value) for field, value in wanted.items()
                   if player.values[field] != value}
        planned.append({"id": character["id"], "name": name, "dob": dob,
                        "changed": changed, "values": wanted})
        if changed:
            for field, value in wanted.items():
                live.set(player, field, value)
            expected.append((name, dob, wanted))
    if dry_run:
        return planned
    if expected:
        live = ch.commit(live, expected)
    for row in planned:
        player = live.find(row["name"], row["dob"])
        ratings = {f: player.values[f] for f in RATINGS}
        potentials = ch.store_potentials(player.values)
        character = next(c for c in characters if c["id"] == row["id"])
        # Supabase writes dominate this stage's wall time. Most weeks the native game changes
        # only a few sheets, but this used to send two PATCH requests for every character even
        # when both payloads were byte-for-byte what the store already held.
        if ratings != (character.get("ratings") or {}):
            store.set_character_field(row["id"], "ratings", ratings)
        if potentials != (character.get("potentials") or {}):
            store.set_character_field(row["id"], "potentials", potentials)
    return planned


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
            champion = _champion(path.parent, season, rounds=spec.playoff_rounds)
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
    from . import headtohead, gamesarchive, statsarchive, takeaways
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
        takeaways.archive_overview(key, season, dest / "html")
        statsarchive.save(key, season, rows[season], source=str(mdb), overwrite=True)
        # THE POSTSEASON NEEDS THE SAME FREEZE, and for a sharper reason than the regular season.
        # A playoff file is only written by capture() while its season is still the current one,
        # and save() refuses to rewrite a finished season afterwards - only force=True repairs
        # it. So a Sim Week that sims and saves the finals but whose publish fails (simweek says
        # "the site did NOT publish; the week itself is saved") would leave
        # playoffs-<key>-<season>.json frozen pre-finals, or absent, permanently, and the
        # all-time postseason board would quietly lose a finals.
        #
        # MISSING IS NOT FATAL here, unlike the regular season above: a league can legitimately
        # reach a rollover having played no postseason at all, and an older export may have no
        # PlayoffStats table to read.
        try:
            postseason = statsarchive.read_mdb(mdb, "playoffs").get(season)
        except Exception as exc:                                        # noqa: BLE001
            postseason = None
            log(f'{key}: no postseason statistics to freeze ({exc})')
        if postseason:
            statsarchive.save(key, season, postseason, source=str(mdb), overwrite=True,
                              kind="playoffs")
        people = [c for c in store.characters(league=key) if c.get('status') in ('active', 'declared')]
        data = headtohead.from_mdb(mdb, people, runs=store.runs(limit=None), league=key, season=season)
        prior = dict(gamesarchive.archived_seasons(key)).get(season, {})
        present = {c["id"] for c in data.get("characters", [])}
        data["characters"].extend(c for c in prior.get("characters", []) if c["id"] not in present)
        gamesarchive.save(key, season, data)
        log(f'{key}: archived season {season} statistics and original exports')


def restore_character_sheets(store, key, source_path, live_path=None, dry_run=True,
                             post_camp_path=None):
    """Remove Training Camps regression while retaining its passive growth.

    ``source_path`` is the pre-camp authority for the floor.  When ``post_camp_path`` is supplied,
    each field becomes the larger of its pre/post value; otherwise the live save is the post-camp
    source.  Midseason development and point purchases are already in the pre-camp sheet and can
    therefore never be lost here.
    """
    source = LeagueDat(Path(source_path))
    live = LeagueDat(Path(live_path) if live_path else ch.save_path(key))
    post = LeagueDat(Path(post_camp_path)) if post_camp_path else live
    planned, expected = [], []
    for character in store.characters(league=key):
        if character.get("status") not in ("active", "declared"):
            continue
        name = f'{character["first_name"]} {character["last_name"]}'
        dob = ch.codec_dob(character.get("game_dob") or
                           (character.get("claimed_slot") or {}).get("dob"))
        before = source.find(name, dob)
        now = live.find(name, dob)
        after = post.find(name, dob)
        values = _character_floor(store, character, before.values, after.values, now.values)
        values = {field: values[field] for field in RATINGS + POTENTIALS}
        changed = {field: (now.values[field], wanted) for field, wanted in values.items()
                   if now.values[field] != wanted}
        planned.append({"id": character["id"], "name": name, "dob": dob,
                        "changed": changed, "values": values,
                        "height": now.values["Height"], "weight": now.values["Weight"]})
        if changed:
            for field, wanted in values.items():
                live.set(now, field, wanted)
            expected.append((name, dob, values))
    if dry_run:
        return planned
    if expected:
        live = ch.commit(live, expected)
    for row in planned:
        player = live.find(row["name"], row["dob"])
        store.set_character_field(row["id"], "ratings",
                                  {field: player.values[field] for field in RATINGS})
        store.set_character_field(row["id"], "potentials", ch.store_potentials(player.values))
        # The save is the physical truth after growth; keep the profile/store in lockstep too.
        store.set_character_field(row["id"], "height_inches", player.values["Height"])
        store.set_character_field(row["id"], "weight_lbs", player.values["Weight"])
    return planned


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
            expected.append((c, name, pl.dob, pl.values['Height'], pl.values['Weight'],
                             {field: pl.values[field] for field in RATINGS + POTENTIALS}))
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
        for character, name, dob, height, weight, sheet in expected:
            matches = [p for p in league.players if p.name == name]
            if not matches and key == "pro":
                from .offseason import age_of, PRO_DECLINE_AGE, retire
                if age_of(character, season) >= PRO_DECLINE_AGE:
                    retired.append(retire(character, season, "retired by the game at season rollover",
                                          store, log=log, slot_exists=False))
                    continue
            if len(matches) != 1:
                raise RuntimeError(f'{key}: {name} cannot be verified after rollover')
            retained.append((character, name, dob, height, weight, sheet))
            pl = matches[0]
            month, day, year = map(int, dob.split('/'))
            # Keep passive Training Camps growth, but never let camps erase midseason development
            # or point purchases.  Johnny's first rollover cut Jumping 25 -> 12; taking the larger
            # pre/post value retains every gain while making that kind of regression impossible.
            sheet = _character_floor(store, character, sheet, pl.values)
            sheet = {field: sheet[field] for field in RATINGS + POTENTIALS}
            values = dict(BirthMonth=month, BirthDay=day, BirthYear=year,
                          Height=height, Weight=weight, **sheet)
            for field, value in values.items():
                league.set(pl, field, value)
            checks.append((name, dob, values))
        if checks:
            league = ch.commit(league, checks)
        for character, name, dob, height, weight, sheet in retained:
            pl = league.find(name, dob)
            store.set_character_field(character["id"], "ratings", {f: pl.values[f] for f in RATINGS})
            store.set_character_field(character["id"], "potentials", ch.store_potentials(pl.values))
            ids = dict(character.get("league_player_ids") or {})
            ids[key] = pl.id
            store.set_character_field(character["id"], "league_player_ids", ids)
        # LAST SAVE MUTATION, after FBPB3 has completed free agency, staff and preseason. Running
        # this before the native rollover let the game sign every defanged graduate straight
        # back, leaving 78 over-age prep players and 20-man rosters in the 2028 opening.
        cleanup = None
        if key in ("prep", "college"):
            from . import ageout
            cleanup = ageout.apply(key, season, store=store, log=log)
            checked = ageout.audit(key, season + 1, store=store)
            if not checked["ok"]:
                raise RuntimeError(f"{key}: post-rollover age-out did not hold "
                                   f"({len(checked['over_age'])} over-age, "
                                   f"roster sizes {checked['sizes']})")
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
        out.append({'key': key, 'season': stamp[1], 'day': stamp[0], 'retired': retired,
                    'ageout': cleanup})
    return out
