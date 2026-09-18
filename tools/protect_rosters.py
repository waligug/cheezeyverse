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

BACKUPS = ROOT / "backups"
# Low enough that no GM prefers them, not zero: a rating of 0 reads as "unrated" in places and the
# league's own leaderboards look broken when everyone outside the population is a literal zero.
FLOOR_RATING = 2
FLOOR_POTENTIAL = 5
# Tendencies are not quality, so leave them alone; crushing Stamina makes the sim engine unhappy.
LEAVE_ALONE = {"3pUsage", "Fouling", "Stamina"}


def _looks_like_ours(player, manifest, key):
    """A rostered player the manifest does not name, whose birth year is inside our band.

    The game's own free agents are adults (Age.ini starts at 18); a rostered teenager the
    manifest has never heard of is one of our reserve rows wearing a deleted character's name.
    """
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
            keep.add((f'{c["first_name"]} {c["last_name"]}', c.get("game_dob") or slot.get("dob")))

    L = LeagueDat(path)

    # Orphaned slots: a reserve row was renamed onto a character, and that character is no longer
    # in the store (deleted, or a store restored from an older backup). The manifest name it used
    # to answer to is missing and nobody claims the row, so the slot is stranded - it can never be
    # handed to anybody again. Give it its manifest identity back.
    known = {(p.name, p.dob) for p in L.players}
    missing = [r for r in man["players"]
               if r["league"] == key and r["role"] == "reserve"
               and (r["name"], r["dob"]) not in known]
    # Rostered or not: an earlier pass may already have released the orphan as an intruder,
    # which leaves it stranded in free agency instead of on a roster.
    unclaimed = [p for p in L.players if (p.name, p.dob) not in keep
                 and _looks_like_ours(p, man, key)]
    if missing and unclaimed and not dry_run:
        for orphan, original in zip(unclaimed, missing):
            first, _, last = original["name"].partition(" ")
            L.rename(orphan, first, last)
            back = L.find(original["name"])
            month, day, year = (int(v) for v in original["dob"].split("/"))
            L.set(back, "BirthMonth", month)
            L.set(back, "BirthDay", day)
            L.set(back, "BirthYear", year)
            print(f"   orphaned slot {orphan.name} restored to {original['name']}")
        L.save(backup_dir=BACKUPS)
        L = LeagueDat(path)
        keep, man = ours(key)
        for c in store_characters or []:
            slot = c.get("claimed_slot") or {}
            if c.get("league") == key and slot:
                keep.discard((slot.get("name"), slot.get("dob")))
                keep.add((f'{c["first_name"]} {c["last_name"]}',
                          c.get("game_dob") or slot.get("dob")))

    mine = [p for p in L.players if (p.name, p.dob) in keep]
    theirs = [p for p in L.players if (p.name, p.dob) not in keep]
    intruders = [p for p in theirs if p.values["Team"] >= 1]
    exiled = [p for p in mine if p.values["Team"] < 1]

    print(f"{key}: {len(mine)} ours / {len(theirs)} the game's own; "
          f"{len(intruders)} intruder(s) on rosters, {len(exiled)} of ours in free agency")
    if dry_run:
        return {"defanged": len(theirs), "released": len(intruders), "signed": len(exiled)}

    # 1. defang everyone who is not ours
    defanged = 0
    for p in theirs:
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

    # 2. release the intruders, then sign our exiles back onto their own teams
    for p in intruders:
        L.release(L.find(p.name, p.dob))

    team_of = {}
    abbrev_to_id = {}
    for p in L.players:
        if p.values["Team"] >= 1:
            abbrev_to_id.setdefault(p.values["Team"], p.values["Team"])
    manifest_team = {(r["name"], r["dob"]): r["team"] for r in man["players"] if r["league"] == key}
    # team ids are ascending in the same order as the config's team table
    ids = sorted({p.values["Team"] for p in L.players if p.values["Team"] >= 1})
    by_abbrev = {t.abbrev: ids[i] for i, t in enumerate(spec.teams) if i < len(ids)}

    signed = 0
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
        if len(L.teams().get(team_id, {}).get("ids", ())) >= spec.roster_size:
            # His own team filled up while he was out. Anywhere is better than free agency,
            # where the AI will not re-sign a defanged 14-year-old and he never plays again.
            room = [t for t, v in L.teams().items() if len(v["ids"]) < spec.roster_size]
            if not room:
                print(f"   ! every roster is full; {p.name} left in free agency")
                continue
            team_id = room[0]
            print(f"   {abbrev} was full, {p.name} goes to team {team_id} instead")
        L.sign(L.find(p.name, p.dob), team_id)
        signed += 1

    L.save(backup_dir=BACKUPS)
    check = LeagueDat(path)
    sizes = Counter(len(v["ids"]) for v in check.teams().values())
    still_out = [p.name for p in check.players
                 if (p.name, p.dob) in keep and p.values["Team"] < 1]
    print(f"   defanged {defanged}, released {len(intruders)}, signed {signed}; "
          f"roster sizes {dict(sizes)}; {len(still_out)} of ours still unrostered")
    return {"defanged": defanged, "released": len(intruders), "signed": signed,
            "unrostered": len(still_out)}


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.key for s in cfg.LEAGUES]
    dry = "--dry-run" in sys.argv
    try:
        from commissioner import simweek
        chars = simweek.store().characters()
    except Exception:
        chars = []
    for k in keys:
        protect(k, dry_run=dry, store_characters=chars)
