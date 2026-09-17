"""Cost-curve and archetype regression tests for site/js/rules.js.

Run: python tests/test_rules.py

These tests do NOT reimplement the rules. Node is available in this environment
(v24.14.0), so the real `site/js/rules.js` is imported and executed, and every number
asserted below comes back from the browser's own code. If node ever disappears the run
reports SKIP rather than quietly passing.

How it works: rules.js is an ES module but lives as `.js` in a folder with no
package.json, so node would treat it as CommonJS. The harness copies it to
`tmp/rules-test/rules.mjs` (tmp/ is gitignored), writes a small dispatcher beside it, and
talks to it with one JSON file in and one JSON blob out. Windows needs no file:// dance
that way, because the import is a plain relative specifier.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_JS = ROOT / "site/js/rules.js"
SCHEMA_SQL = ROOT / "supabase/schema.sql"
WORK = ROOT / "tmp/rules-test"

failures = []
skipped = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        failures.append(name)


HARNESS = """
import * as R from './rules.mjs';

const ops = {
  constants: () => ({
    RATINGS: R.RATINGS,
    POTENTIAL_RATINGS: R.POTENTIAL_RATINGS,
    POSITIONS: R.POSITIONS,
    ARCHETYPE_KEYS: R.ARCHETYPE_KEYS,
    LOCKED_RATINGS: R.LOCKED_RATINGS,
    CAP_DEFAULT: R.CAP_DEFAULT,
    POTENTIAL_MULTIPLIER: R.POTENTIAL_MULTIPLIER,
    RATING_MAX: R.RATING_MAX,
    HEIGHT_RANGES: R.HEIGHT_RANGES,
    RATING_LABELS: R.RATING_LABELS,
    RATING_GROUPS: R.RATING_GROUPS,
    ARCHETYPES: R.ARCHETYPES,
    POSITION_TEMPLATES: R.POSITION_TEMPLATES,
    describeCurve: R.describeCurve(),
  }),
  stepCost: (a) => R.stepCost(...a),
  upgradeCost: (a) => R.upgradeCost(...a),
  nextPointCost: (a) => R.nextPointCost(...a),
  affordableSteps: (a) => R.affordableSteps(...a),
  startingSheet: (a) => R.startingSheet(...a),
  capsFor: (a) => R.capsFor(...a),
  ratingCeiling: (a) => R.ratingCeiling(...a),
  potentialCeiling: (a) => R.potentialCeiling(...a),
  sheetCost: (a) => R.sheetCost(...a),
  validateBuild: (a) => R.validateBuild(...a),
  validateUpgrade: (a) => R.validateUpgrade(...a),
  canCreateAnother: (a) => R.canCreateAnother(...a),
  archetypesForPosition: (a) => R.archetypesForPosition(...a),
  formatHeight: (a) => R.formatHeight(...a),
};

const calls = JSON.parse(await (await import('node:fs/promises')).readFile(process.argv[2], 'utf8'));
const out = calls.map((c) => {
  try { return { ok: true, value: ops[c.op](c.args || []) }; }
  catch (e) { return { ok: false, error: String(e && e.message ? e.message : e) }; }
});
process.stdout.write(JSON.stringify(out));
"""


def node_version():
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def run_js(calls):
    """Send a list of {op, args} to rules.js under node; get a list of results back."""
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RULES_JS, WORK / "rules.mjs")
    (WORK / "harness.mjs").write_text(HARNESS, encoding="utf-8")
    payload = WORK / "calls.json"
    payload.write_text(json.dumps(calls), encoding="utf-8")
    proc = subprocess.run(["node", str(WORK / "harness.mjs"), str(payload)],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"node failed:\n{proc.stderr[:2000]}")
    return json.loads(proc.stdout)


def value(results, i, label):
    r = results[i]
    if not r["ok"]:
        failures.append(f"{label}: rules.js threw {r['error']}")
        return None
    return r["value"]


# ---------------------------------------------------------------------------------------


def test_syntax():
    """node --check on every site script. config.js is a classic script, the rest ESM."""
    WORK.mkdir(parents=True, exist_ok=True)
    bad = []
    for path in sorted((ROOT / "site").rglob("*.js")):
        rel = path.relative_to(ROOT)
        # a module has to be checked as .mjs, or node's syntax detection may guess CommonJS
        is_module = "export " in path.read_text(encoding="utf-8") or "import " in path.read_text(encoding="utf-8")
        target = WORK / (path.stem + (".mjs" if is_module else ".cjs"))
        shutil.copyfile(path, target)
        proc = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            bad.append(f"{rel}: {proc.stderr.strip().splitlines()[-1] if proc.stderr else '?'}")
    check("every site/**/*.js passes node --check", not bad, str(bad))


EXPORT_RE = re.compile(
    r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M)
EXPORT_LIST_RE = re.compile(r"^export\s*\{([^}]*)\}", re.M)
IMPORT_RE = re.compile(r"import\s*\{([^}]*)\}\s*from\s*'(\./[^']+)'", re.S)


def exported_names(path):
    src = path.read_text(encoding="utf-8")
    names = set(EXPORT_RE.findall(src))
    for block in EXPORT_LIST_RE.findall(src):
        for part in block.split(","):
            part = part.strip()
            if not part:
                continue
            names.add(part.split(" as ")[-1].strip())
    return names


def test_imports_resolve():
    """Every named import between the site modules must actually be exported.

    node --check only parses; it never resolves a specifier, and the browser only finds
    out at run time. This is the cheap version of that check.
    """
    js_dir = ROOT / "site/js"
    exports = {p.name: exported_names(p) for p in js_dir.glob("*.js")}
    bad = []
    for path in sorted(js_dir.glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for block, target in IMPORT_RE.findall(src):
            name = target.split("/")[-1]
            if name not in exports:
                bad.append(f"{path.name} imports from {target}, which does not exist")
                continue
            for part in block.split(","):
                part = part.strip().split(" as ")[0].strip()
                if part and part not in exports[name]:
                    bad.append(f"{path.name} imports {part} from {name}, which does not export it")
    check("every named import between site modules resolves", not bad,
          "\n      " + "\n      ".join(bad) if bad else "")


def test_page_ids_exist():
    """Each page module only reaches for element ids its own page actually has."""
    pages = {"page-index.js": "index.html", "page-create.js": "create.html", "page-me.js": "me.html"}
    bad = []
    for module, page in pages.items():
        js = (ROOT / "site/js" / module).read_text(encoding="utf-8")
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        have = set(re.findall(r'id="([^"]+)"', html))
        for wanted in set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js)):
            if wanted not in have:
                bad.append(f"{module} uses #{wanted}, which {page} does not have")
    check("every #id a page script uses exists in its HTML", not bad, str(bad))


def test_pages_wire_up():
    """The three pages must load config.js as a classic script BEFORE their module, or
    window.CV_CONFIG is undefined when supabase.js reads it."""
    bad = []
    for page in ("index.html", "create.html", "me.html"):
        html = (ROOT / "site" / page).read_text(encoding="utf-8")
        cfg = html.find('src="config.js"')
        mod = html.find('type="module"')
        if cfg < 0:
            bad.append(f"{page} never loads config.js")
        elif mod < 0:
            bad.append(f"{page} has no module script")
        elif cfg > mod:
            bad.append(f"{page} loads config.js after its module")
        if 'href="css/cheezey.css"' not in html:
            bad.append(f"{page} does not link the stylesheet")
        if 'name="viewport"' not in html:
            bad.append(f"{page} has no viewport meta, so it will not work on a phone")
    check("every page loads config.js before its module, plus css and viewport", not bad, str(bad))


def test_no_dark_theme():
    """The brief says warm and cream, explicitly not a dark theme."""
    css = (ROOT / "site/css/cheezey.css").read_text(encoding="utf-8")
    check("no prefers-color-scheme: dark block", "prefers-color-scheme" not in css)
    for token in ("#F2B705", "#D9901A", "#8A5A00", "#2E2100", "#FFF8E6",
                  "#F3E4BE", "#D9C48A", "#7A4B00", "#1D5C8A"):
        if token not in css:
            check(f"palette keeps {token} from restyle.py", False)
            return
    check("palette matches restyle.py value for value", True)


def test_no_table_wide_write_grants():
    """A column-level GRANT does not narrow a table-wide one, and Supabase hands
    `authenticated` a table-wide ALL by default. So every table a user can write at all
    must be revoked wholesale first, or the column grants are decorative.

    This is the check that catches `update profiles set is_admin = true where id = auth.uid()`.
    """
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    tables = set(re.findall(r"create table if not exists public\.(\w+)", sql))
    bad = []
    for table in sorted(tables):
        # does any policy let a plain user write this table?
        writable = re.search(
            rf"create\s+policy\s+\w+\s+on\s+public\.{table}\s+for\s+(update|insert|delete|all)\s+to\s+authenticated",
            sql)
        if not writable:
            continue
        for verb in ("update", "insert", "delete"):
            has_column_grant = re.search(rf"grant\s+{verb}\s*\([^)]*\)\s*\n?\s*on public\.{table}", sql)
            revoked = re.search(
                rf"revoke[^;]*\b{verb}\b[^;]*on public\.{table} from[^;]*authenticated", sql)
            if has_column_grant and not revoked:
                bad.append(f"public.{table}: column-level {verb.upper()} grant with no table-wide revoke")
    check("no table-wide write grant survives a column-level grant", not bad,
          "\n      " + "\n      ".join(bad) if bad else "")

    # is_admin must never be grantable to a plain user
    admin_grant = re.search(r"grant\s+update\s*\([^)]*is_admin[^)]*\)", sql)
    check("is_admin is never granted to authenticated", not admin_grant)


def test_anon_key_only():
    """Nothing in site/ may carry a service role key, and .env must be gitignored."""
    bad = []
    for path in sorted((ROOT / "site").rglob("*")):
        if path.is_file() and path.suffix in (".js", ".html", ".css"):
            text = path.read_text(encoding="utf-8")
            if "service_role" in text and "never" not in text.lower():
                bad.append(str(path.relative_to(ROOT)))
    check("no service role key material anywhere under site/", not bad, str(bad))
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    check(".env is gitignored", ".env" in [line.strip() for line in ignore])
    check("no real .env was committed by this work", not (ROOT / ".env").exists()
          or ".env" in [line.strip() for line in ignore])


def test_cost_curve(results, base):
    """The documented curve: 1 below 50, 2 at 50-69, 3 at 70-84, 5 at 85+, charged per
    step at the value being *left*."""
    steps = [(0, 1), (1, 1), (49, 1), (50, 2), (60, 2), (69, 2),
             (70, 3), (80, 3), (84, 3), (85, 5), (99, 5), (100, 5)]
    bad = []
    for i, (v, want) in enumerate(steps):
        got = value(results, base + i, f"stepCost({v})")
        if got != want:
            bad.append(f"stepCost({v})={got} want {want}")
    check("step cost bands: 1 / 2 / 3 / 5", not bad, str(bad))
    return base + len(steps)


def test_documented_examples(results, base):
    """The examples written down in the spec and in schema.sql's comments."""
    cases = [
        ((48, 4, "rating"), 6, "48 -> 52 costs 1+1+2+2"),
        # the discriminator: charging at the value you ARRIVE at would make this 2
        ((49, 1, "rating"), 1, "49 -> 50 costs 1, not 2"),
        ((69, 1, "rating"), 2, "69 -> 70 costs 2"),
        ((70, 1, "rating"), 3, "70 -> 71 costs 3"),
        ((84, 1, "rating"), 3, "84 -> 85 costs 3"),
        ((85, 1, "rating"), 5, "85 -> 86 costs 5"),
        ((0, 50, "rating"), 50, "0 -> 50 costs 50"),
        ((50, 20, "rating"), 40, "50 -> 70 costs 40"),
        ((70, 15, "rating"), 45, "70 -> 85 costs 45"),
        ((85, 15, "rating"), 75, "85 -> 100 costs 75"),
        ((0, 100, "rating"), 210, "0 -> 100 costs 210"),
        ((0, 0, "rating"), 0, "no steps costs nothing"),
        ((60, 1, "potential"), 4, "potential 60 -> 61 costs double (4)"),
        ((48, 4, "potential"), 12, "potential 48 -> 52 costs double (12)"),
        ((85, 1, "potential"), 10, "potential 85 -> 86 costs double (10)"),
        ((49, 1, "potential"), 2, "potential 49 -> 50 costs double (2)"),
    ]
    bad = []
    for i, (args, want, label) in enumerate(cases):
        got = value(results, base + i, label)
        if got != want:
            bad.append(f"{label}: got {got}, want {want}")
    check("documented cost examples", not bad, "\n      " + "\n      ".join(bad) if bad else "")
    return base + len(cases)


def test_affordable(results, base):
    cases = [
        ((48, 6, 100, "rating"), {"steps": 4, "cost": 6}),
        ((48, 5, 100, "rating"), {"steps": 3, "cost": 4}),
        ((48, 100, 50, "rating"), {"steps": 2, "cost": 2}),   # ceiling bites before the budget
        ((60, 3, 100, "potential"), {"steps": 0, "cost": 0}),  # a potential step here is 4
    ]
    bad = []
    for i, (args, want) in enumerate(cases):
        got = value(results, base + i, f"affordableSteps{args}")
        if got != want:
            bad.append(f"affordableSteps{args} -> {got}, want {want}")
    check("affordableSteps stops at the budget and at the ceiling", not bad, str(bad))
    return base + len(cases)


def test_vocabulary(consts):
    """The 18 and the 12, exactly these names in exactly this order (CONVENTIONS.md)."""
    ratings = ["InsideScoring", "JumpShot", "FtShot", "3pUsage", "3pShot", "Handling", "Passing",
               "Quickness", "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "OReb",
               "DReb", "Jumping", "Strength", "Stamina", "Fouling"]
    pots = ["InsideScoring", "JumpShot", "FtShot", "3pShot", "Handling", "Passing", "OReb",
            "DReb", "PostDefense", "PerimeterDefense", "Stealing", "Blocking"]
    check("18 ratings, exact names and order", consts["RATINGS"] == ratings,
          "" if consts["RATINGS"] == ratings else str(consts["RATINGS"]))
    check("12 potentials, exact names and order", consts["POTENTIAL_RATINGS"] == pots,
          "" if consts["POTENTIAL_RATINGS"] == pots else str(consts["POTENTIAL_RATINGS"]))
    grouped = [r for g in consts["RATING_GROUPS"] for r in g["ratings"]]
    check("RATING_GROUPS covers all 18 exactly once", sorted(grouped) == sorted(ratings),
          str(sorted(set(ratings)) if sorted(grouped) != sorted(ratings) else ""))
    check("every rating has a label", all(r in consts["RATING_LABELS"] for r in ratings))
    check("potentials are a subset of ratings", set(pots) <= set(ratings))


def test_codec_agreement(consts):
    """The site's rating names must be the codec's names, or an upgrade cannot be written
    into league.dat. Read straight out of commissioner/codec/league_dat.py."""
    src = (ROOT / "commissioner/codec/league_dat.py").read_text(encoding="utf-8")
    m = re.search(r"^RATINGS = (\[.*?\])\n(?=POTENTIALS)", src, re.S | re.M)
    codec_ratings = eval(m.group(1)) if m else None  # noqa: S307 - our own source file
    check("site ratings == codec RATINGS", codec_ratings == consts["RATINGS"],
          "" if codec_ratings == consts["RATINGS"] else str(codec_ratings))


def test_schema_mirror():
    """The SQL copy of the curve has to say the same thing. Cheap textual check - the SQL
    is never executed in this test suite (see the notes at the top of schema.sql)."""
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    band = re.search(r"case when v < 50 then 1 when v < 70 then 2 when v < 85 then 3 else 5 end", sql)
    check("schema.sql cv_step_cost has the same bands as rules.js", bool(band))
    doubles = "total := total * 2" in sql
    check("schema.sql doubles the cost of a potential step", doubles)
    for name in ("InsideScoring", "PerimeterDefense", "Fouling", "3pUsage"):
        if f"'{name}'" not in sql:
            check(f"schema.sql cv_ratings lists {name}", False)
            return
    check("schema.sql cv_ratings lists the same rating names", True)


def test_starting_sheets(results, base, consts):
    """Every (archetype, position) pair must produce a legal, playable starting sheet:
    rating <= potential <= archetype cap, and nothing already maxed out."""
    pairs = [(a, p) for a in consts["ARCHETYPE_KEYS"]
             for p in consts["ARCHETYPES"][a]["positions"]]
    bad_legal, bad_cost, bad_terrible, bad_room = [], [], [], []
    idx = base
    for a, p in pairs:
        sheet = value(results, idx, f"startingSheet({p},{a})"); idx += 1
        caps = value(results, idx, f"capsFor({a})"); idx += 1
        cost = value(results, idx, f"sheetCost({p},{a})"); idx += 1
        if sheet is None or caps is None:
            continue
        for r in consts["RATINGS"]:
            if sheet["ratings"][r] > caps[r]:
                bad_legal.append(f"{a}/{p} {r}: rating {sheet['ratings'][r]} > cap {caps[r]}")
        for r in consts["POTENTIAL_RATINGS"]:
            if sheet["ratings"][r] > sheet["potentials"][r]:
                bad_legal.append(f"{a}/{p} {r}: rating {sheet['ratings'][r]} > potential {sheet['potentials'][r]}")
            if sheet["potentials"][r] > caps[r]:
                bad_legal.append(f"{a}/{p} {r}: potential {sheet['potentials'][r]} > cap {caps[r]}")
        if cost != 0:
            bad_cost.append(f"{a}/{p} untouched sheet costs {cost}")
        # a 14 year old is terrible: nothing above 30 to start with, tendencies aside
        skills = [v for r, v in sheet["ratings"].items() if r not in ("3pUsage", "Fouling")]
        if max(skills) > 30:
            bad_terrible.append(f"{a}/{p} starts at {max(skills)}")
        # and there is somewhere to spend the first 20 points
        room = sum(1 for r in consts["RATINGS"]
                   if r not in consts["LOCKED_RATINGS"]
                   and sheet["ratings"][r] < (sheet["potentials"].get(r) or caps[r]))
        if room < 6:
            bad_room.append(f"{a}/{p} has only {room} spendable ratings")
    check(f"all {len(pairs)} archetype/position sheets are legal (rating <= potential <= cap)",
          not bad_legal, "\n      " + "\n      ".join(bad_legal[:5]) if bad_legal else "")
    check("an untouched starting sheet costs 0 points", not bad_cost, str(bad_cost[:3]))
    check("every character starts genuinely terrible (no skill over 30)", not bad_terrible, str(bad_terrible[:3]))
    check("every sheet has room to spend the opening points", not bad_room, str(bad_room[:3]))
    return idx, pairs


def test_build_validation(results, base):
    labels = ["clean 20-point build is accepted",
              "overspending is rejected",
              "a rating above its potential is rejected",
              "a potential above the archetype cap is rejected",
              "a Big Man cannot be a point guard",
              "a locked rating (Fouling) cannot be bought",
              "a nameless character is rejected",
              "a 5'6\" center is rejected"]
    wants = [True, False, False, False, False, False, False, False]
    bad = []
    for i, (label, want) in enumerate(zip(labels, wants)):
        got = value(results, base + i, label)
        if got is None or got["ok"] != want:
            bad.append(f"{label}: ok={None if got is None else got['ok']} "
                       f"errors={[] if got is None else got['errors'][:2]}")
        else:
            check(label, True, f"(spent {got['spent']})" if want else f"({got['errors'][0]})")
    for line in bad:
        check(line.split(":")[0], False, line)
    return base + len(labels)


def test_upgrade_validation(results, base):
    cases = [
        ("an affordable upgrade is allowed", True),
        ("an upgrade past the potential is refused", False),
        ("an upgrade you cannot afford is refused", False),
        ("queued requests reserve their cost", False),
    ]
    for i, (label, want) in enumerate(cases):
        got = value(results, base + i, label)
        ok = got is not None and got["ok"] == want
        check(label, ok, "" if ok else f"got {got}")
    return base + len(cases)


def test_limits(results, base):
    got = value(results, base, "canCreateAnother")
    check("two live characters means no third", got is not None and got["ok"] is False,
          f"got {got}")
    got = value(results, base + 1, "canCreateAnother with a retired one")
    check("a retired character does not count against the limit",
          got is not None and got["ok"] is True, f"got {got}")
    got = value(results, base + 2, "formatHeight")
    check("height formats as feet and inches", got == "6'4\"", f"got {got}")
    return base + 3


def main():
    ver = node_version()
    if not ver:
        print("SKIP  node is not on PATH; rules.js was not executed.")
        print("      Install node, or run these assertions by hand against site/js/rules.js.")
        skipped.append("node")
        return 0
    print(f"node {ver}; executing site/js/rules.js directly\n")

    test_syntax()
    test_imports_resolve()
    test_page_ids_exist()
    test_pages_wire_up()
    test_no_dark_theme()
    test_no_table_wide_write_grants()
    test_anon_key_only()

    consts_call = [{"op": "constants"}]
    consts = run_js(consts_call)[0]["value"]

    calls = []
    for v in (0, 1, 49, 50, 60, 69, 70, 80, 84, 85, 99, 100):
        calls.append({"op": "stepCost", "args": [v]})
    for args in [(48, 4, "rating"), (49, 1, "rating"), (69, 1, "rating"), (70, 1, "rating"),
                 (84, 1, "rating"), (85, 1, "rating"), (0, 50, "rating"), (50, 20, "rating"),
                 (70, 15, "rating"), (85, 15, "rating"), (0, 100, "rating"), (0, 0, "rating"),
                 (60, 1, "potential"), (48, 4, "potential"), (85, 1, "potential"),
                 (49, 1, "potential")]:
        calls.append({"op": "upgradeCost", "args": list(args)})
    for args in [(48, 6, 100, "rating"), (48, 5, 100, "rating"),
                 (48, 100, 50, "rating"), (60, 3, 100, "potential")]:
        calls.append({"op": "affordableSteps", "args": list(args)})

    sheet_base = len(calls)
    pairs = [(a, p) for a in consts["ARCHETYPE_KEYS"] for p in consts["ARCHETYPES"][a]["positions"]]
    for a, p in pairs:
        calls.append({"op": "startingSheet", "args": [p, a]})
        calls.append({"op": "capsFor", "args": [a]})
        calls.append({"op": "sheetCost", "args": [p, a, None, None]})

    # sheetCost with None ratings means "nothing raised", which must be free; feed the real
    # starting sheet back in for the build tests below.
    seed = run_js([{"op": "startingSheet", "args": ["SG", "sharpshooter"]},
                   {"op": "startingSheet", "args": ["C", "big_man"]}])
    sg = seed[0]["value"]
    big = seed[1]["value"]

    def build(sheet, **over):
        b = {"firstName": "Milo", "lastName": "Trask", "position": "SG",
             "archetype": "sharpshooter", "heightInches": 75,
             "ratings": dict(sheet["ratings"]), "potentials": dict(sheet["potentials"])}
        b.update(over)
        return b

    # 1. a clean build: put 20 points into 3pShot and JumpShot, both below 50 so 1/point
    clean = build(sg)
    clean["ratings"]["3pShot"] = min(sg["potentials"]["3pShot"], sg["ratings"]["3pShot"] + 10)
    clean["ratings"]["JumpShot"] = min(sg["potentials"]["JumpShot"], sg["ratings"]["JumpShot"] + 10)
    # 2. the same but 40 points of it
    over = build(sg)
    over["potentials"]["3pShot"] = min(95, sg["potentials"]["3pShot"] + 20)
    over["ratings"]["3pShot"] = sg["ratings"]["3pShot"] + 20
    # 3. rating above potential
    above = build(sg)
    above["ratings"]["Handling"] = above["potentials"]["Handling"] + 1
    # 4. potential above the archetype cap
    capped = build(sg)
    capped["potentials"]["Blocking"] = 99
    # 5. wrong position for the archetype
    wrongpos = build(big, position="PG", archetype="big_man")
    # 6. buying a locked rating
    locked = build(sg)
    locked["ratings"]["Fouling"] = locked["ratings"]["Fouling"] + 5
    # 7. no name
    nameless = build(sg, firstName="", lastName="")
    # 8. a 5'6" center
    short = build(big, position="C", archetype="big_man", heightInches=66)

    build_base = len(calls)
    for b in (clean, over, above, capped, wrongpos, locked, nameless, short):
        calls.append({"op": "validateBuild", "args": [b, {"startingPoints": 20}]})

    upgrade_base = len(calls)
    char = {"archetype": "sharpshooter", "ratings": dict(sg["ratings"]),
            "potentials": dict(sg["potentials"]), "points_available": 10}
    calls.append({"op": "validateUpgrade", "args": [char, "3pShot", 3, "rating", 0]})
    pot_gap = sg["potentials"]["3pShot"] - sg["ratings"]["3pShot"]
    calls.append({"op": "validateUpgrade", "args": [char, "3pShot", pot_gap + 1, "rating", 0]})
    calls.append({"op": "validateUpgrade", "args": [
        {**char, "points_available": 1}, "3pShot", 3, "rating", 0]})
    calls.append({"op": "validateUpgrade", "args": [char, "3pShot", 3, "rating", 9]})

    limit_base = len(calls)
    calls.append({"op": "canCreateAnother", "args": [
        [{"status": "active"}, {"status": "pending"}], 2]})
    calls.append({"op": "canCreateAnother", "args": [
        [{"status": "active"}, {"status": "retired"}], 2]})
    calls.append({"op": "formatHeight", "args": [76]})

    results = run_js(calls)

    i = test_cost_curve(results, 0)
    i = test_documented_examples(results, i)
    i = test_affordable(results, i)
    test_vocabulary(consts)
    test_codec_agreement(consts)
    test_schema_mirror()
    test_starting_sheets(results, sheet_base, consts)
    test_build_validation(results, build_base)
    test_upgrade_validation(results, upgrade_base)
    test_limits(results, limit_base)

    print()
    print(f"cost curve as rules.js describes it: {consts['describeCurve']}")
    print(f"{len(pairs)} archetype/position combinations, "
          f"{len(consts['ARCHETYPE_KEYS'])} archetypes, {len(consts['RATINGS'])} ratings")
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("all rules tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
