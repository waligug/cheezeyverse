"""The price the browser quotes must be the price the database charges.

`site/js/tree.js` shows somebody what a point costs before he spends it (the page's
treeRules() reading the live settings - the skill tree's price bands when the tree is open,
the old curve when it is not), and `supabase/schema.sql` prices it again server-side in a
trigger - deliberately, so the browser cannot name its own price. Two implementations of one curve is exactly the shape of every bug
found on 2026-09-17: the website and the save file disagreed about how to key a potential, and
nothing noticed until a character came out ruined.

A mismatch here is worse than a crash, because it does not look like one. Somebody is quoted
2 points, charged 3, and just quietly runs out sooner than the page said he would.

Needs Supabase configured (it asks the real database) and node on PATH.

    python tests/test_price_parity.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store  # noqa: E402

SITE_JS = ROOT / "site" / "js"

# The band edges, and both sides of each, because an off-by-one at a boundary is the only way
# this realistically breaks - the old curve's (50/70/85) and the skill tree's (50/60/70/80/85/
# 90/95/100), with values past 100 because the tree prices those too.
FROMS = [0, 10, 30, 45, 49, 50, 51, 59, 60, 61, 68, 69, 70, 71, 79, 80, 81, 84, 85, 86, 89, 90,
         94, 95, 99, 100, 101, 120, 140]
STEPS = [1, 2, 3, 5, 10]

# tree.js imports three shared modules; only its pure functions run here, so two are stubbed.
STUBS = {
    "supabase.mjs": "export function client() { throw new Error('no network in this test'); }\n",
    "ui.mjs": "export function el() { return null; }\nexport function clear() {}\n",
}

SCRIPT = """
import {{ treeRules, upgradeCost }} from './tree.mjs';
const rules = treeRules({settings});
const out = [];
for (const kind of ['rating', 'potential'])
  for (const from of {froms})
    for (const steps of {steps})
      out.push([kind, from, steps, upgradeCost(rules, from, steps, kind)]);
console.log(JSON.stringify({{ on: rules.on, rows: out }}));
"""


def browser_prices(settings):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tree = (SITE_JS / "tree.js").read_text(encoding="utf-8")
        tree = (tree.replace("from './supabase.js'", "from './supabase.mjs'")
                    .replace("from './rules.js'", "from './rules.mjs'")
                    .replace("from './ui.js'", "from './ui.mjs'"))
        (tmp / "tree.mjs").write_text(tree, encoding="utf-8")
        (tmp / "rules.mjs").write_text((SITE_JS / "rules.js").read_text(encoding="utf-8"), encoding="utf-8")
        for name, body in STUBS.items():
            (tmp / name).write_text(body, encoding="utf-8")
        js = tmp / "cost.mjs"
        js.write_text(SCRIPT.format(settings=json.dumps(settings, default=str), froms=json.dumps(FROMS),
                                    steps=json.dumps(STEPS)), encoding="utf-8")
        out = subprocess.run(["node", str(js)], capture_output=True, text=True, cwd=tmp)
        if out.returncode:
            sys.exit(f"could not price anything with tree.js:\n{out.stderr}")
        return json.loads(out.stdout)


def main():
    got = browser_prices(store.get_settings())
    rows = got["rows"]
    print(f"skill tree {'OPEN' if got['on'] else 'closed'}: the page prices with "
          f"{'settings.price_bands' if got['on'] else 'the old curve'}")
    bad = []
    for kind, frm, steps, js in rows:
        sql = int(store._rpc("cv_upgrade_cost",
                             {"p_from": frm, "p_steps": steps, "p_kind": kind}))
        if sql != int(js):
            bad.append(f"{kind} from={frm} steps={steps}: browser quotes {js}, database charges {sql}")

    print(f"{len(rows)} price points checked against the live database")
    for row in bad:
        print(f"  MISMATCH  {row}")
    if bad:
        print(f"\n{len(bad)} mismatch(es) - somebody is being charged a price he was not shown")
        return 1
    print("the browser and the database agree on every price")
    return 0


if __name__ == "__main__":
    sys.exit(main())
