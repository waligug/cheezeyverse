"""The browser's archetype matching must agree with the python that built the data.

TWO IMPLEMENTATIONS OF ONE FORMULA. `tools/nba_archetypes.py` computes each NBA player's shape
and writes it into `site/data/nba-archetypes.json`; `site/js/archetypes.js` computes a
CHARACTER's shape in the browser and compares it against those stored vectors. If the two centre
or scale differently, the comparison is between two things that are not the same kind of number -
and the answer still looks perfectly plausible, because every output of cosine similarity is a
number between -1 and 1 with a famous name attached.

So this runs the real JS - through node, against the real 579-player file - and requires it to
produce what the python produces, to four decimal places, on the same inputs.

It also pins the two properties the UI leans on:

  * A PLAYER MATCHES HIMSELF at 1.0. If the field ORDER in the JS ever drifts from the JSON's,
    this is what catches it: every similarity stays in range and only this goes wrong.
  * TIES BREAK THE SAME WAY. Two players can sit at identical similarity to four decimals, and
    "you play like" changing between two reloads of one character reads as a broken page.

    python tests/test_archetypes.py
"""
from __future__ import annotations

import csv
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "site" / "data" / "nba-archetypes.json"
JS = ROOT / "site" / "js" / "archetypes.js"


def main():
    if not DATA.exists():
        print("SKIP archetypes: site/data/nba-archetypes.json has not been generated")
        return 0
    if not shutil.which("node"):
        print("SKIP archetypes: node is not on PATH")
        return 0

    data = json.loads(DATA.read_text(encoding="utf-8"))
    players = data["players"]
    fields = data["fields"]

    # ---- the JS field order must equal the JSON's, or every comparison is scrambled --------
    js = JS.read_text(encoding="utf-8")
    block = js.split("SHAPE_FIELDS = [", 1)[1].split("]", 1)[0]
    js_fields = [w.strip().strip("'\",") for w in block.replace("\n", " ").split(",")]
    js_fields = [w for w in js_fields if w]
    assert js_fields == fields, \
        f"JS field order differs from the data's:\n  js  {js_fields}\n  json {fields}"

    # ---- build inputs whose right answer is known: each player's own ratings ---------------
    # The stored `shape` is already centred and scaled, so feeding it back in must return the
    # same shape and therefore match that player at 1.0.
    sample = players[:40]
    probes = [{f: p["shape"][i] for i, f in enumerate(fields)} for p in sample]

    # A Windows absolute path is not a valid ESM specifier - node wants a file:// URL - and
    # `require` does not exist inside a module, so the JSON is read with node:fs instead.
    script = f"""
import {{ readFileSync }} from 'node:fs';
import {{ shapeOf, similarity, matchArchetypes }} from {json.dumps(JS.resolve().as_uri())};
const data = JSON.parse(readFileSync({json.dumps(str(DATA))}, 'utf8'));
const probes = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = probes.map((r) => {{
  const best = matchArchetypes(r, data, 3);
  return {{ shape: shapeOf(r), top: best.map((b) => [b.name, Number(b.score.toFixed(4))]) }};
}});
console.log(JSON.stringify(out));
"""
    tmp = Path(tempfile.mkdtemp(prefix="arch-"))
    try:
        (tmp / "run.mjs").write_text(script, encoding="utf-8")
        (tmp / "probes.json").write_text(json.dumps(probes), encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), str(tmp / "run.mjs"),
                               str(tmp / "probes.json")],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"node failed: {proc.stderr[:400]}"
        got = json.loads(proc.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    assert len(got) == len(sample), (len(got), len(sample))

    # ---- a player matches himself, and he is first -----------------------------------------
    misses = []
    for player, result in zip(sample, got):
        name, score = result["top"][0]
        if name != player["name"] or score < 0.9999:
            misses.append((player["name"], name, score))
    assert not misses, f"players who did not match themselves first: {misses[:5]}"

    # ---- and the python agrees, on the same inputs ------------------------------------------
    def py_similarity(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    def py_shape(ratings):
        values = [float(ratings.get(k) or 0) for k in fields]
        mean = sum(values) / len(values)
        spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        return None if spread < 1e-6 else [round((v - mean) / spread, 4) for v in values]

    for probe, result in zip(probes, got):
        mine = py_shape(probe)
        ranked = sorted(((py_similarity(mine, p["shape"]), p["name"]) for p in players),
                        key=lambda r: (-r[0], r[1]))[:3]
        py_top = [[n, round(s, 4)] for s, n in ranked]
        js_top = [[n, s] for n, s in result["top"]]
        assert py_top == js_top, f"python and JS disagree:\n  py {py_top}\n  js {js_top}"

    # ---- a flat sheet has no shape and must not be guessed at --------------------------------
    flat = {f: 50 for f in fields}
    assert py_shape(flat) is None, "a player with no spread should have no shape"

    print(f"OK  archetypes: JS and python agree on {len(sample)} probes against "
          f"{len(players)} NBA players, and every one matches himself first")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
