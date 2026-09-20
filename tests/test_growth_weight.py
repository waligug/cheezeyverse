"""Weight follows a character as he grows, and the offseason writes it where it is checked.

Height has moved every offseason since the model existed; weight was written once, when he was
stamped onto his slot, and then never again. A character who left prep at 5'10" and finished at
7'2" was still listed at the weight a fourteen-year-old chose, which is both wrong on the page
and wrong in the sim - FBPB3 rebounds and posts up with the body it has.

Three things have to hold, and only the first is obvious:

  1. a character who grew is heavier in the FILE afterwards
  2. a character who grew NOTHING is heavier too, if he is still under eighteen - the frame
     curve fills him out by four pounds a year whether or not he gained an inch, and the old
     control flow skipped him entirely
  3. both writes are in the same `expect` entry, so ch.commit re-reads the file and refuses the
     whole offseason if either did not land

Runs on a COPY of a real save, with the backup directory redirected into the same temp folder,
so it neither touches a league nor leaves anything behind. Skips when no save is on the machine.

    python tests/test_growth_weight.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import characters as ch  # noqa: E402
from commissioner import growth, offseason  # noqa: E402
from commissioner.codec.league_dat import LeagueDat  # noqa: E402

LEAGUE = "prep"
failures = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def a_character(player, character_id, genes):
    """A live character standing on a real record in the save.

    `game_dob` is the player's OWN birthday, not one invented here: the codec finds him by
    (name, dob), so a made-up date finds nobody. His age therefore comes from the season the
    caller picks - see `make()`.
    """
    first, _, last = player.name.partition(" ")
    return {
        "id": character_id,
        "first_name": first, "last_name": last,
        "league": LEAGUE, "status": "active",
        "game_dob": player.dob,
        "height_inches": player.values["Height"],
        "weight_lbs": player.values["Weight"],
        "build": "solid",
        "traits": {"height_genes": genes},
    }


def an_id_that(grows, player, age, genes):
    """A character id whose seeded curve grows him this offseason, or does not.

    The curve is seeded off the id, so whether a given character grows in a given year is
    decided by his id and nothing the test can set. Rather than hope, this asks the model for
    an id that does what the case needs - which is also what keeps the test deterministic.
    """
    for n in range(200):
        cid = f'{"grow" if grows else "stall"}-{n}'
        inches = growth.grew_this_offseason(cid, player.values["Height"], genes, age)
        if (inches > 0) == grows:
            return cid
    return None


def run_offseason_on_a_copy(make_characters, season=2030):
    """apply_growth against a throwaway copy; returns what the FILE says afterwards."""
    source = ch.save_path(LEAGUE)
    if not source.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "league.dat"
        shutil.copy2(source, copy)
        backups = Path(tmp) / "backups"
        backups.mkdir()

        real_path, real_backups = ch.save_path, ch.BACKUPS
        ch.save_path = lambda key: copy            # noqa: ARG005 - the point is to ignore it
        ch.BACKUPS = backups
        try:
            L = LeagueDat(copy)
            characters = make_characters(L, season)
            before = {}
            for c in characters:
                pl = L.find(f'{c["first_name"]} {c["last_name"]}', ch.codec_dob(c["game_dob"]))
                before[c["id"]] = dict(pl.values)
            grown, expect = offseason.apply_growth(LEAGUE, characters, season, log=lambda *a: None)
            after = LeagueDat(copy)
            files = {}
            for c in characters:
                pl = after.find(f'{c["first_name"]} {c["last_name"]}', ch.codec_dob(c["game_dob"]))
                files[c["id"]] = dict(pl.values)
            return {"characters": characters, "before": before, "file": files,
                    "grown": grown, "expect": expect}
        finally:
            ch.save_path, ch.BACKUPS = real_path, real_backups


def pick(L, n):
    """n players who are unambiguous in the save - a name the codec can find exactly once."""
    seen, out = {}, []
    for p in L.players:
        seen[(p.name, p.dob)] = seen.get((p.name, p.dob), 0) + 1
    for p in L.players:
        if seen[(p.name, p.dob)] == 1 and 60 <= p.values["Height"] <= 90:
            out.append(p)
        if len(out) == n:
            break
    return out


def main():
    if not ch.save_path(LEAGUE).exists():
        print(f"SKIP  no {LEAGUE} save on this machine ({ch.save_path(LEAGUE)})")
        return 0

    # Two players born in the same year, so one season makes them both sixteen: old enough to
    # have been through an offseason, young enough that the frame curve is still filling them
    # out. One id that grows this year and one that does not.
    def make(L, season):
        players = pick(L, 40)
        by_year = {}
        for p in players:
            by_year.setdefault(p.dob.split("/")[-1], []).append(p)
        pair = next((v for v in by_year.values() if len(v) >= 2), None)
        if not pair:
            return []
        age = season - int(pair[0].dob.split("/")[-1])
        grower_id = an_id_that(True, pair[0], age, 95)
        stayer_id = an_id_that(False, pair[1], age, 0)
        if not grower_id or not stayer_id:
            return []
        return [a_character(pair[0], grower_id, 95), a_character(pair[1], stayer_id, 0)]

    # The season is chosen from the save's own birth years inside make(), so read it back off
    # the characters rather than fixing a number here.
    out = run_offseason_on_a_copy(make)
    if out is None or not out["characters"]:
        print("SKIP  could not build two findable characters from the save")
        return 0

    by_id = {c["id"]: c for c in out["characters"]}
    for cid, c in by_id.items():
        before, after = out["before"][cid], out["file"][cid]
        age = offseason.age_of(c, 2030)
        want = growth.weight_step(c, before["Weight"], before["Height"], after["Height"], age)
        check(f"{cid}: the file holds this year's weight", after["Weight"] == want,
              f'file {after["Weight"]}, model {want} (was {before["Weight"]})')
        check(f"{cid}: he is heavier than he was", after["Weight"] > before["Weight"],
              f'{before["Weight"]} -> {after["Weight"]}')

    grew = [g for g in out["grown"] if g["inches"] > 0]
    check("somebody actually grew, so the height half still works", grew,
          f'{len(grew)} of {len(by_id)}')

    stalled = [cid for cid in by_id
               if out["file"][cid]["Height"] == out["before"][cid]["Height"]]
    check("a character who gained no inches still filled out", stalled,
          "every test character grew, so the no-inches path was not exercised"
          if not stalled else f"{stalled} gained no height and still gained weight")

    # The catch-up: a character carrying a filler's old weight walks to the truth over a few
    # offseasons rather than jumping, but he gets ALL of this year's own growth on the way.
    him = {"weight_lbs": None, "height_inches": 72, "build": "strong"}
    cap = growth.WEIGHT_CATCHUP_PER_YEAR
    bad = []
    if cap is not None:
        wrong_weight, height, age, seen = 120, 72, 15, []
        for _ in range(8):
            wrong_weight = growth.weight_step(him, wrong_weight, height, height + 1, age)
            height, age = height + 1, age + 1
            seen.append(wrong_weight)
        if seen[-1] != growth.weight_at(him, height, age - 1):
            bad.append(f"never caught up: {seen}")
        if any(b < a for a, b in zip(seen, seen[1:])):
            bad.append(f"the catch-up goes backwards: {seen}")
        # this year's real change always lands in full - the cap only rations the old error
        target = growth.weight_at(him, 76, 17)
        natural = target - growth.weight_at(him, 75, 16)
        stepped = growth.weight_step(him, 100, 75, 76, 17)
        if stepped - 100 < natural:
            bad.append(f"a year's own growth was rationed: {stepped - 100} < {natural}")
        if stepped - 100 > natural + cap:
            bad.append(f"more than the cap was corrected: {stepped - 100}")
    right = dict(him, weight_lbs=growth.build_weight(72, "strong"))
    at_home = growth.weight_step(right, growth.weight_at(right, 73, 15), 73, 74, 16)
    if at_home != growth.weight_at(right, 74, 16):
        bad.append("a character already on the curve was moved off it")
    check("an old wrong weight is walked to the truth, not snapped to it", not bad, str(bad))

    # Moving up a level must not undo any of it. promote() re-stamps him onto a slot in the
    # next league's save, and it reads his body out of the save he is leaving - so a source
    # record's Height and Weight both have to arrive intact. This checks the SHAPE of what
    # promote() hands stamp_character, without a second league's save to write into: a dict
    # carrying his current weight stamps that weight, and one without it silently does not.
    source = {"height_inches": 84, "weight_lbs": 214}
    carried = growth.weight_for_save(source)
    dropped = growth.weight_for_save({"height_inches": source["height_inches"]})
    check("a promoted character keeps the weight he had at the old level",
          carried == source["weight_lbs"] and dropped != source["weight_lbs"],
          f"carried {carried}, and without it he would be stamped {dropped}")

    promote_src = Path(ROOT / "commissioner" / "offseason.py").read_text(encoding="utf-8")
    body = promote_src.split("def promote(")[1].split(os.linesep + "def ")[0]
    check("promote() reads Weight out of the save he is leaving",
          'pl.values["Weight"]' in body and '"weight_lbs": weight' in body)
    check("promote() verifies the weight it wrote", '"Weight": weight' in body)

    # The guard that makes the write real: ch.commit verifies against this, and a field outside
    # it is a field nobody checks.
    fields = {f for _, _, wants in out["expect"] for f in wants}
    check("Height and Weight are verified in the same commit", "Weight" in fields, str(fields))
    check("`grown` still means people who gained inches",
          all(g["inches"] > 0 for g in out["grown"]),
          "the offseason report counts this list and names them")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("weight follows him: written every offseason, height or not, and verified with it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
