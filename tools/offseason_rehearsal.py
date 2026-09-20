"""Run a whole offseason against throwaway copies of the saves, before doing it for real.

The offseason has never run once in this universe. It grows everybody, promotes whoever has
outgrown his level, runs the pro draft, retires the finished, hands slots back and pays
everybody - and the first time any of that executes it will be on three live saves with seven
friends' characters in them, in a step that cannot be undone from the panel.

So it gets rehearsed. This builds a complete fake season in a sandbox and drives the real
`offseason.run_offseason` through it:

  * copies of the three league.dat files, with the manifest birthdays stamped back on (the
    desktop's abandoned copies never had tools/stamp_dobs.py run, and every reserve slot is
    unfindable without it - the live saves on SERVERPC do have them)
  * a local JSON store, never Supabase, holding characters built to hit every branch:
    one who only grows, one who ages out of prep, one drafted on eligibility, one who
    declared early, one who stays in college, one who retires on age, one journeyman
  * notify stubbed, so nobody's Discord hears about a rehearsal

WHAT IT REFUSES TO TOUCH. Nothing outside the sandbox: not the real saves, not the real
`universe/local_store.json`, not Supabase, not the backups folder, not Discord. Every path the
commissioner reads is redirected first, and the run aborts if any of them still points at the
real thing.

    python tools/offseason_rehearsal.py            # rehearse, then delete the sandbox
    python tools/offseason_rehearsal.py --keep     # leave it in tmp/rehearsal to poke at
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch  # noqa: E402
from commissioner import growth, localstore, notify, offseason  # noqa: E402
from commissioner.codec.league_dat import (  # noqa: E402
    RATINGS as ROSTER_RATINGS, LeagueDat, find_season_day)
from commissioner.universe import config as cfg  # noqa: E402

SANDBOX = ROOT / "tmp" / "rehearsal"
# The REAL save locations, captured before anything is redirected. redirect_everything()
# replaces ch.save_path, so a second scenario that asked it where the saves are would be told
# "in the sandbox you just deleted" and find nothing to copy.
REAL_SAVE_PATH = ch.save_path
MANIFEST = json.loads((ROOT / "universe" / "manifest.json").read_text(encoding="utf-8"))
LEAGUES = ("prep", "college", "pro")

failures = []
notes = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)
    return cond


# ---------------------------------------------------------------------------------------
# the sandbox
# ---------------------------------------------------------------------------------------
def build_sandbox():
    """Copies of the three saves, with manifest birthdays restored, and an empty store."""
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    (SANDBOX / "saves").mkdir(parents=True)
    (SANDBOX / "backups").mkdir()

    paths = {}
    for key in LEAGUES:
        source = REAL_SAVE_PATH(key)
        if not source.exists():
            raise SystemExit(f"no {key} save on this machine ({source})")
        dest = SANDBOX / "saves" / key / "league.dat"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        paths[key] = dest
    return paths


def stamp_manifest_dobs(paths):
    """Put the manifest's birthdays back, the way tools/stamp_dobs.py does after a rebuild.

    Without this no reserve slot in the sandbox can be found by (name, dob), which is how the
    commissioner finds every one of them - so the rehearsal would fail on the copies' own
    history rather than on anything the offseason does.
    """
    fixed = {}
    for key, path in paths.items():
        want = {r["name"]: r for r in MANIFEST["players"] if r["league"] == key}
        L = LeagueDat(path)
        changed = 0
        for pl in L.players:
            row = want.get(pl.name)
            if not row or pl.dob == ch.codec_dob(row["dob"]):
                continue
            month, day, year = (int(v) for v in ch.codec_dob(row["dob"]).split("/"))
            L.set(pl, "BirthMonth", month)
            L.set(pl, "BirthDay", day)
            L.set(pl, "BirthYear", year)
            changed += 1
        if changed:
            L.save()
        fixed[key] = changed
    return fixed


def redirect_everything(paths, store_path):
    """Point every path the commissioner writes at the sandbox, and prove it landed."""
    ch.save_path = lambda key: paths[key]
    ch.BACKUPS = SANDBOX / "backups"
    offseason.BACKUPS = SANDBOX / "backups"
    localstore.PATH = store_path
    from commissioner import simweek
    simweek.MARKER = SANDBOX / "run_in_progress.json"
    from commissioner.saveguard import SaveLock
    simweek._SIM_LOCK = SaveLock(SANDBOX / ".save-operation.lock")
    notify.post = lambda *a, **k: False
    notify.post_embed = lambda *a, **k: False

    real_docs = str(ch.DOCS).lower()
    for key in LEAGUES:
        here = str(ch.save_path(key)).lower()
        if real_docs in here or "leaguedata" in here:
            raise SystemExit(f"refusing to run: {key} still points at the real save ({here})")
    if str(localstore.PATH).lower().endswith("universe\\local_store.json"):
        raise SystemExit("refusing to run: the local store still points at the real one")


# ---------------------------------------------------------------------------------------
# the cast - one character per branch the offseason has
# ---------------------------------------------------------------------------------------
def cast_for(season):
    """Seven characters, each chosen to take a different path through the offseason."""
    def born(age):
        return f"6/15/{season - age}"

    return [
        {"who": "grows and stays", "league": "prep", "age": 15, "genes": 95,
         "first_name": "Colby", "last_name": "Jackman", "height": 70, "weight": 145},
        {"who": "ages out of prep", "league": "prep", "age": 17, "genes": 60,
         "first_name": "Brie", "last_name": "Halloumi", "height": 76, "weight": 180},
        {"who": "drafted on eligibility", "league": "college", "age": 21, "genes": 40,
         "college_years": 3, "first_name": "Edam", "last_name": "Pecorino",
         "height": 79, "weight": 205},
        {"who": "declared early", "league": "college", "age": 19, "genes": 40, "declared": True,
         "college_years": 1, "first_name": "Gouda", "last_name": "Brieson",
         "height": 81, "weight": 215},
        {"who": "stays in college", "league": "college", "age": 19, "genes": 50,
         "college_years": 1, "first_name": "Jarls", "last_name": "Bergman",
         "height": 74, "weight": 185},
        {"who": "retires on age", "league": "pro", "age": 39, "genes": 50,
         "first_name": "Stilton", "last_name": "Vantage", "height": 78, "weight": 210},
        {"who": "journeyman, just gets paid", "league": "pro", "age": 26, "genes": 50,
         "first_name": "Munster", "last_name": "Kaas", "height": 80, "weight": 220},
    ], born


def build_cast(paths, season, log=print):
    """Insert the cast into the store and stamp each one onto a free reserve slot."""
    people, born = cast_for(season)
    made = []
    for n, person in enumerate(people):
        league = person["league"]
        row = localstore.add_character({
            # A FIXED id, not a fresh uuid. The height curve is seeded off it, so random ids
            # mean a rehearsal that grows different people every time and a failure nobody can
            # reproduce. These are the same seven characters on every run.
            "id": f"rehearsal-{n}-{person['first_name'].lower()}",
            "first_name": person["first_name"], "last_name": person["last_name"],
            "position": "C", "league": league, "archetype": "big",
            "height_inches": person["height"], "weight_lbs": person["weight"],
            "build": "solid", "hometown": "Curdston", "jersey_preference": 32,
            "traits": {"height_genes": person["genes"]},
            "college_years": person.get("college_years", 0),
            "declared": person.get("declared", False),
            "ratings": {r: 40 for r in growth.RATINGS} if hasattr(growth, "RATINGS") else {},
            "potentials": {},
        })

        # the real sheet, keyed the way the codec wants it
        from commissioner.codec.league_dat import POTENTIALS, RATINGS
        ratings = {r: 45 for r in RATINGS}
        potentials = {p: 80 for p in POTENTIALS}
        localstore.set_character_field(row["id"], "ratings", ratings)

        holders = [c for c in localstore.characters(league=league) if c.get("claimed_slot")]
        taken = [{"name": c["claimed_slot"].get("name"), "dob": c["claimed_slot"].get("dob")}
                 for c in holders]
        slots = ch.free_slots(MANIFEST, league, taken)
        existing = {(p.name, p.dob) for p in LeagueDat(paths[league]).players}
        slots = [s for s in slots if (s.name, ch.codec_dob(s.dob)) in existing]
        if not slots:
            raise SystemExit(f"no free reserve slot in {league} for the rehearsal cast")
        slot = slots[0]

        dob = born(person["age"])
        L = LeagueDat(paths[league])
        ch.stamp_character(L, slot, {
            "first_name": person["first_name"], "last_name": person["last_name"],
            "dob": dob, "height_inches": person["height"], "weight_lbs": person["weight"],
            "build": "solid", "position": "C", "ratings": ratings, "potentials": potentials,
        })
        ch.commit(L, [(f'{person["first_name"]} {person["last_name"]}', dob,
                       {"Height": person["height"]})])
        localstore.activate_character(row["id"], league, slot.team, slot.as_json(), dob)
        for field in ("college_years", "declared"):
            if person.get(field) is not None:
                localstore.set_character_field(row["id"], field, person.get(field, 0))
        made.append({**person, "id": row["id"], "dob": dob, "slot": slot.as_json()})
        log(f'   {person["first_name"]} {person["last_name"]:12} {league:8} '
            f'age {person["age"]:2}  {slot.team}  ({person["who"]})')
    return made


# ---------------------------------------------------------------------------------------
# the rehearsal
# ---------------------------------------------------------------------------------------
def in_save(path, name, dob=None):
    """The record for a name in a save, or None. Birthdays move, so the name is enough here."""
    L = LeagueDat(path)
    hits = [p for p in L.players if p.name == name and (dob is None or p.dob == ch.codec_dob(dob))]
    return hits[0] if len(hits) == 1 else None


def setup(log=print):
    """A fresh sandbox with the cast in it. Every scenario starts from this."""
    paths = build_sandbox()
    fixed = stamp_manifest_dobs(paths)
    log('   birthdays restored: ' + ", ".join(f"{k} {v}" for k, v in fixed.items()))

    store_path = SANDBOX / "local_store.json"
    redirect_everything(paths, store_path)
    log(f"   saves, store, backups and Discord all redirected into {SANDBOX}")

    season = find_season_day(LeagueDat(paths["prep"]).data)[1]
    localstore.set_setting("current_season", season)
    localstore.set_setting("offseason_points", 15)
    localstore.set_setting("starting_points", 20)
    log(f"\nseason {season}; building the cast")
    made = build_cast(paths, season, log=log)
    return paths, store_path, season, made


def rehearse(keep=False):
    print("building the sandbox")
    paths, store_path, season, made = setup()

    before = {key: paths[key].read_bytes() for key in LEAGUES}
    store_before = store_path.read_text(encoding="utf-8")

    print("\n---- dry run ----")
    dry = offseason.run_offseason(localstore, season=season, dry_run=True)
    check("the dry run touched no save",
          all(paths[k].read_bytes() == before[k] for k in LEAGUES))
    check("the dry run touched no character",
          store_path.read_text(encoding="utf-8") == store_before)
    check("the dry run found everybody",
          not dry["failed"], str([f["error"] for f in dry["failed"]][:3]))

    print("\n---- the real thing ----")
    result = offseason.run_offseason(localstore, season=season)

    print("\n---- the preview told the truth ----")
    # The panel shows the dry run and then somebody presses the real button. If the two
    # disagree, the preview is a lie, and it is the only look anybody gets before a step that
    # cannot be undone.
    def names(res, key):
        if key == "grew":
            return sorted(g["name"] for g in res.get("grew") or [])
        return sorted(f'{m["character"]["first_name"]} {m["character"]["last_name"]}'
                      for m in res.get(key) or [])

    retiring = set(names(dry, "retired"))
    check("the preview named the same people who actually moved up",
          names(dry, "promoted") == names(result, "promoted"),
          f'{names(dry, "promoted")} vs {names(result, "promoted")}')
    check("the preview named the same people who were actually drafted",
          names(dry, "drafted") == names(result, "drafted"),
          f'{names(dry, "drafted")} vs {names(result, "drafted")}')
    check("the preview named the same people who actually retired",
          names(dry, "retired") == names(result, "retired"),
          f'{names(dry, "retired")} vs {names(result, "retired")}')
    # A retiring character is still active when the dry run measures growth and is gone by the
    # time the real one does, so he is allowed to differ - and only he.
    grew_dry = [n for n in names(dry, "grew") if n not in retiring]
    check("the preview named the same people who actually grew",
          grew_dry == names(result, "grew"),
          f'{grew_dry} vs {names(result, "grew")}')
    if set(names(dry, "grew")) & retiring:
        notes.append("the dry run counts a retiring character among those who grow; the real "
                     "run retires him first, so the preview's growth count can read one high")

    print("\n---- what happened ----")
    check("nothing failed", not result["failed"],
          "; ".join(f'{f["stage"]}: {f["error"]}' for f in result["failed"][:4]))

    by_who = {m["who"]: m for m in made}
    rows = {c["id"]: c for c in localstore.characters()}

    grower = rows[by_who["grows and stays"]["id"]]
    pl = in_save(paths["prep"], "Colby Jackman")
    check("the fifteen-year-old grew, and is still in prep",
          pl is not None and pl.values["Height"] > by_who["grows and stays"]["height"],
          f'{by_who["grows and stays"]["height"]} -> {pl and pl.values["Height"]}')
    check("and his weight moved with him",
          pl is not None and pl.values["Weight"] != by_who["grows and stays"]["weight"],
          f'{by_who["grows and stays"]["weight"]} -> {pl and pl.values["Weight"]}')

    aged_out = rows[by_who["ages out of prep"]["id"]]
    moved = in_save(paths["college"], "Brie Halloumi")
    check("the seventeen-year-old is in the college save",
          moved is not None and aged_out.get("league") == "college",
          f'store says {aged_out.get("league")}, save {"has" if moved else "does not have"} him')
    if moved:
        check("he carried his body up with him, not a fourteen-year-old's",
              moved.values["Height"] >= by_who["ages out of prep"]["height"]
              and moved.values["Weight"] >= by_who["ages out of prep"]["weight"] - 2,
              f'{moved.values["Height"]}in {moved.values["Weight"]}lbs')
    # The slot has to go back to being the filler it was, body included: ratings were always
    # scrubbed here and the body never was, so a recycled row used to keep the departed
    # character's height - and now his weight, which follows growth.
    was = by_who["ages out of prep"]["slot"]
    back = in_save(paths["prep"], was["name"])
    check("the vacated slot got its own body back, not the character's",
          back is not None and was.get("height") and back.values["Height"] == was["height"]
          and back.values["Weight"] == was["weight"],
          f'slot was {was.get("height")}in {was.get("weight")}lbs, row now '
          f'{back and back.values["Height"]}in {back and back.values["Weight"]}lbs')
    check("his prep slot went back to being a filler",
          in_save(paths["prep"], by_who["ages out of prep"]["slot"]["name"]) is not None
          and in_save(paths["prep"], "Brie Halloumi") is None)

    for who in ("drafted on eligibility", "declared early"):
        person = by_who[who]
        row = rows[person["id"]]
        drafted = in_save(paths["pro"], f'{person["first_name"]} {person["last_name"]}')
        check(f"{who}: he is in the pro save",
              drafted is not None and row.get("league") == "pro",
              f'store {row.get("league")}, round {row.get("draft_round")} '
              f'pick {row.get("draft_pick")}')
        check(f"{who}: his old college slot is free again",
              in_save(paths["college"], person["slot"]["name"]) is not None)

    stayer = rows[by_who["stays in college"]["id"]]
    check("the one who stayed banked a college year",
          int(stayer.get("college_years") or 0) == 2, f'{stayer.get("college_years")}')

    retiree = rows[by_who["retires on age"]["id"]]
    check("the thirty-nine-year-old retired",
          retiree.get("status") == "retired",
          f'{retiree.get("status")}, {retiree.get("retired_reason")}')
    check("and his pro slot is a filler again",
          in_save(paths["pro"], by_who["retires on age"]["slot"]["name"]) is not None)

    journeyman = rows[by_who["journeyman, just gets paid"]["id"]]
    check("the journeyman was paid his offseason points",
          int(journeyman.get("points_available") or 0) >= 15,
          f'{journeyman.get("points_available")} points')

    settings = localstore.get_settings()
    check("the season rolled over",
          int(settings.get("current_season")) == season + 1
          and int(settings.get("last_offseason")) == season,
          f'current {settings.get("current_season")}, last {settings.get("last_offseason")}')

    print("\n---- the saves afterwards ----")
    for key in LEAGUES:
        L = LeagueDat(paths[key])
        try:
            teams = L.teams()
            ok, detail = True, f"{len(L.players)} players, {len(teams)} teams"
        except Exception as exc:
            ok, detail = False, str(exc)
        check(f"{key}: the save still parses and its rosters agree", ok, detail)

    print("\n---- running it twice ----")
    try:
        offseason.run_offseason(localstore, season=season)
        check("a second run of the same season is refused", False, "it ran again")
    except offseason.OffseasonError as exc:
        check("a second run of the same season is refused", True, str(exc)[:60])
    except Exception as exc:
        check("a second run of the same season is refused",
              False, f"raised {type(exc).__name__}: {exc}")

    when_it_goes_wrong(keep=keep)

    print()
    for line in notes:
        print(f"NOTE  {line}")
    if keep:
        print(f"sandbox kept at {SANDBOX}")
    else:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILURE(S): {failures}")
        return 1
    print("\nthe offseason runs end to end, and survives every way it is known to go wrong")
    return 0


# ---------------------------------------------------------------------------------------
# the half that matters more: what happens when something is wrong
# ---------------------------------------------------------------------------------------
def quiet(*a, **k):
    pass


def rename_in_save(path, old_name, new_first, new_last, dob=None):
    """Rename a record, so the character the store believes in is not the one in the file.

    `dob` copies a birthday over too, which is what it takes to make a real duplicate:
    `_locate` matches on name AND birthday, so two Jarls Bergmans born on different days are
    not ambiguous to it - and should not be, because it can still tell them apart.
    """
    L = LeagueDat(path)
    pl = L.find(old_name)
    L.rename(pl, new_first, new_last)
    if dob:
        pl = L.find(f"{new_first} {new_last}", pl.dob)
        month, day, year = (int(v) for v in ch.codec_dob(dob).split("/"))
        L.set(pl, "BirthMonth", month)
        L.set(pl, "BirthDay", day)
        L.set(pl, "BirthYear", year)
    L.save()


def when_it_goes_wrong(keep=False):
    print("\n========  when something is wrong  ========")

    # ---- 1. the destination league is full ----------------------------------------------
    print("\n---- every college slot is taken ----")
    paths, store_path, season, made = setup(log=quiet)
    # Claim every free college reserve slot with characters who are not in the save at all.
    # free_slots reads the STORE's claims, which is what decides whether there is room.
    holders = [c for c in localstore.characters(league="college") if c.get("claimed_slot")]
    taken = [{"name": c["claimed_slot"].get("name"), "dob": c["claimed_slot"].get("dob")}
             for c in holders]
    for n, slot in enumerate(ch.free_slots(MANIFEST, "college", taken)):
        row = localstore.add_character({
            "id": f"squatter-{n}", "first_name": "Squatter", "last_name": f"Number{n}",
            "league": "college", "position": "C", "height_inches": 76, "archetype": "big",
            "ratings": {}, "potentials": {}, "traits": {},
        })
        localstore.activate_character(row["id"], "college", slot.team, slot.as_json(), slot.dob)
        localstore.set_character_status(row["id"], "retired")   # not judged, still holding
    before = {k: paths[k].read_bytes() for k in LEAGUES}
    try:
        result = offseason.run_offseason(localstore, season=season, log=quiet)
        ran = True
    except Exception as exc:
        ran = False
        check("a full destination league does not abort the offseason", False,
              f"{type(exc).__name__}: {exc}")
    if ran:
        check("a full destination league does not abort the offseason", True)
        stuck = [f for f in result["failed"] if f["stage"] == "promote"]
        check("the character who could not move is reported, by name",
              len(stuck) == 1 and "no reserve slot" in stuck[0]["error"],
              str(stuck[:1]))
        rows = {c["id"]: c for c in localstore.characters()}
        blocked = next(m for m in made if m["who"] == "ages out of prep")
        still = rows[blocked["id"]]
        check("and he is left where he was, not half-moved",
              still.get("league") == "prep" and still.get("claimed_slot"),
              f'{still.get("league")}, slot {(still.get("claimed_slot") or {}).get("name")}')
        check("his prep slot was NOT handed back while he is still standing on it",
              in_save(paths["prep"], "Brie Halloumi") is not None)
        check("everybody else still moved", result["grown"] >= 1 and result["drafted"])

    # ---- 2. the game deleted his record --------------------------------------------------
    print("\n---- the game aged somebody out of the file ----")
    paths, store_path, season, made = setup(log=quiet)
    rename_in_save(paths["pro"], "Munster Kaas", "Somebody", "Else")
    try:
        result = offseason.run_offseason(localstore, season=season, log=quiet)
        ran = True
    except Exception as exc:
        ran = False
        check("a character missing from the save does not abort the offseason", False,
              f"{type(exc).__name__}: {exc}")
    if ran:
        check("a character missing from the save does not abort the offseason", True)
        rows = {c["id"]: c for c in localstore.characters()}
        ghost = next(m for m in made if m["who"] == "journeyman, just gets paid")
        row = rows[ghost["id"]]
        check("the game having deleted him ends his career, with a reason that says so",
              row.get("status") == "retired" and "no record left" in (row.get("retired_reason") or ""),
              f'{row.get("status")}: {row.get("retired_reason")}')
        check("his slot is NOT offered to somebody else, because the row is gone",
              row.get("claimed_slot") is not None,
              "a slot handed out here cannot be stamped into")

    # ---- 3. two records answer to the same name ------------------------------------------
    print("\n---- two players with his name ----")
    paths, store_path, season, made = setup(log=quiet)
    twinned = next(m for m in made if m["who"] == "stays in college")
    twin = next(p for p in LeagueDat(paths["college"]).players if p.name != "Jarls Bergman")
    rename_in_save(paths["college"], twin.name, "Jarls", "Bergman", dob=twinned["dob"])
    try:
        result = offseason.run_offseason(localstore, season=season, log=quiet)
        ran = True
    except Exception as exc:
        ran = False
        check("an ambiguous name does not abort the offseason", False,
              f"{type(exc).__name__}: {exc}")
    if ran:
        check("an ambiguous name does not abort the offseason", True)
        rows = {c["id"]: c for c in localstore.characters()}
        row = rows[twinned["id"]]
        check("he is reported rather than retired or guessed at",
              row.get("status") == "active"
              and any("answers to" in f["error"] for f in result["failed"]),
              f'{row.get("status")}; failures {[f["error"][:40] for f in result["failed"]]}')

    # ---- 4. the write fails halfway through ----------------------------------------------
    print("\n---- a save refuses the write, halfway through ----")
    paths, store_path, season, made = setup(log=quiet)
    before = {k: paths[k].read_bytes() for k in LEAGUES}
    real_commit = ch.commit

    def commit_that_fails_on_pro(L, expect):
        if "pro" in str(L.path):
            raise ch.ApplyError("write verification failed: the pro save did not take it")
        return real_commit(L, expect)

    ch.commit = commit_that_fails_on_pro
    try:
        offseason.run_offseason(localstore, season=season, log=quiet)
        check("a failed write stops the offseason", False, "it finished anyway")
    except ch.ApplyError:
        check("a failed write stops the offseason", True)
    except Exception as exc:
        check("a failed write stops the offseason", False,
              f"raised {type(exc).__name__} instead: {exc}")
    finally:
        ch.commit = real_commit

    # The one that matters: growth reads the save's own height and adds to it, so a half-written
    # offseason plus the obvious recovery - run it again - grows those characters twice.
    from commissioner import simweek
    check("a failure after store writes preserves recovery evidence",
          bool(simweek.interrupted_run()), "journal retained")
    check("the season did not roll over on a failed run",
          localstore.get_settings().get("last_offseason") in (None, ""),
          f'last_offseason={localstore.get_settings().get("last_offseason")}')
    backups = list((SANDBOX / "backups").glob("*offseason*"))
    check("all three saves were copied before anything was written",
          len(backups) == 3, f"{[b.name for b in backups]}")
    # A save-only restore would undo growth but keep retirements and points in the store.
    try:
        offseason.run_offseason(localstore, season=season, log=quiet, force=True)
        check("a failed offseason cannot be repeated before reconciliation", False)
    except offseason.OffseasonError:
        check("a failed offseason cannot be repeated before reconciliation", True)

    # ---- 5. the week AFTER a failure that was restored ------------------------------------
    print("\n---- a sim week over a slot the store freed and the save did not ----")
    paths, store_path, season, made = setup(log=quiet)
    from commissioner import simweek

    # Exactly what a restored offseason can leave: the store has let go of the claim, and the
    # save still has the character's name on that row, so the manifest name is in neither.
    freed = next(m for m in made if m["who"] == "grows and stays")
    localstore.set_character_field(freed["id"], "team_abbrev", None)
    data = json.loads(store_path.read_text(encoding="utf-8"))
    for row in data["characters"]:
        if row["id"] == freed["id"]:
            row["claimed_slot"] = None
            row["status"] = "retired"
    # and somebody new is waiting for a slot in that league
    data["characters"].append({
        "id": "newcomer", "first_name": "Havarti", "last_name": "Nystrom",
        "league": "prep", "position": "C", "status": "pending", "height_inches": 70,
        "weight_lbs": 145, "build": "solid", "archetype": "big", "traits": {"height_genes": 50},
        "ratings": {r: 40 for r in ROSTER_RATINGS}, "potentials": {}, "points_available": 20,
        "points_spent": 0,
    })
    store_path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    # The broken slot has to be the ONLY one left, or pick_slot simply chooses another and the
    # path this scenario exists for is never taken.
    holders = [c for c in localstore.characters(league="prep") if c.get("claimed_slot")]
    taken = [{"name": c["claimed_slot"].get("name"), "dob": c["claimed_slot"].get("dob")}
             for c in holders]
    free = ch.free_slots(MANIFEST, "prep", taken)
    broken = freed["slot"]["name"]
    blocked = 0
    for n, slot in enumerate(free):
        if slot.name == broken:
            continue
        row = localstore.add_character({
            "id": f"prep-squatter-{n}", "first_name": "Squatter", "last_name": f"Prep{n}",
            "league": "prep", "position": "C", "height_inches": 74, "archetype": "big",
            "ratings": {}, "potentials": {}, "traits": {},
        })
        localstore.activate_character(row["id"], "prep", slot.team, slot.as_json(), slot.dob)
        localstore.set_character_status(row["id"], "retired")
        blocked += 1
    left = [s.name for s in ch.free_slots(MANIFEST, "prep", [
        {"name": c["claimed_slot"].get("name"), "dob": c["claimed_slot"].get("dob")}
        for c in localstore.characters(league="prep") if c.get("claimed_slot")])]
    check("the only prep slot the store offers is the one the save cannot produce",
          left == [broken], f"{left[:3]} (blocked {blocked})")

    said = []
    try:
        activated, expect, writes = simweek._activate_pending(
            "prep", LeagueDat(paths["prep"]), localstore, said.append, season=season)
        check("a slot the save cannot produce does not abort the week", True)
        check("the newcomer stays pending rather than being half-placed",
              not activated, f"{len(activated)} activated")
        check("and the week says out loud who it could not place",
              any("could not place" in line for line in said),
              "; ".join(line.strip() for line in said[:3]))
    except Exception as exc:
        check("a slot the save cannot produce does not abort the week", False,
              f"{type(exc).__name__}: {exc}")

    # ---- 4. nobody is playing -------------------------------------------------------------
    print("\n---- an offseason with no characters at all ----")
    build_sandbox()
    paths = {k: SANDBOX / "saves" / k / "league.dat" for k in LEAGUES}
    redirect_everything(paths, SANDBOX / "local_store.json")
    localstore.set_setting("current_season", 2030)
    try:
        empty = offseason.run_offseason(localstore, season=2030, log=quiet)
        check("an empty universe rolls over without complaint",
              empty["grown"] == 0 and not empty["failed"], str(empty["failed"][:2]))
        check("and it still advances the season",
              int(localstore.get_settings().get("current_season")) == 2031)
    except Exception as exc:
        check("an empty universe rolls over without complaint", False,
              f"{type(exc).__name__}: {exc}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python tools/offseason_rehearsal.py",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true",
                    help="leave the sandbox in tmp/rehearsal instead of deleting it")
    args = ap.parse_args(argv)
    return rehearse(keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
