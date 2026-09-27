"""site/js/tree.js - the page's copy of the tree's rules - against the database's.

Runs tree.js in node with its three imports stubbed (only pure functions are exercised), and:
  * prices every value 0..150, one step and several, rating and ceiling, across growth biases,
    and compares with cv_upgrade_cost + the guard's bias arithmetic in a real Postgres
    (CV_PG_DSN, the database tests/test_skill_tree_sql.py builds) - with the tree ON and OFF;
  * checks the send order never files a request the guard would refuse;
  * checks the node states say what the guard would say.

    python tests/test_tree_js.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "tmp" / "tree-js-test"

failures = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


STUB_SUPABASE = "export function client() { throw new Error('no network in tests'); }\n"
STUB_UI = ("export function el() { return null; }\n"
           "export function clear() {}\n")

HARNESS = r"""
import * as T from './tree.mjs';
const input = JSON.parse(await new Promise((ok) => { let s = ''; process.stdin.on('data', (d) => s += d); process.stdin.on('end', () => ok(s)); }));
const out = {};
for (const [name, cfg] of Object.entries(input.configs)) {
  const rules = T.treeRules(cfg);
  out[name] = input.cases.map(([from, steps, kind, bias]) => T.biasedCost(rules, from, steps, kind, bias));
}
const on = T.treeRules({ skill_tree_enabled: true });
out.order = input.orders.map(([base, draft]) => T.sendOrder(on, base, draft, input.ratings, input.pots));
out.states = input.states.map(([character, node, requests]) =>
  T.nodeState(character, node, requests, { ...on, openedAt: '2026-01-01T00:00:00Z' }, input.nodes));
out.spent = T.branchSpent(input.spentRequests, 'Scoring', '2026-01-01T00:00:00Z');
out.describe = T.describePrices(on);
process.stdout.write(JSON.stringify(out));
"""


def build():
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    src = (ROOT / "site" / "js" / "tree.js").read_text(encoding="utf-8")
    src = (src.replace("from './supabase.js'", "from './supabase.mjs'")
              .replace("from './rules.js'", "from './rules.mjs'")
              .replace("from './ui.js'", "from './ui.mjs'"))
    (TMP / "tree.mjs").write_text(src, encoding="utf-8")
    (TMP / "supabase.mjs").write_text(STUB_SUPABASE, encoding="utf-8")
    (TMP / "ui.mjs").write_text(STUB_UI, encoding="utf-8")
    rules = (ROOT / "site" / "js" / "rules.js").read_text(encoding="utf-8")
    (TMP / "rules.mjs").write_text(rules, encoding="utf-8")
    (TMP / "harness.mjs").write_text(HARNESS, encoding="utf-8")


def run(payload):
    proc = subprocess.run(["node", str(TMP / "harness.mjs")], input=json.dumps(payload), text=True,
                          capture_output=True, encoding="utf-8", cwd=TMP)
    if proc.returncode:
        raise SystemExit(proc.stderr)
    return json.loads(proc.stdout)


RATINGS = ['InsideScoring', 'JumpShot', 'FtShot', '3pUsage', '3pShot', 'Handling', 'Passing', 'Quickness',
           'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking', 'OReb', 'DReb', 'Jumping', 'Strength',
           'Stamina', 'Fouling']
POTS = ['InsideScoring', 'JumpShot', 'FtShot', '3pShot', 'Handling', 'Passing', 'OReb', 'DReb',
        'PostDefense', 'PerimeterDefense', 'Stealing', 'Blocking']


def sql_prices(cases):
    dsn = os.environ.get("CV_PG_DSN")
    try:
        import psycopg
    except ImportError:
        return None, "psycopg is not installed"
    if not dsn:
        return None, "CV_PG_DSN is not set"
    try:
        conn = psycopg.connect(dsn.rsplit("/", 1)[0] + "/cv_tree_upgrade", autocommit=True, connect_timeout=5)
    except Exception as exc:                                              # noqa: BLE001
        return None, f"run tests/test_skill_tree_sql.py first ({exc})"
    out = {}
    for name, on in (("off", False), ("on", True)):
        conn.execute("update public.settings set value = %s::jsonb where key = 'skill_tree_enabled'",
                     (json.dumps(on),))
        conn.execute("update public.settings set value = '[[50,1],[60,2],[70,3],[80,5],[85,8],[90,12],[95,18],[100,25],[999,35]]'::jsonb "
                     "where key = 'price_bands'")
        lo, hi = (50, 200) if on else (85, 115)
        got = []
        for frm, steps, kind, bias in cases:
            c = conn.execute("select public.cv_upgrade_cost(%s, %s, %s)", (frm, steps, kind)).fetchone()[0]
            b = min(hi, max(lo, bias))
            got.append(conn.execute("select greatest(1, round(%s::int * %s::int / 100.0))::int", (c, b)).fetchone()[0])
        out[name] = got
    conn.close()
    return out, ""


def main():
    build()
    cases = [(v, s, k, b) for v in range(0, 151, 1) for s in (1, 4) for k in ("rating", "potential")
             for b in (40, 85, 100, 115, 180)]
    nodes = [
        {"id": "scoring-1", "branch": "Scoring", "tier": 1, "name": "Shooting Form", "stage": "prep", "requires": [],
         "min_spent": 20, "min_ratings": {}, "group_key": None},
        {"id": "scoring-2", "branch": "Scoring", "tier": 2, "name": "Scorer's Touch", "stage": "college",
         "requires": ["scoring-1"], "min_spent": 60, "min_ratings": {}, "group_key": None},
        {"id": "sig-a", "branch": "Scoring", "tier": 4, "name": "Deadeye", "stage": "pro", "requires": [],
         "min_spent": 0, "min_ratings": {}, "group_key": "signature"},
        {"id": "sig-b", "branch": "Scoring", "tier": 4, "name": "Paint Beast", "stage": "pro", "requires": [],
         "min_spent": 0, "min_ratings": {}, "group_key": "signature"},
    ]
    spent = [{"kind": "rating", "rating": "JumpShot", "cost": 12, "status": "applied", "requested_at": "2026-02-01T00:00:00Z"},
             {"kind": "potential", "rating": "3pShot", "cost": 9, "status": "pending", "requested_at": "2026-02-02T00:00:00Z"},
             {"kind": "rating", "rating": "3pShot", "cost": 50, "status": "applied", "requested_at": "2025-06-01T00:00:00Z"},
             {"kind": "rating", "rating": "Passing", "cost": 7, "status": "applied", "requested_at": "2026-02-01T00:00:00Z"},
             {"kind": "rating", "rating": "InsideScoring", "cost": 4, "status": "rejected", "requested_at": "2026-02-01T00:00:00Z"}]
    prep = {"league": "prep", "nodes": [], "ratings": {}}
    pro_owner = {"league": "pro", "nodes": ["sig-a"], "ratings": {}}
    states = [
        [prep, nodes[0], []],
        [prep, nodes[0], spent],
        [prep, nodes[1], spent],
        [pro_owner, nodes[3], spent],
        [pro_owner, nodes[2], spent],
    ]
    base = {"ratings": {k: 60 for k in RATINGS}, "potentials": {k: 66 for k in POTS}}
    orders = [
        [base, {"ratings": {**base["ratings"], "DReb": 80}, "potentials": {**base["potentials"], "DReb": 80}}],
        [base, {"ratings": {**base["ratings"], "Stamina": 75}, "potentials": dict(base["potentials"])}],
        [base, {"ratings": {**base["ratings"], "JumpShot": 66}, "potentials": {**base["potentials"], "JumpShot": 70}}],
    ]
    out = run({"configs": {"off": {}, "on": {"skill_tree_enabled": True}}, "cases": cases, "nodes": nodes,
               "states": states, "orders": orders, "ratings": RATINGS, "pots": POTS, "spentRequests": spent})

    sql, why = sql_prices(cases)
    if sql is None:
        print(f"SKIP  prices against the database: {why}")
    else:
        for name in ("off", "on"):
            bad = [(c, j, q) for c, j, q in zip(cases, out[name], sql[name]) if j != q]
            check(f"tree {name}: {len(cases)} prices identical to cv_upgrade_cost and the guard's bias",
                  not bad, str(bad[:5]))

    # the send order: every request legal when it lands
    for i, res in enumerate(out["order"]):
        cur = {"r": dict(orders[i][0]["ratings"]), "p": dict(orders[i][0]["potentials"])}
        legal = True
        for step in res["order"]:
            k, d = step["rating"], step["delta"]
            if step["kind"] == "potential":
                if cur["p"][k] + d > cur["r"][k] + 10:
                    legal = False
                cur["p"][k] += d
            else:
                if k in cur["p"] and cur["r"][k] + d > cur["p"][k]:
                    legal = False
                cur["r"][k] += d
        target = orders[i][1]
        reached = all(cur["r"][k] == target["ratings"][k] for k in RATINGS) and \
            all(cur["p"][k] == target["potentials"][k] for k in POTS)
        check(f"send order {i + 1}: every request legal when filed, and the draft reached",
              legal and reached and not res["stuck"], json.dumps(res))
    check("send order: a ceiling far over the rating is interleaved (DReb 66 -> 80 with the rating)",
          len([s for s in out["order"][0]["order"] if s["rating"] == "DReb"]) > 2, json.dumps(out["order"][0]))

    s = out["states"]
    check("node: needs the branch spend", s[0]["state"] == "locked" and "20 points spent on Scoring (0 so far)" in s[0]["why"], str(s[0]))
    check("node: open once spent (only since the tree opened, rejected ones not counted)",
          s[1]["state"] == "open", str(s[1]))
    check("node: a college node in prep, and its prerequisite", s[2]["state"] == "locked"
          and "Opens in college" in s[2]["why"] and "Needs Shooting Form first" in s[2]["why"], str(s[2]))
    check("node: a second Signature", s[3]["state"] == "locked" and "One Signature per player" in s[3]["why"], str(s[3]))
    check("node: one he owns", s[4]["state"] == "owned", str(s[4]))
    check("branch spend: 12 + 9 since the tree opened, in Scoring only", out["spent"] == 21, str(out["spent"]))
    check("the price line reads plainly", out["describe"].startswith("under 50: 1 · 50-59: 2") and "100 and up: 35" in out["describe"],
          out["describe"])

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("tree.js prices, orders and explains exactly what the database does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
