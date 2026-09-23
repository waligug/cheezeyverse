"""Stop the AI from cutting our characters, and put back the ones it already cut.

FBPB3 hands every league a generated free-agent pool of its own, and that pool is full of adults
(`Age.ini` starts at 18) who are simply better than a 14-year-old. AI general managers do their job:
during preseason they cut the worst player on the roster and sign an upgrade. On the first real sim
of the Prep league that removed **36 of 48 reserve slots** and the one real character with them.

Two things fix it, and both are needed:

1. **Defang the pool.** Every player in the save who is not ours gets crushed to the floor of the
   rating scale. The AI is comparing numbers; if nobody outside our population is worth signing,
   nobody outside our population gets signed. Their potentials go too, or the AI signs for upside.
2. **Restore the roster.** Anyone rostered who is not in the manifest is released, and every one of
   our players sitting in free agency is signed back onto the team the manifest says he belongs to.

Run after any sim that reports a character missing, and at universe creation as a matter of course.

    python tools/protect_rosters.py            # all three leagues
    python tools/protect_rosters.py prep --dry-run
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.codec.league_dat import POTENTIALS, RATINGS, LeagueDat  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402
from commissioner import characters as ch  # noqa: E402
from commissioner import ageout  # noqa: E402

BACKUPS = ROOT / "backups"
# Low enough that no GM prefers them, not zero: a rating of 0 reads as "unrated" in places and the
# league's own leaderboards look broken when everyone outside the population is a literal zero.
FLOOR_RATING = 2
FLOOR_POTENTIAL = 5
# Tendencies are not quality, so leave them alone; crushing Stamina makes the sim engine unhappy.
LEAVE_ALONE = {"3pUsage", "Fouling", "Stamina"}


def _looks_like_ours(player, manifest, key):
    """A player the manifest does not name, whose birth year is inside our band.

    The game's own free agents are adults (Age.ini starts at 18); a teenager the manifest has
    never heard of is one of our reserve rows wearing a deleted character's name.

    DRAFT-POOL players are excluded. The birth-year test alone matches them - they are teenagers
    too - and a real restore's candidate list came back as one orphan followed by three
    draft-pool sixteen-year-olds. The loop takes the first candidate in file order, so had one
    of those sat earlier in the file than the orphan, it would have been renamed into the
    reserve slot and the character's own row left behind as a stranger. An orphan released as an
    intruder goes to free agency (-1), never to the draft pool (-2), so nothing we want to find
    is lost by ruling them out.
    """
    if player.values["Team"] == -2:
        return False
    years = [int(r["dob"].split("/")[-1]) for r in manifest["players"] if r["league"] == key]
    if not years:
        return False
    return min(years) <= player.values["BirthYear"] <= max(years)


def ours(key):
    man = json.loads((ROOT / "universe" / "manifest.json").read_text(encoding="utf-8"))
    return {(r["name"], r["dob"]) for r in man["players"] if r["league"] == key}, man


def protect(key, dry_run=False, store_characters=None):
    spec = cfg.BY_KEY[key]
    path = ch.save_path(key)
    keep, man = ours(key)
    # characters have been renamed, so the manifest no longer knows them: the store does
    for c in store_characters or []:
        slot = c.get("claimed_slot") or {}
        if c.get("league") == key and slot:
            keep.discard((slot.get("name"), slot.get("dob")))
            keep.add((f'{c["first_name"]} {c["last_name"]}', ch.codec_dob(c.get("game_dob") or slot.get("dob"))))

    L = LeagueDat(path)

    # Orphaned slots: a reserve row was renamed onto a character, and that character is no longer
    # in the store (deleted, or a store restored from an older backup). The manifest name it used
    # to answer to is missing and nobody claims the row, so the slot is stranded - it can never be
    # handed to anybody again. Give it its manifest identity back.
    # `keep` already has each live character swapped in for the manifest row he holds, so a slot
    # that is legitimately claimed is NOT missing. Computing this against the raw manifest instead
    # made every claimed slot look orphaned and set the restore loop cascading through the whole
    # reserve list - it renamed a row, saw the name it had just written as the next orphan, and
    # renamed that too.
    known = {(p.name, p.dob) for p in L.players}
    claimed = set()
    for c in store_characters or []:
        slot = c.get("claimed_slot") or {}
        if c.get("league") == key and slot:
            claimed.add((slot.get("name"), slot.get("dob")))
    missing = [r for r in man["players"]
               if r["league"] == key and r["role"] == "reserve"
               and (r["name"], r["dob"]) not in known
               and (r["name"], r["dob"]) not in claimed]
    # Rostered or not: an earlier pass may already have released the orphan as an intruder,
    # which leaves it stranded in free agency instead of on a roster.
    # Rostered first. A slot that is still on a team is far more likely to be the orphan than a
    # loose free agent, and the restore loop takes whichever candidate comes first.
    unclaimed = sorted(
        (p for p in L.players if (p.name, p.dob) not in keep and _looks_like_ours(p, man, key)),
        key=lambda p: 0 if p.values["Team"] >= 1 else 1)
    if missing and unclaimed and not dry_run:
        # One rename at a time, re-finding by name each pass. `rename` splices the file and the
        # codec re-parses, so every Player object captured before it - including the rest of this
        # list - points at an offset that has moved. Holding them across a splice is how this
        # loop renamed the wrong records and then failed to find the name it had just written.
        restored = 0
        for original in missing:
            names = [p.name for p in unclaimed]
            if not names:
                break
            target = names[0]
            unclaimed = unclaimed[1:]
            try:
                orphan = L.find(target)
            except Exception as exc:
                print(f"   ! could not re-find orphan {target}: {exc}")
                continue
            try:
                # Name, birthday AND floor ratings. This used to restore only the first two,
                # so the recycled slot kept the departed character's ratings and became a
                # fully developed player masquerading as dormant filler - taking rotation
                # minutes from real characters, one more of them after every departure.
                ch.reset_reserve(L, orphan, original)
            except Exception as exc:
                print(f"   ! renamed {target} but could not re-find it: {exc}")
                continue
            print(f"   orphaned slot {target} restored to {original['name']}")
            restored += 1
            unclaimed = sorted(
                (p for p in L.players
                 if (p.name, p.dob) not in keep and _looks_like_ours(p, man, key)),
                key=lambda p: 0 if p.values["Team"] >= 1 else 1)
        # The renames live in memory until they are written. Re-reading the file without
        # saving first is how three restored Pro slots were silently thrown away: the loop
        # printed "restored", the next line reloaded the unchanged file, and the save at the
        # end of this function then wrote back a league that had never heard of them. Those
        # three rows stayed unclaimed, got defanged as strangers, and were released - which
        # is why two Pro rosters came out at 13 and 14 men.
        if restored:
            L.save(backup_dir=BACKUPS)
        # Fresh read either way: a splice moves every offset captured before it.
        L = LeagueDat(path)
        keep, man = ours(key)
        for c in store_characters or []:
            slot = c.get("claimed_slot") or {}
            if c.get("league") == key and slot:
                keep.discard((slot.get("name"), slot.get("dob")))
                keep.add((f'{c["first_name"]} {c["last_name"]}',
                          ch.codec_dob(c.get("game_dob") or slot.get("dob"))))

    mine = [p for p in L.players if (p.name, p.dob) in keep]
    theirs = [p for p in L.players if (p.name, p.dob) not in keep]
    intruders = [p for p in theirs if p.values["Team"] >= 1]
    exiled = [p for p in mine if p.values["Team"] < 1]

    print(f"{key}: {len(mine)} ours / {len(theirs)} the game's own; "
          f"{len(intruders)} intruder(s) on rosters, {len(exiled)} of ours in free agency")
    if dry_run:
        return {"defanged": len(theirs), "released": len(intruders), "signed": len(exiled)}

    # 1. defang everyone who is not ours - EXCEPT THE DRAFT POOL.
    #
    # Team -2 is the game's draft class. Nobody in it holds a roster spot, so flooring him stops
    # nothing - but it does ruin him, and he does not stay in the pool: the next draft puts him on
    # a roster. Measured 2026-09-23, every draft-pool body in all three leagues was floored (65 of
    # 65 prep, 70 of 70 college, 80 of 80 pro), so the whole incoming class was rating-3 and every
    # rollover delivered a fresh batch of 3-overalls onto pro rosters. It also poisoned OUR draft
    # board, which reads the same pool. The pool is left exactly as the game made it.
    defanged = 0
    for p in theirs:
        if p.values["Team"] == -2:
            continue
        touched = False
        for field in RATINGS:
            if field in LEAVE_ALONE:
                continue
            if p.values[field] > FLOOR_RATING:
                L.set(p, field, FLOOR_RATING)
                touched = True
        for field in POTENTIALS:
            if p.values[field] > FLOOR_POTENTIAL:
                L.set(p, field, FLOOR_POTENTIAL)
                touched = True
        defanged += 1 if touched else 0

    # A clean guard used to rewrite and reparse every 5-10 MB save anyway. This path runs before
    # every league and was charging a full save cycle for proving there was nothing to do.
    # A SHORT ROSTER IS NOT CLEAN. This shortcut used to ask only whether anything needed
    # defanging, evicting or signing back - and once the rosters had already collapsed, the
    # answer to all three was no. So the pass reported "already clean" on a prep league with a
    # SIX MAN TEAM, every publish, while the games went on being played. Teams below the roster
    # size are now a reason to do work, not a state to report as fine.
    short_now = [t for t, v in L.teams().items() if len(v["ids"]) < spec.roster_size]
    if not defanged and not intruders and not exiled and not short_now:
        sizes = Counter(len(v["ids"]) for v in L.teams().values())
        print(f"   already clean; roster sizes {dict(sizes)}")
        return {"defanged": 0, "released": 0, "signed": 0, "backfilled": 0, "unrostered": 0}

    # 2. release the intruders, then sign our exiles back onto their own teams. Do each side as
    # one structural edit. release()/sign() re-parse the entire 5-10 MB save after every player;
    # Pro commonly has 100+ intruders after preseason, which made this guard appear frozen for
    # several minutes before FBPB3 even launched.
    exiled_keys = [(p.name, p.dob) for p in exiled]
    release_groups = {}
    for p in intruders:
        release_groups.setdefault(p.values["Team"], []).append(p)
    if release_groups:
        L.release_groups(release_groups)
    exiled = [L.find(name, dob) for name, dob in exiled_keys]

    team_of = {}
    abbrev_to_id = {}
    for p in L.players:
        if p.values["Team"] >= 1:
            abbrev_to_id.setdefault(p.values["Team"], p.values["Team"])
    manifest_team = {(r["name"], r["dob"]): r["team"] for r in man["players"] if r["league"] == key}
    # team ids are ascending in the same order as the config's team table
    ids = sorted({p.values["Team"] for p in L.players if p.values["Team"] >= 1})
    by_abbrev = {t.abbrev: ids[i] for i, t in enumerate(spec.teams) if i < len(ids)}

    assignments = []
    team_sizes = {t: len(v["ids"]) for t, v in L.teams().items()}
    for p in exiled:
        abbrev = manifest_team.get((p.name, p.dob))
        if abbrev is None:
            for c in store_characters or []:
                slot = c.get("claimed_slot") or {}
                if f'{c.get("first_name")} {c.get("last_name")}' == p.name:
                    abbrev = slot.get("team")
        team_id = by_abbrev.get(abbrev)
        if team_id is None:
            print(f"   ! no team for {p.name}; left in free agency")
            continue
        if team_sizes.get(team_id, 0) >= spec.roster_size:
            # His own team filled up while he was out. Anywhere is better than free agency,
            # where the AI will not re-sign a defanged 14-year-old and he never plays again.
            room = [t for t, size in team_sizes.items() if size < spec.roster_size]
            if not room:
                print(f"   ! every roster is full; {p.name} left in free agency")
                continue
            team_id = room[0]
            print(f"   {abbrev} was full, {p.name} goes to team {team_id} instead")
        assignments.append((p, team_id))
        team_sizes[team_id] = team_sizes.get(team_id, 0) + 1

    if assignments:
        L.sign_many(assignments)
    signed = len(assignments)

    # ---- BACKFILL TO A LEGAL ROSTER --------------------------------------------------------
    # Evicting every body the manifest does not know is what this pass is for. It cannot be the
    # whole story, because OUR POPULATION IS SMALLER THAN THE LEAGUE'S ROSTER CAPACITY: prep has
    # 168 manifest players against 16 x 15 = 240 places. Eviction alone therefore cannot leave
    # sixteen legal teams, and signing our exiles back does not close the gap - the live run
    # released 89 and signed 17.
    #
    # THAT IS HOW THE ROSTERS COLLAPSED. ageout's intake recycles free-agent bodies to fill every
    # team back to fifteen, but a recycled body is not in the manifest, so the next run of this
    # pass evicted it again as a stranger. Each pass ran on every publish, so prep went {15: 16}
    # after the 2027 rollover to a team of SIX by 2029 day 32, and those were real games.
    #
    # Refilling from the pool is safe precisely because of step 1: everyone not ours has already
    # been defanged to floor ratings and potentials, so a body signed here cannot take minutes
    # from a character. Defanging is what protects the universe; eviction never was.
    #
    # Free agency only (-1), never the draft pool (-2) - the draft is how the next class arrives
    # and emptying it here would take a season of prospects out of the game.
    fills, pool_i = [], 0
    sizes_now = {t: len(v["ids"]) for t, v in L.teams().items()}
    # AGE FIRST, AND NOT BY NAME. Free agency is where a released body stays: ageout only ever
    # walks the ROSTERS, so nobody in the pool is aged out again and the over-age pile grows every
    # season. Prep's pool currently holds 72 men of 19, 20 and 21 in a league that ends at 18.
    #
    # Signing one of those to fill a short roster would put an over-age player back on a court -
    # the exact thing the cap exists to prevent, undone by the repair for a different bug. So the
    # cap is applied here too, and the youngest go first, which also keeps the intake young rather
    # than filling a team with whoever is alphabetically first.
    season = L.season_day()[1]
    cap = ageout.AGE_CAPS.get(key)

    def _age(pl):
        return ageout.age_of(pl, season)

    free = [p for p in L.players if p.values["Team"] == -1]
    eligible = [p for p in free if cap is None or _age(p) < cap]
    pool = sorted(eligible, key=lambda p: (_age(p), p.name))
    if cap is not None and len(eligible) != len(free):
        print(f"   {len(free) - len(eligible)} free agent(s) are {cap}+ and were not considered")
    for team_id in sorted(sizes_now):
        while sizes_now[team_id] < spec.roster_size and pool_i < len(pool):
            fills.append((pool[pool_i], team_id))
            sizes_now[team_id] += 1
            pool_i += 1
    if fills:
        L.sign_many(fills)
    short = [t for t, n in sizes_now.items() if n < spec.roster_size]
    if short:
        print(f"   ! {len(short)} team(s) still short; the free-agent pool is empty")

    L.save(backup_dir=BACKUPS)
    check = LeagueDat(path)

    # REGISTER WHAT WE JUST SIGNED, or this pass undoes itself for ever.
    #
    # The comment forty lines up already tells this story for prep: a recycled body is not in the
    # manifest, so the NEXT run of this pass evicts it as a stranger, refills from the pool, and
    # the pass has fresh work every publish while nothing improves. That was closed by
    # `ageout.sync_manifest` - but `ageout.apply` runs only for prep and college
    # (seasonflow.py:386), because AGE_CAPS has no 'pro' key. So pro never registered anything
    # and kept churning: measured on the live panel log, "pro: 11 intruder(s) ... released 11,
    # backfilled 11" on EVERY publish, while prep said "already clean" and college released none.
    #
    # Registering here rather than in ageout fixes it for whichever league is being guarded,
    # including the one that has no age-out at all. sync_manifest rebuilds from who is actually
    # rostered and excludes `keep`, so characters and reserve seats are never touched.
    if fills and not dry_run:
        try:
            # THE PROTECTED SET IS NARROW, and getting it wrong is destructive in both
            # directions. sync_manifest REBUILDS this league's filler rows from whoever is
            # rostered and skips `pl.name in keep`, so:
            #   - too NARROW (the (name, dob) tuples this function carries, which never match a
            #     bare name) registers the sixty reserve seats as ordinary filler, and a seat
            #     registered as filler is one the next pass may recycle out from under a signup;
            #   - too WIDE (every manifest name) skips almost everybody, drops 240 live rows and
            #     sets off a far worse churn than the one being fixed.
            # Both were measured on the way to this line. What is wanted is exactly what
            # ageout.protected_names means: the reserve SEATS, plus our characters under the
            # names they actually play under.
            protected = {r["name"] for r in man["players"]
                         if r["league"] == key and r.get("role") == "reserve"}
            for c in (store_characters or []):
                if c.get("league") == key:
                    protected.add(f'{c["first_name"]} {c["last_name"]}')
            man2, registered, dropped = ageout.sync_manifest(check, key, protected, man)
            if registered or dropped:
                (ROOT / "universe" / "manifest.json").write_text(
                    json.dumps(man2, indent=1), encoding="utf-8")
                # COUNTS, not the lists themselves. sync_manifest returns the names, and on the
                # first pro run that was seventy-one of them printed as a single wall of text
                # across the panel's live log.
                print(f"   registered {len(registered)} rostered bod"
                      f"{'y' if len(registered) == 1 else 'ies'} in the manifest, "
                      f"dropped {len(dropped)} stale row(s)")
        except Exception as exc:                                        # noqa: BLE001
            # Never fatal: an unregistered backfill is churn, a crashed roster guard is a
            # league nobody tidied at all.
            print(f"   (could not register the backfill: {exc}; it will be evicted next run)")

    sizes = Counter(len(v["ids"]) for v in check.teams().values())
    still_out = [p.name for p in check.players
                 if (p.name, p.dob) in keep and p.values["Team"] < 1]
    print(f"   defanged {defanged}, released {len(intruders)}, signed {signed}, "
          f"backfilled {len(fills)}; roster sizes {dict(sizes)}; "
          f"{len(still_out)} of ours still unrostered")
    return {"defanged": defanged, "released": len(intruders), "signed": signed,
            "backfilled": len(fills), "unrostered": len(still_out)}


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]
    dry = "--dry-run" in sys.argv
    # Running this with an empty character list is far worse than not running it at all: a
    # claimed reserve row answers to a name the manifest does not know, so with no characters
    # it looks BOTH like a missing reserve and like a stranger - it gets defanged to floor
    # ratings and renamed back to its manifest identity, which is a deleted character. The old
    # `except: chars = []` turned any store hiccup into exactly that. Refuse instead.
    from commissioner import simweek
    try:
        chars = simweek.store().characters()
    except Exception as exc:
        sys.exit(
            f"cannot read the characters from the store ({exc}). Refusing to run: "
            "without them this pass would defang and rename live characters. Pass "
            "--no-characters only if you are certain the universe has none.")
    if not chars and "--no-characters" not in sys.argv:
        print("note: the store reports no characters yet; nothing to protect from the rename pass")
    for k in keys:
        protect(k, dry_run=dry, store_characters=chars)
