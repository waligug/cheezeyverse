"""The price the browser quotes must be the price the database charges.

`site/js/rules.js` shows somebody what a point costs before he spends it, and
`supabase/schema.sql` prices it again server-side in a trigger - deliberately, so the browser
cannot name its own price. Two implementations of one curve is exactly the shape of every bug
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

RULES = (ROOT / "site" / "js" / "rules.js").as_uri()

# The band edges, and both sides of each, because an off-by-one at a boundary is the only way
# this realistically breaks: under 50 / 50-69 / 70-84 / 85 and up, potentials doubled.
FROMS = [0, 10, 30, 45, 49, 50, 51, 60, 68, 69, 70, 71, 80, 84, 85, 86, 90, 95, 99]
STEPS = [1, 2, 3, 5, 10]

SCRIPT = """
import {{ upgradeCost }} from '{rules}';
const out = [];
for (const kind of ['rating', 'potential'])
  for (const from of {froms})
    for (const steps of {steps})
      out.push([kind, from, steps, upgradeCost(from, steps, kind)]);
console.log(JSON.stringify(out));
"""


def browser_prices():
    with tempfile.TemporaryDirectory() as tmp:
        js = Path(tmp) / "cost.mjs"
        js.write_text(SCRIPT.format(rules=RULES, froms=json.dumps(FROMS),
                                    steps=json.dumps(STEPS)), encoding="utf-8")
        out = subprocess.run(["node", str(js)], capture_output=True, text=True)
        if out.returncode:
            sys.exit(f"could not read the cost curve out of rules.js:\n{out.stderr}")
        return json.loads(out.stdout)


def main():
    rows = browser_prices()
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
