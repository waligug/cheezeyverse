"""The skill tree's SQL, run against a real Postgres - the upgrade path the live project takes.

Builds the database the way the live one was built (schema.sql as it stood before the tree,
then every delta file in the order they were pasted), runs supabase/skill_tree.sql on top of
it TWICE, and then drives it the way the website and the commissioner do: as the owner through
row-level security, and as the service role.

It needs a Postgres it may create databases in. The throwaway one used on SERVERPC:

    docker run -d --name cv-pgtest -e POSTGRES_PASSWORD=cvtest -p 127.0.0.1:55432:5432 postgres:15-alpine
    set CV_PG_DSN=postgresql://postgres:cvtest@127.0.0.1:55432/postgres
    python tests/test_skill_tree_sql.py

Without psycopg or a reachable server it says so and exits 0, like the other live checks.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("CV_PG_DSN", "postgresql://postgres:cvtest@127.0.0.1:55432/postgres")

try:
    import psycopg
except ImportError:                                                   # pragma: no cover
    print("SKIP  psycopg is not installed")
    sys.exit(0)

failures = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


STUBS = r"""
create schema if not exists auth;
create table if not exists auth.users (
  id uuid primary key, email text,
  raw_user_meta_data jsonb default '{}'::jsonb, raw_app_meta_data jsonb default '{}'::jsonb);
create or replace function auth.uid() returns uuid language sql stable as
  $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
create or replace function auth.jwt() returns jsonb language sql stable as
  $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end $$;
grant usage on schema public, auth to anon, authenticated, service_role;
grant execute on all functions in schema auth to anon, authenticated, service_role;
-- what Supabase hands out by default, which schema.sql then narrows
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
"""

# The order the live project received its SQL (docs/STATUS.md and the files' own headers).
LIVE_ORDER = ["schema.sql", "weight_column.sql", "weight_range.sql", "fix_blank_display_names.sql",
              "level_income.sql", "potential_ceiling.sql"]


def old_file(name):
    """A file as it stood on master before the tree - the live project's state."""
    try:
        return subprocess.run(["git", "show", f"master:supabase/{name}"], cwd=ROOT, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout
    except subprocess.CalledProcessError:
        return (ROOT / "supabase" / name).read_text(encoding="utf-8")


def fresh_db(admin, name):
    admin.execute(f'drop database if exists "{name}"')
    admin.execute(f'create database "{name}"')
    return psycopg.connect(DSN.rsplit("/", 1)[0] + f"/{name}", autocommit=True)


def run_sql(conn, text, label):
    try:
        conn.execute(text)
        return True
    except Exception as exc:                                          # noqa: BLE001
        print(f"      {label}: {str(exc).splitlines()[0]}")
        return False


RATINGS = ['InsideScoring', 'JumpShot', 'FtShot', '3pUsage', '3pShot', 'Handling', 'Passing', 'Quickness',
           'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking', 'OReb', 'DReb', 'Jumping', 'Strength',
           'Stamina', 'Fouling']
POTS = ['InsideScoring', 'JumpShot', 'FtShot', '3pShot', 'Handling', 'Passing', 'OReb', 'DReb',
        'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking']


def make_user(conn):
    uid = str(uuid.uuid4())
    conn.execute("insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s)",
                 (uid, f"{uid}@x", '{"full_name": "Tester"}'))
    conn.execute("insert into public.profiles (id, display_name) values (%s, 'Tester') on conflict do nothing", (uid,))
    return uid


def make_character(conn, owner, league, ratings=None, potentials=None, points=500, bias=None):
    r = {k: 45 for k in RATINGS}
    r.update(ratings or {})
    p = {k: 90 for k in POTS}
    p.update(potentials or {})
    # created with an ordinary sheet (the creation guard caps a new player at 100), then given
    # the sheet under test directly, as the commissioner's writes would
    base_r, base_p = {k: 45 for k in RATINGS}, {k: 90 for k in POTS}
    cid = conn.execute(
        """insert into public.characters (owner, first_name, last_name, "position", height_inches, archetype,
                                          ratings, potentials, league)
           values (%s, 'Test', %s, 'SF', 78, 'wing', %s, %s, %s) returning id""",
        (owner, uuid.uuid4().hex[:8], psycopg.types.json.Jsonb(base_r), psycopg.types.json.Jsonb(base_p),
         league)).fetchone()[0]
    conn.execute("update public.characters set ratings = %s, potentials = %s, growth_bias = %s, "
                 "points_available = %s, status = 'active' where id = %s",
                 (psycopg.types.json.Jsonb(r), psycopg.types.json.Jsonb(p), psycopg.types.json.Jsonb(bias or {}),
                  points, cid))
    return str(cid)


def as_user(conn, uid):
    conn.execute("set role authenticated")
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid,))


def as_admin(conn):
    conn.execute("reset role")
    conn.execute("select set_config('request.jwt.claim.sub', '', false)")


def request(conn, uid, cid, rating, delta, kind="rating"):
    """Insert as the owner, like the website. Returns (row or None, error text)."""
    as_user(conn, uid)
    try:
        row = conn.execute(
            "insert into public.upgrade_requests (character_id, rating, delta, kind) values (%s, %s, %s, %s) "
            "returning id, cost, status", (cid, rating, delta, kind)).fetchone()
        return row, ""
    except Exception as exc:                                          # noqa: BLE001
        return None, str(exc).splitlines()[0]
    finally:
        as_admin(conn)


def apply_all(conn, cid):
    ids = [r[0] for r in conn.execute(
        "update public.upgrade_requests set status = 'approved' where character_id = %s and status = 'pending' "
        "returning id", (cid,)).fetchall()]
    conn.execute("select * from public.apply_upgrade_requests(%s::uuid[])", (ids,))
    return conn.execute("select ratings, potentials, growth_bias, nodes, points_available, cap_breakers "
                        "from public.characters where id = %s", (cid,)).fetchone()


def seed_nodes(conn):
    conn.execute("""
      insert into public.tree_nodes (id, branch, tier, name, cost, stage, requires, min_spent, group_key, effects) values
        ('scoring-1', 'Scoring', 1, 'Shooting Form', 15, 'prep', '{}', 20, null,
         '{"potentials": {"InsideScoring": 3, "JumpShot": 3, "FtShot": 3, "3pShot": 3}}'),
        ('scoring-2', 'Scoring', 2, 'Scorer''s Touch', 30, 'college', '{scoring-1}', 60, null,
         '{"potentials": {"InsideScoring": 3, "JumpShot": 3, "FtShot": 3, "3pShot": 3},
           "bias": {"InsideScoring": -10, "JumpShot": -10, "FtShot": -10, "3pShot": -10}}'),
        ('sig-a', 'Scoring', 4, 'Deadeye', 60, 'prep', '{}', 0, 'signature',
         '{"ratings": {"3pShot": 6, "JumpShot": 4}, "potentials": {"3pShot": 5, "JumpShot": 5}, "tendency": {"3pUsage": 10}}'),
        ('sig-b', 'Scoring', 4, 'Paint Beast', 60, 'prep', '{}', 0, 'signature',
         '{"ratings": {"InsideScoring": 6, "Strength": 4}}')
      on conflict (id) do nothing""")


def main():
    try:
        admin = psycopg.connect(DSN, autocommit=True, connect_timeout=5)
    except Exception as exc:                                          # noqa: BLE001
        print(f"SKIP  no Postgres at {DSN}: {exc}")
        return 0

    # ------------------------------------------------------------------ the upgrade path
    db = fresh_db(admin, "cv_tree_upgrade")
    check("stubs for auth and the Supabase roles load", run_sql(db, STUBS, "stubs"))
    live_ok = all(run_sql(db, old_file(name), name) for name in LIVE_ORDER)
    check("the live project's SQL, in the order it was pasted, loads", live_ok)
    tree_sql = (ROOT / "supabase" / "skill_tree.sql").read_text(encoding="utf-8")
    check("skill_tree.sql runs on top of it", run_sql(db, tree_sql, "skill_tree.sql (1st)"))
    check("skill_tree.sql runs a second time (idempotent)", run_sql(db, tree_sql, "skill_tree.sql (2nd)"))

    cons = {r[0]: r[1] for r in db.execute(
        "select conname, pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = 'public.upgrade_requests'::regclass").fetchall()}
    kind_checks = [d for d in cons.values() if "kind" in d]
    check("exactly one kind check on upgrade_requests, and it allows node and breaker",
          len(kind_checks) == 1 and "node" in kind_checks[0] and "breaker" in kind_checks[0], str(kind_checks))
    delta_checks = [d for d in cons.values() if "delta" in d]
    check("exactly one delta check, up to 150", len(delta_checks) == 1 and "150" in delta_checks[0], str(delta_checks))

    db.execute("update public.settings set value = '999'::jsonb where key = 'max_characters'")
    uid, other = make_user(db), make_user(db)
    seed_nodes(db)

    # ------------------------------------------------------------------ switch OFF: nothing changed
    cost = lambda f, s, k='rating': db.execute("select public.cv_upgrade_cost(%s, %s, %s)", (f, s, k)).fetchone()[0]
    check("off: the old curve (48 -> 52 costs 6)", cost(48, 4) == 6)
    check("off: the old top band (85 and up is 5)", cost(85, 1) == 5 and cost(120, 1) == 5)
    check("off: a ceiling step is double", cost(48, 4, 'potential') == 12)
    prep = make_character(db, uid, 'prep', ratings={'3pShot': 69}, potentials={'3pShot': 95})
    row, err = request(db, uid, prep, '3pShot', 5)
    check("off: no stage cap (prep 69 -> 74 allowed)", row is not None, err)
    row, err = request(db, uid, prep, 'Handling', 41)
    check("off: a 41-step request is no longer refused by the old 40 limit", row is not None, err)
    biased = make_character(db, uid, 'college', ratings={'Passing': 60}, bias={'Passing': 60})
    row, err = request(db, uid, biased, 'Passing', 1)
    check("off: the bias clamps at 85 as before (2 -> 2)", row is not None and row[1] == 2, f"{row} {err}")
    row, err = request(db, uid, prep, 'scoring-1', 1, 'node')
    check("off: a node is refused", row is None and "not open" in err, err)
    row, err = request(db, uid, prep, 'JumpShot', 1, 'breaker')
    check("off: a Cap Breaker is refused", row is None and "not open" in err, err)

    # ------------------------------------------------------------------ switch ON
    db.execute("update public.settings set value = 'true'::jsonb where key = 'skill_tree_enabled'")
    db.execute("insert into public.settings (key, value) values ('tree_opened_at', to_jsonb(now())) "
               "on conflict (key) do update set value = excluded.value")
    step = lambda v: db.execute("select public.cv_step_cost(%s)", (v,)).fetchone()[0]
    expected = {49: 1, 50: 2, 59: 2, 60: 3, 69: 3, 70: 5, 79: 5, 80: 8, 84: 8, 85: 12, 89: 12,
                90: 18, 94: 18, 95: 25, 99: 25, 100: 35, 140: 35}
    got = {v: step(v) for v in expected}
    check("on: every price band", got == expected, str({v: (got[v], e) for v, e in expected.items() if got[v] != e}))
    check("on: 48 -> 52 costs 6, a ceiling step is triple", cost(48, 4) == 6 and cost(48, 4, 'potential') == 18)

    prep2 = make_character(db, uid, 'prep', ratings={'3pShot': 68, 'Stamina': 70}, potentials={'3pShot': 95})
    row, err = request(db, uid, prep2, '3pShot', 2)
    check("on: prep may buy up to 70", row is not None, err)
    row, err = request(db, uid, prep2, '3pShot', 1)
    check("on: prep may not buy past 70", row is None and "past 70" in err, err)
    col = make_character(db, uid, 'college', ratings={'DReb': 84}, potentials={'DReb': 99})
    row, err = request(db, uid, col, 'DReb', 1)
    check("on: college may buy up to 85", row is not None, err)
    row, err = request(db, uid, col, 'DReb', 1)
    check("on: college may not buy past 85", row is None and "past 85" in err, err)
    pro = make_character(db, uid, 'pro', ratings={'Blocking': 110}, potentials={'Blocking': 115})
    row, err = request(db, uid, pro, 'Blocking', 2)
    check("on: a pro buys past 100 at 35 a step", row is not None and row[1] == 70, f"{row} {err}")

    room = make_character(db, uid, 'pro', ratings={'Passing': 60}, potentials={'Passing': 68})
    row, err = request(db, uid, room, 'Passing', 2, 'potential')
    check("on: a ceiling may be bought to 10 over the rating", row is not None, err)
    row, err = request(db, uid, room, 'Passing', 1, 'potential')
    check("on: but not 11 over", row is None and "above its rating" in err, err)

    cheap = make_character(db, uid, 'college', ratings={'Passing': 60}, bias={'Passing': 40})
    row, err = request(db, uid, cheap, 'Passing', 2)
    check("on: the bias now clamps at 50 (6 -> 3)", row is not None and row[1] == 3, f"{row} {err}")

    # nodes
    kid = make_character(db, uid, 'prep', ratings={'JumpShot': 50}, potentials={'JumpShot': 80})
    row, err = request(db, uid, kid, 'scoring-1', 1, 'node')
    check("on: a node needs its branch spend", row is None and "points spent on Scoring" in err, err)
    request(db, uid, kid, 'JumpShot', 10)               # 50..59 at 2 a step = 20
    row, err = request(db, uid, kid, 'scoring-1', 1, 'node')
    check("on: queued spend counts toward it, and it costs 15", row is not None and row[1] == 15, f"{row} {err}")
    row, err = request(db, uid, kid, 'scoring-1', 1, 'node')
    check("on: the same node twice is refused", row is None and "already" in err, err)
    row, err = request(db, uid, kid, 'scoring-2', 1, 'node')
    check("on: a college node is refused in prep", row is None and "opens in college" in err, err)
    row, err = request(db, uid, kid, 'sig-a', 2, 'node')
    check("on: a node's delta must be 1", row is None, err)
    row, err = request(db, uid, kid, 'sig-a', 1, 'node')
    check("on: a Signature", row is not None and row[1] == 60, f"{row} {err}")
    row, err = request(db, uid, kid, 'sig-b', 1, 'node')
    check("on: a second Signature is refused", row is None and "only one signature" in err, err)
    row, err = request(db, other, kid, 'JumpShot', 1)
    check("on: nobody else can spend his points", row is None and "not your character" in err, err)

    before = db.execute("select points_available from public.characters where id = %s", (kid,)).fetchone()[0]
    r, p, b, nodes, pts, _ = apply_all(db, kid)
    check("apply: both nodes recorded, in order", nodes == ['scoring-1', 'sig-a'], str(nodes))
    check("apply: points charged 20 + 15 + 60", before - pts == 95, f"{before} -> {pts}")
    check("apply: ratings moved (JumpShot 50 +10 +4, 3pShot +6, 3pUsage +10)",
          r['JumpShot'] == 64 and r['3pShot'] == 51 and r['3pUsage'] == 55, str({k: r[k] for k in ('JumpShot', '3pShot', '3pUsage')}))
    check("apply: ceilings moved (JumpShot 80 +3 +5, 3pShot 90 +3 +5, Inside 90 +3)",
          p['JumpShot'] == 88 and p['3pShot'] == 98 and p['InsideScoring'] == 93, str(p))
    ledger = [x[0] for x in db.execute("select reason from public.point_ledger where character_id = %s order by created_at", (kid,)).fetchall()]
    check("apply: the ledger names the nodes", any("Shooting Form" in x for x in ledger) and any("Deadeye" in x for x in ledger), str(ledger))
    total = db.execute("select coalesce(sum(amount),0) from public.point_ledger where character_id = %s and amount < 0", (kid,)).fetchone()[0]
    check("apply: the ledger's spends add up to what was charged", -total == 95, str(total))

    col2 = make_character(db, uid, 'college', ratings={'JumpShot': 50}, potentials={'JumpShot': 80})
    row, err = request(db, uid, col2, 'scoring-2', 1, 'node')
    check("on: tier 2 needs tier 1 first", row is None and "needs Shooting Form first" in err, err)
    request(db, uid, col2, 'JumpShot', 20)             # 50..69: 10x2 + 10x3 = 50
    request(db, uid, col2, 'FtShot', 5)                 # 45..49: 5
    request(db, uid, col2, 'InsideScoring', 5)          # 5 -> 60 in the branch
    row, err = request(db, uid, col2, 'scoring-1', 1, 'node')
    ok1 = row is not None
    row, err = request(db, uid, col2, 'scoring-2', 1, 'node')
    check("on: tier 2 with tier 1 queued and 60 spent", ok1 and row is not None and row[1] == 30, f"{row} {err}")
    r, p, b, nodes, pts, _ = apply_all(db, col2)
    check("apply: tier 2's discount lands in growth_bias", b.get('JumpShot') == 90 and b.get('3pShot') == 90, str(b))
    row, err = request(db, uid, col2, 'JumpShot', 1)
    check("on: and prices the next step 10% cheaper (JumpShot 70: 5 -> 5*0.9 = 4.5 -> 5)", row is not None and row[1] == 5, f"{row} {err}")

    # Cap Breakers
    brk = make_character(db, uid, 'pro', ratings={'DReb': 80}, potentials={'DReb': 85})
    row, err = request(db, uid, brk, 'DReb', 1, 'breaker')
    check("breaker: refused with none to spend", row is None and "no Cap Breakers" in err, err)
    db.execute("update public.characters set cap_breakers = 4 where id = %s", (brk,))
    rows = [request(db, uid, brk, 'DReb', 1, 'breaker') for _ in range(4)]
    check("breaker: three on one rating, free", all(r[0] is not None and r[0][1] == 0 for r in rows[:3]), str(rows[:3]))
    check("breaker: never a fourth on the same rating", rows[3][0] is None and "all 3" in rows[3][1], rows[3][1])
    row, err = request(db, uid, brk, 'Stamina', 1, 'breaker')
    check("breaker: only on a rating that has a ceiling", row is None and "no ceiling" in err, err)
    r, p, b, nodes, pts, left = apply_all(db, brk)
    check("breaker apply: DReb ceiling 85 + 9, one breaker left", p['DReb'] == 94 and left == 1, f"{p['DReb']} {left}")

    # what a player can and cannot touch
    as_user(db, uid)
    try:
        db.execute("update public.characters set cap_breakers = 99 where id = %s", (brk,))
        wrote = db.execute("select cap_breakers from public.characters where id = %s", (brk,)).fetchone()[0] == 99
    except Exception:                                                  # noqa: BLE001
        wrote = False
    as_admin(db)
    check("a player cannot hand himself Cap Breakers", not wrote)
    as_user(db, uid)
    try:
        db.execute("insert into public.tree_nodes (id, branch, tier, name, cost, stage) values ('x', 'Scoring', 1, 'x', 0, 'prep')")
        wrote = True
    except Exception:                                                  # noqa: BLE001
        wrote = False
    as_admin(db)
    check("a player cannot add a free node", not wrote)
    db.execute("set role anon")
    try:
        n = db.execute("select count(*) from public.tree_nodes").fetchone()[0]
        nodes_visible = db.execute("select nodes, cap_breakers from public.characters limit 1").fetchone() is not None
    except Exception as exc:                                           # noqa: BLE001
        n, nodes_visible = -1, False
        print("     ", exc)
    as_admin(db)
    check("anyone can read the tree and who owns what", n >= 4 and nodes_visible, f"{n} {nodes_visible}")

    # tuned values survive a re-run
    db.execute("update public.settings set value = '[[999, 7]]'::jsonb where key = 'price_bands'")
    run_sql(db, tree_sql, "skill_tree.sql (3rd)")
    check("a re-run never clobbers a tuned price list", step(40) == 7)

    # off again is the rollback
    db.execute("update public.settings set value = 'false'::jsonb where key = 'skill_tree_enabled'")
    check("switching off restores the old curve at once", cost(48, 4) == 6 and cost(85, 1) == 5 and step(40) == 1)
    db.close()

    # ------------------------------------------------------------------ a fresh install
    fresh = fresh_db(admin, "cv_tree_fresh")
    run_sql(fresh, STUBS, "stubs")
    schema = (ROOT / "supabase" / "schema.sql").read_text(encoding="utf-8")
    check("a fresh install from schema.sql alone works", run_sql(fresh, schema, "schema.sql"))
    has = fresh.execute("select count(*) from information_schema.columns where table_name = 'characters' "
                        "and column_name in ('nodes', 'cap_breakers')").fetchone()[0]
    check("and already has the tree's columns", has == 2, str(has))
    check("and skill_tree.sql on top of it changes nothing that breaks", run_sql(fresh, tree_sql, "skill_tree.sql on fresh"))
    fresh.close()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("the skill tree SQL holds up on the upgrade path and on a fresh install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
