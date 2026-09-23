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
        # PRESENT IS NOT FINISHED. This checked only that the season KEY existed, and a stale MDB
        # has the key - it just has a fraction of the games. On 2026-09-21 pro's export had failed
        # hours earlier and its MDB held 8 games of a 58-game season; nothing here would have
        # raised, archive_finished would have written an 8-game year as pro's permanent record,
        # and once current_season advances save() refuses to rewrite a finished season, so only
        # force repairs it. That is the same shape as the lost 2028 title: a guard that tested
        # presence rather than completeness.
        #
        # HALF THE SCHEDULE, deliberately loose. The question is "is this export from this
        # season's END", and a stale one is stale by a mile - pro's was 8 of 58, 14%. A tight
        # bound would fire on a real season instead: injuries and trades mean nobody is
        # guaranteed to have played every game, and a legitimate 30 of 32 must not be refused.
        # The floor only has to separate a finished season from a fragment.
        played = max((int(r.get("Games") or 0) for r in rows[season]), default=0)
        if played * 2 < spec.schedule_games:
            raise RuntimeError(
                f'{key}: the export holds only {played} game(s) of a {spec.schedule_games}-game '
                f'season, so it is stale or incomplete. Re-run the Output MDB export for '
                f'{spec.save_name} before rolling over - this would be written as the permanent '
                f'record and cannot be rewritten afterwards.')
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


def _team_abbrev(key, dat, team_id):
    """The abbreviation for a team id, read off an ALREADY-OPEN save.

    Takes the open LeagueDat rather than a path on purpose: the caller has one, and re-parsing a
    six-megabyte save once per character to answer a lookup is the kind of cost that turns a
    tidy-up into a visible pause in the middle of a rollover.

    Teams are numbered in the save and named in config; the mapping is by ascending id, the same
    assumption `promote` and `draft.needs_from_save` already make.
    """
    # STRICTLY AN INTEGER. A Team value is an int16 straight out of the record, so anything
    # else arriving here means something upstream read the wrong field - and int(1.5) would
    # quietly hand back team 1, turning that mistake into a plausible team name on somebody's
    # career page. Better to resolve nothing and let the caller say it could not tell.
    if isinstance(team_id, bool) or not isinstance(team_id, int):
        return None
    if team_id < 1:
        return None                      # a free agent has no abbreviation to record
    spec = cfg.BY_KEY[key]
    for i, tid in enumerate(sorted(dat.teams())):
        if tid == team_id and i < len(spec.teams):
            return spec.teams[i].abbrev
    return None


def _replace_character(key, path, character, name, dob, season, store, log, game_factory=None):
    """Put a character the rollover left without a team back on the right one. Returns the abbrev.

    A 16-team save takes the codec's release/sign/dress directly. The 20-team pro save does not:
    those writes left it unloadable twice on 2026-09-23, and the codec refuses them there. So on
    pro the move is REHEARSED - made on a clone, loaded and simmed a day in FBPB3 - and only then
    made on the live save. Without this, Dodger Manson came out of the 2032 rollover a free agent
    (his three game years were up and nobody signed him) and the offseason could only log "he
    needs placing by hand"; the rehearsal is exactly what was then done by hand.
    """
    if len(LeagueDat(path).teams()) >= 20:
        return _place_rehearsed(key, path, character, name, dob, season, store, log,
                                game_factory or FBPB3)
    return _place_character(key, path, character, name, dob, season, store, log)


REHEARSAL_PREFIX = "ZZ_rehearsal_"


def _place_rehearsed(key, path, character, name, dob, season, store, log, game_factory):
    """`_place_character` on a clone first; FBPB3 must load it and sim a day before the live write."""
    placed = rehearse(
        path, lambda p, say: _place_character(key, p, character, name, dob, season, store, say),
        f"{name}'s placement", log, game_factory)
    log(f"   {name}: placement rehearsed on a copy of the save first - it loaded and simmed")
    return placed


def dress_rehearsed(path, people, log=print, game_factory=None):
    """Dress `people` - [(name, codec dob)] - on a 20-team save, rehearsed. Returns who changed.

    The draft stamps a pick onto a reserve seat and dresses him, and dressing is a lineup/depth
    write the codec refuses on pro. So on pro the draft stamps WITHOUT dressing and hands its
    picks here once, at the end: one clone, one load and one simmed day for the whole class.
    """
    def write(p, say):
        L = LeagueDat(p)
        changed = [name for name, dob in people if L.dress(L.find(name, dob))]
        if changed:
            L.save()
        return changed
    changed = rehearse(path, write, "dressing " + ", ".join(n for n, _ in people), log,
                       game_factory)
    if changed:
        log(f"   dressed {', '.join(changed)} - rehearsed on a copy of the save first")
    return changed


def rehearse(path, write, what, log, game_factory=None):
    """Run `write(league_dat_path, log)` on a CLONE, prove FBPB3 loads and sims it, then for real.

    For the 20-team pro save, where the codec's lineup/depth writes left the file unloadable twice
    on 2026-09-23 and are refused outside `rehearsed_writes()`. If the game cannot load the clone
    or shows a run-time error simming a day on it, this raises and the live save is untouched.
    Returns whatever the LIVE `write` returned.
    """
    import struct
    from .codec.league_dat import rehearsed_writes
    game_factory = game_factory or FBPB3
    folder = Path(path).parent
    clone = folder.parent / f"{REHEARSAL_PREFIX}{folder.name}"
    if clone.exists():
        shutil.rmtree(clone)
    shutil.copytree(folder, clone)
    # A copied saveinfo.dat ties with the original's save time, and a tie makes the Load list
    # order arbitrary - load_save refuses rather than risk loading the live save by mistake.
    # A month older sorts the clone last and unambiguously.
    info = clone / "saveinfo.dat"
    raw = bytearray(info.read_bytes())
    frac, serial = struct.unpack_from("<2d", raw, 0)
    struct.pack_into("<2d", raw, 0, frac, serial - 30)
    info.write_bytes(bytes(raw))
    try:
        with rehearsed_writes():
            write(clone / "league.dat", lambda m: None)
        game = game_factory()
        try:
            game.launch()
            clash = {n: why for n, why in game.ambiguous_saves().items()
                     if n in (clone.name, folder.name)}
            if clash:
                raise RuntimeError(f"rehearsal clone cannot be told apart in the Load list: {clash}")
            game.load_save(clone.name)
            for stage in ("loading", "simming a day on"):
                if stage != "loading":
                    game.sim_days(1)
                box = game._runtime_error()
                if box:
                    raise RuntimeError(f"FBPB3 failed {stage} the rehearsal of {what} ({box}); "
                                       "the live save was not touched")
        finally:
            if game.app is not None:
                try:
                    game.exit_game(save=False)
                except Exception:                                       # noqa: BLE001
                    FBPB3.kill()
    finally:
        shutil.rmtree(clone, ignore_errors=True)
    with rehearsed_writes():
        return write(path, log)


def _place_character(key, path, character, name, dob, season, store, log):
    """The codec half of `_replace_character`. Callers go through that, never here directly.

    WHY. With Finances on, the game's own rollover runs free agency, and it RELEASES people:
    Gravy Jones was drafted #3 by THP on a three-year rookie deal in the 2031 offseason and came
    out of the rollover with Team -1 and his contract gone - not shortened, erased. Traced through
    the offseason's own save backups: he was on THP with the deal going in and a free agent with
    nothing coming out. Dodger Manson made the identical trip a year earlier and kept both, so it
    is not the draft path; the likeliest difference is the luxury tax, switched on between the two,
    since an AI releasing a player to get under it voids his deal. The cause does not change the
    fix, which is the same belt-and-braces idea as the weekly re-dress: a real person must come
    out of a rollover on a roster, so the rollover checks and puts him there.

    Nate's rule, stated 2026-09-22: "He needs to play his rookie year for the team that drafted
    him, add that in 100%." Where he goes:
      * an ACTIVE rookie deal -> the team on that deal, for the years it has left, at its salary
      * otherwise              -> the team the store last had him on
      * otherwise              -> the team with the fewest of our characters on it
    A full roster makes room by releasing its weakest body that is not one of ours.

    The team is resolved with `_team_abbrev`'s mapping - ascending save ids zipped with config -
    and NEVER by counting from 1: pro's ids run 5..24, and counting from 1 put Gravy on the Curds
    when he was meant for the Threshers, which is how this function's manual predecessor went
    wrong the first time.
    """
    from .codec.league_dat import LeagueDat, RATINGS
    spec = cfg.BY_KEY[key]
    L = LeagueDat(path)
    ids = sorted(L.teams())
    abbrev_of = {tid: spec.teams[i].abbrev for i, tid in enumerate(ids) if i < len(spec.teams)}
    id_of = {a: t for t, a in abbrev_of.items()}

    deal = None
    for entry in reversed(list(character.get("level_history") or [])):
        if isinstance(entry, dict) and entry.get("level") == key and entry.get("to_season") is None:
            deal = entry.get("contract") or None
            break
    years_left, salary, target = 0, None, None
    if deal and deal.get("game_years") and deal.get("season_from") is not None:
        years_left = int(deal["game_years"]) - (int(season) + 1 - int(deal["season_from"]))
        if years_left > 0 and deal.get("team") in id_of:
            target, salary = deal["team"], deal.get("salary")
    if target is None and character.get("team_abbrev") in id_of:
        target = character["team_abbrev"]
    if target is None:
        ours = {}
        for other in store.characters(league=key):
            if other.get("team_abbrev"):
                ours[other["team_abbrev"]] = ours.get(other["team_abbrev"], 0) + 1
        target = min(id_of, key=lambda a: (ours.get(a, 0), a))
    team_id = id_of[target]

    protected = {f'{c["first_name"]} {c["last_name"]}' for c in store.characters()}
    pl = L.find(name, dob)
    if pl.values["Team"] == team_id:
        return target
    if pl.values["Team"] >= 1:
        L.release(pl)
        L.save()
        L = LeagueDat(path)
    roster = [p for p in L.players if p.values["Team"] == team_id]
    if len(roster) >= spec.roster_size:
        skill = [f for f in RATINGS if f not in ("3pUsage", "Fouling", "Stamina")]
        spare = [p for p in roster if p.name not in protected]
        if not spare:
            raise RuntimeError(f"{target} is full of characters; nowhere to put {name}")
        worst = min(spare, key=lambda p: sum(p.values[f] for f in skill))
        log(f"   {target} is full: releasing {worst.name} to make room for {name}")
        L.release(worst)
        L.save()
        L = LeagueDat(path)
    L.sign(L.find(name, dob), team_id)
    if salary:
        # The years the deal has LEFT, not the years it was written for: the rollover that just
        # ran consumed one. Writing the full term would pay him a year he is not owed.
        L.set_contract(L.find(name, dob), [int(salary)] * max(1, years_left))
    else:
        # NO DEAL LEFT TO HONOUR, so price him like free agency would have. `sign` just gave him
        # the $1,000,000 backfill token, which is what Dodger Manson - eighth-best in pro - was
        # left on after the 2032 rollover. See characters.market_deal.
        market = ch.market_deal(L, L.find(name, dob))
        if market:
            L.set_contract(L.find(name, dob), [market[0]] * market[1])
            log(f"   {name}: signed at the market rate, ${market[0]:,} x {market[1]}")
    L.save()
    L = LeagueDat(path)
    if L.dress(L.find(name, dob)):
        L.save()
    return target


def rollover_saves(store, season, journal, log, progress=None):
    """Only mark the season complete after every saved league reports the next year.

    `progress(percent, detail, league)` moves the status card. THIS IS THE LONGEST STRETCH OF
    THE OFFSEASON - the game's own rollover, save, verify and export, three leagues, twelve
    minutes and more - and it used to say nothing, so the card sat on the last thing before it
    ("College: retiring the over-age...") and the 2032 offseason was reported as frozen on
    college while pro was the one actually running.
    """
    def say(percent, detail, key):
        if progress is None:
            return
        try:
            progress(percent, detail, key)
        except Exception:                                               # noqa: BLE001
            pass

    out = []
    count = len(cfg.LEAGUES)
    for index, spec in enumerate(cfg.LEAGUES):
        # 80..92 split across the leagues; publish takes over at 92.
        base = 80 + 12 * index / max(count, 1)
        step = 12 / max(count, 1)
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
        say(base, f"{spec.name}: the game's own season rollover to {season + 1}", key)
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
        say(base + step / 2, f"{spec.name}: checking the characters and exporting {season + 1}", key)
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
            # AND WHICH TEAM HE IS ACTUALLY ON, which the rollover can change under us now that
            # Finances is on. Free agency runs INSIDE this rollover: an expiring contract is
            # thrown open and the AI re-signs the man wherever it likes, so the team the store
            # recorded when he was stamped can be a season out of date before anybody looks.
            #
            # Everything else here is re-read from the save for exactly this reason - ratings,
            # potentials, player id - and the team was the one field still trusted from the
            # stamp. Left alone it is not a transient error: the site names the wrong team for a
            # whole season and the career page keeps that season wrong for ever.
            try:
                abbrev = _team_abbrev(key, league, pl.values["Team"])
                # A ROOKIE DEAL OUTRANKS WHEREVER FREE AGENCY PUT HIM. Released-then-re-signed
                # elsewhere is the other shape of the same failure, and "rookie year for the team
                # that drafted him, 100%" covers both.
                open_deal = None
                for entry in reversed(list(character.get("level_history") or [])):
                    if (isinstance(entry, dict) and entry.get("level") == key
                            and entry.get("to_season") is None):
                        open_deal = entry.get("contract") or None
                        break
                if (abbrev and open_deal and open_deal.get("team")
                        and open_deal.get("season_from") is not None
                        and int(open_deal.get("game_years") or 0)
                        - (int(season) + 1 - int(open_deal["season_from"])) > 0
                        and abbrev != open_deal["team"]):
                    placed = _replace_character(key, path, character, name, dob, season, store, log)
                    log(f"   {name}: free agency moved him to {abbrev} mid-rookie-deal - put back "
                        f"on {placed}, the team that drafted him")
                    store.set_character_field(character["id"], "team_abbrev", placed)
                    league = LeagueDat(path)
                    abbrev = placed
                if abbrev and abbrev != character.get("team_abbrev"):
                    log(f'   {name}: now on {abbrev} (was {character.get("team_abbrev")})')
                    store.set_character_field(character["id"], "team_abbrev", abbrev)
                elif not abbrev:
                    # A REAL PERSON CAME OUT OF THE ROLLOVER WITH NO TEAM. This used to be logged
                    # with "he needs placing by hand" and left there - and Gravy Jones, a #3 pick
                    # on a live rookie deal, was placed by hand, onto the wrong team the first
                    # time. The rollover knows the drafting team, the deal and the years left, so
                    # it puts him back itself. Only if THAT fails does a human hear about it.
                    try:
                        placed = _replace_character(key, path, character, name, dob, season,
                                                    store, log)
                        log(f"   {name} came out of the rollover with no team - put back on "
                            f"{placed}")
                        store.set_character_field(character["id"], "team_abbrev", placed)
                        league = LeagueDat(path)
                    except Exception as exc:                            # noqa: BLE001
                        log(f"   !! {name} is NOT ON A ROSTER after the rollover and could not "
                            f"be put back automatically ({exc}). He needs placing by hand.")
            except Exception as exc:                                    # noqa: BLE001
                # Never fatal. A wrong team name on the site is a bad day; a rollover that
                # raises here is a universe stranded mid-offseason with the game holding it.
                log(f"   (could not re-read {name}'s team: {exc})")
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
        # NOTHING FLOORED OPENS A SEASON. The rollover's own draft and free agency put the
        # game's bodies onto rosters, and anything the roster guard ever floored comes with them.
        # This used to need two hand-run repair passes after every offseason - 117 floored
        # players sat on pro rosters after 2031's. Characters are never touched.
        try:
            from . import ageout
            ageout.reconcile(key, store=store, log=log, reserves=True)
        except Exception as exc:                                        # noqa: BLE001
            log(f"   ! {key}: could not restore floored bodies after the rollover ({exc})")
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
