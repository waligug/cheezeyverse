"""The skill tree's Python half: its nodes, its arithmetic, and how the commissioner uses it.

The SQL half is tests/test_skill_tree_sql.py. Where a live Postgres is available (CV_PG_DSN) this
file also buys every node through the real apply_upgrade_requests and checks the stored sheet
lands exactly where commissioner/tree.py says it does - the two must never disagree, because the
database moves the stored sheet and tree.py moves league.dat.

    python tests/test_tree.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import tree, seasonflow, simweek, offseason  # noqa: E402
from commissioner import characters as ch  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


RATINGS = ['InsideScoring', 'JumpShot', 'FtShot', '3pUsage', '3pShot', 'Handling', 'Passing', 'Quickness',
           'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking', 'OReb', 'DReb', 'Jumping', 'Strength',
           'Stamina', 'Fouling']


def shape():
    ids = [n["id"] for n in tree.TREE]
    check("25 nodes, every id unique", len(ids) == 25 and len(set(ids)) == 25, str(len(ids)))
    for branch in tree.BRANCHES:
        mine = [n for n in tree.TREE if n["branch"] == branch]
        tiers = sorted(n["tier"] for n in mine)
        check(f"{branch}: tiers 1, 2, 3 and two Signatures", tiers == [1, 2, 3, 4, 4], str(tiers))
    known = set(ids)
    check("every requirement names a real node",
          all(r in known for n in tree.TREE for r in n["requires"]))
    check("every Signature is in the one group", all((n["group_key"] == "signature") == (n["tier"] == 4)
                                                     for n in tree.TREE))
    bad = []
    for n in tree.TREE:
        fx = n["effects"]
        for k in fx.get("potentials", {}):
            if k not in ch.POT_BY_RATING:
                bad.append((n["id"], "ceiling", k))
        for k in list(fx.get("ratings", {})) + list(fx.get("tendency", {})) + list(fx.get("bias", {})):
            if k not in RATINGS or k == "Fouling":
                bad.append((n["id"], "rating", k))
        for b in fx.get("shield", {}):
            if b not in tree.BRANCHES:
                bad.append((n["id"], "shield", b))
    check("every effect names a rating the game has (ceilings only where FBPB3 has one)", not bad, str(bad))
    check("costs climb with the tier", all(n["cost"] == tree.TIER_COST[n["tier"]] for n in tree.TREE))
    check("stages open with the tier", all(n["stage"] == tree.TIER_STAGE[n["tier"]] for n in tree.TREE))


def arithmetic():
    r = {k: 50 for k in RATINGS}
    p = {k: 60 for k in ch.POT_BY_RATING}
    r2, p2 = tree.sheet_after(r, p, tree.BY_ID["scoring-sig-deadeye"]["effects"])
    check("Deadeye: +9 3PT, +7 Jump Shot, +10 3PT Usage", (r2["3pShot"], r2["JumpShot"], r2["3pUsage"]) == (59, 57, 60))
    check("Deadeye: both ceilings +8", (p2["3pShot"], p2["JumpShot"]) == (68, 68))
    r = {k: 148 for k in RATINGS}
    p = {k: 148 for k in ch.POT_BY_RATING}
    r2, p2 = tree.sheet_after(r, p, tree.BY_ID["defense-sig-rim-protector"]["effects"])
    check("a rating with a ceiling stops at 150", r2["Blocking"] == 150 and p2["Blocking"] == 150)
    r = {k: 97 for k in RATINGS}
    r2, _ = tree.sheet_after(r, {}, tree.BY_ID["athletic-sig-iron-man"]["effects"])
    check("a rating without one stops at 100", r2["Stamina"] == 100 and r2["Strength"] == 100)
    r = {k: 70 for k in RATINGS}
    p = {k: 70 for k in ch.POT_BY_RATING}
    r2, p2 = tree.sheet_after(r, p, tree.BY_ID["scoring-sig-paint-beast"]["effects"])
    check("a ceiling is never left below its rating", p2["InsideScoring"] >= r2["InsideScoring"] == 79, f"{r2['InsideScoring']} {p2['InsideScoring']}")
    values = {k: 50 for k in RATINGS} | {pot: 60 for pot in ch.POT_BY_RATING.values()}
    changes = tree.save_changes(values, tree.BY_ID["rebounding-1"]["effects"])
    check("save_changes speaks codec: PotOReb and PotDReb +6", changes == {"PotOReb": 66, "PotDReb": 66}, str(changes))
    check("a Cap Breaker is +3 on the ceiling, in codec names", tree.breaker_changes(values, "DReb") == {"PotDReb": 63})
    check("a Cap Breaker on a rating with no ceiling does nothing", tree.breaker_changes(values, "Stamina") == {})
    check("shields come from tier 3", tree.shields(["defense-3", "scoring-1"]) == {"Defense": 0.5})
    check("the switch reads jsonb true and the string", tree.tree_on({"skill_tree_enabled": True})
          and tree.tree_on({"skill_tree_enabled": "true"}) and not tree.tree_on({}))


def aging():
    sheet = {k: 100 for k in RATINGS} | {pot: 100 for pot in ch.POT_BY_RATING.values()}
    plain = seasonflow.regress(sheet, 35)
    shielded = seasonflow.regress(sheet, 35, {"Defense": 0.5})
    check("at 35 a rating loses 10%", plain["Blocking"] == 90 and plain["InsideScoring"] == 90)
    check("a tier-3 shield halves it for that branch only", shielded["Blocking"] == 95 and shielded["InsideScoring"] == 90)
    check("and its ceilings", shielded["PotBlocking"] == 95 and shielded["PotInside"] == 90)


def rollover():
    before = {"DReb": 80, "PotDReb": 90, "Blocking": 60, "PotBlocking": 70, "JumpShot": 50, "PotJumpShot": 55}
    after = {"DReb": 72, "PotDReb": 93, "Blocking": 58, "PotBlocking": 60, "JumpShot": 54, "PotJumpShot": 52}
    out = seasonflow.rollover_two_way(before, after, 5)
    check("a fall of 8 counts as 5", out["DReb"] == 75)
    check("a rise is kept", out["PotDReb"] == 93 and out["JumpShot"] == 54)
    check("a small fall counts in full", out["Blocking"] == 58)
    check("a ceiling fall of 10 counts as 5", out["PotBlocking"] == 65)
    check("a ceiling is never below its rating", out["PotJumpShot"] == 54)


class FakeL:
    def __init__(self, values):
        self.player = type("P", (), {"values": dict(values), "name": "Test Kid", "dob": "1/2/2020"})()

    def find(self, name, dob=None):
        return self.player

    def set(self, pl, field, value):
        pl.values[field] = int(value)


class FakeStore:
    def __init__(self, rows, nodes=None, settings=None):
        self.rows, self.rejected, self.nodes, self.settings = rows, [], nodes, settings or {}

    def pending_requests(self, league=None):
        return self.rows

    def reject_request(self, rid, why):
        self.rejected.append((rid, why))

    def get_settings(self):
        return self.settings

    def tree_nodes(self):
        return self.nodes


def applying():
    values = {k: 50 for k in RATINGS} | {pot: 60 for pot in ch.POT_BY_RATING.values()}
    character = {"first_name": "Test", "last_name": "Kid", "game_dob": "2020-01-02", "claimed_slot": {}}
    rows = [
        {"id": 1, "character_id": "c", "status": "approved", "kind": "rating", "rating": "DReb", "delta": 3, "character": character},
        {"id": 2, "character_id": "c", "status": "approved", "kind": "breaker", "rating": "DReb", "delta": 1, "character": character},
        {"id": 3, "character_id": "c", "status": "approved", "kind": "node", "rating": "rebounding-1", "delta": 1, "character": character},
        {"id": 4, "character_id": "c", "status": "approved", "kind": "node", "rating": "no-such-node", "delta": 1, "character": character},
        {"id": 5, "character_id": "c", "status": "approved", "kind": "breaker", "rating": "Stamina", "delta": 1, "character": character},
    ]
    L = FakeL(values)
    st = FakeStore(rows)
    old = ch.apply_deltas
    try:
        def fake_apply(L_, name, dob, deltas):
            pl = L_.find(name, dob)
            moved = {}
            for f, d in deltas.items():
                was = pl.values[f]
                pl.values[f] = was + d
                moved[f] = (was, was + d)
            return moved
        ch.apply_deltas = fake_apply
        applied, expect = simweek._apply_requests("pro", L, st, lambda m: None)
    finally:
        ch.apply_deltas = old
    v = L.player.values
    check("apply: a rating, a Cap Breaker and a node land together", sorted(applied) == [1, 2, 3], str(applied))
    check("apply: DReb 50 +3, its ceiling 60 +3 (breaker) +6 (node)", v["DReb"] == 53 and v["PotDReb"] == 69, f"{v['DReb']} {v['PotDReb']}")
    check("apply: OReb's ceiling +6 from the node", v["PotOReb"] == 66)
    check("apply: an unknown node and a breaker on a rating with no ceiling are rejected, not fatal",
          sorted(r for r, _ in st.rejected) == [4, 5], str(st.rejected))
    check("apply: the save is checked for what the node wrote", expect and expect[0][2].get("PotOReb") == 66, str(expect))


def cap_breakers():
    class S:
        def __init__(self):
            self.rows = [
                {"id": "a", "status": "active", "league": "pro", "game_dob": "2010-05-01", "cap_breakers": 1, "first_name": "A", "last_name": "A"},
                {"id": "b", "status": "active", "league": "pro", "game_dob": "2000-05-01", "cap_breakers": 0, "first_name": "B", "last_name": "B"},
                {"id": "c", "status": "active", "league": "college", "game_dob": "2020-05-01", "cap_breakers": 0, "first_name": "C", "last_name": "C"},
                {"id": "d", "status": "active", "league": "college", "game_dob": "2021-05-01", "cap_breakers": 0, "first_name": "D", "last_name": "D"},
            ]
            self.set = {}

        def characters(self):
            return self.rows

        def set_character_field(self, cid, field, value):
            self.set[(cid, field)] = value
    s = S()
    given = offseason._grant_cap_breakers(s, 2039, {"c"}, {}, log=lambda m: None)
    check("Cap Breakers: a young pro and a promoted player get one each", sorted(given) == ["a", "c"], str(given))
    check("Cap Breakers: added to what he already has", s.set.get(("a", "cap_breakers")) == 2 and s.set.get(("c", "cap_breakers")) == 1, str(s.set))


def against_postgres():
    dsn = os.environ.get("CV_PG_DSN")
    try:
        import psycopg
    except ImportError:
        print("SKIP  parity with apply_upgrade_requests: psycopg is not installed")
        return
    if not dsn:
        print("SKIP  parity with apply_upgrade_requests: CV_PG_DSN is not set")
        return
    import json
    import uuid
    base = dsn.rsplit("/", 1)[0]
    try:
        conn = psycopg.connect(base + "/cv_tree_upgrade", autocommit=True, connect_timeout=5)
    except Exception as exc:                                               # noqa: BLE001
        print(f"SKIP  parity with apply_upgrade_requests: run tests/test_skill_tree_sql.py first ({exc})")
        return
    conn.execute("update public.settings set value = 'true'::jsonb where key = 'skill_tree_enabled'")
    conn.execute("delete from public.tree_nodes")
    for n in tree.TREE:
        conn.execute("insert into public.tree_nodes (id, branch, tier, name, blurb, cost, stage, requires, min_spent, "
                     "min_ratings, group_key, effects, sort) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (n["id"], n["branch"], n["tier"], n["name"], n["blurb"], 0, n["stage"], [], 0,
                      json.dumps({}), None, json.dumps(n["effects"]), n["sort"]))
    owner = conn.execute("select owner from public.characters limit 1").fetchone()[0]
    mismatches = []
    for n in tree.TREE:
        r0 = {k: 40 + (i * 7) % 60 for i, k in enumerate(RATINGS)}
        p0 = {k: r0[k] + 5 for k in ch.POT_BY_RATING}
        cid = conn.execute("insert into public.characters (owner, first_name, last_name, \"position\", height_inches, "
                           "archetype, ratings, potentials, league) values (%s,'P',%s,'SF',78,'x',%s,%s,'pro') returning id",
                           (owner, uuid.uuid4().hex[:6], json.dumps({k: 45 for k in RATINGS}),
                            json.dumps({k: 90 for k in ch.POT_BY_RATING}))).fetchone()[0]
        conn.execute("update public.characters set ratings = %s, potentials = %s, points_available = 999, status = 'active' "
                     "where id = %s", (json.dumps(r0), json.dumps(p0), cid))
        rid = conn.execute("insert into public.upgrade_requests (character_id, rating, delta, kind) values (%s,%s,1,'node') "
                           "returning id", (cid, n["id"])).fetchone()[0]
        conn.execute("update public.upgrade_requests set status = 'approved' where id = %s", (rid,))
        conn.execute("select * from public.apply_upgrade_requests(%s::uuid[])", ([rid],))
        r_db, p_db = conn.execute("select ratings, potentials from public.characters where id = %s", (cid,)).fetchone()
        r_py, p_py = tree.sheet_after(r0, p0, n["effects"])
        if r_db != r_py or p_db != p_py:
            mismatches.append(n["id"])
    check("every node moves the stored sheet exactly as tree.py moves the save", not mismatches, str(mismatches))
    conn.close()


if __name__ == "__main__":
    shape()
    arithmetic()
    aging()
    rollover()
    applying()
    cap_breakers()
    against_postgres()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("the skill tree's Python half holds up")
