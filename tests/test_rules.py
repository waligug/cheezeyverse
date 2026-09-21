"""Regression tests for site/js/rules.js and commissioner/growth.py.

Run: python tests/test_rules.py

These tests do NOT reimplement the rules. Node is available in this environment
(v24.14.0), so the real `site/js/rules.js` is imported and executed, and every number
asserted below comes back from the browser's own code. If node ever disappears the run
reports SKIP rather than quietly passing.

How it works: rules.js is an ES module but lives as `.js` in a folder with no
package.json, so node would treat it as CommonJS. The harness copies it to
`tmp/rules-test/rules.mjs` (tmp/ is gitignored), writes a small dispatcher beside it, and
talks to it with one JSON file in and one JSON blob out. Windows needs no file:// dance
that way, because the import is a plain relative specifier. rules.js deliberately has no
relative imports of its own, so copying that one file is enough.

The one place this file computes anything itself is the height model, and that is the
whole point of it: commissioner/growth.py is imported directly and its curves are compared
inch by inch against the JS copy's.
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

sys.path.insert(0, str(ROOT))
from commissioner import growth as PY_GROWTH  # noqa: E402  - after sys.path

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
    TENDENCY_RATINGS: R.TENDENCY_RATINGS,
    SKILL_RATINGS: R.SKILL_RATINGS,
    POSITIONS: R.POSITIONS,
    LOCKED_RATINGS: R.LOCKED_RATINGS,
    POTENTIAL_MULTIPLIER: R.POTENTIAL_MULTIPLIER,
    RATING_MAX: R.RATING_MAX,
    BIAS_MIN: R.BIAS_MIN,
    BIAS_MAX: R.BIAS_MAX,
    GOAL_DISCOUNT: R.GOAL_DISCOUNT,
    START_RATING_CEILING: R.START_RATING_CEILING,
    START_RATING_FLOOR: R.START_RATING_FLOOR,
    START_POTENTIAL_CEILING: R.START_POTENTIAL_CEILING,
    HEIGHT_RANGES: R.HEIGHT_RANGES,
    HEIGHT_DEFAULTS: R.HEIGHT_DEFAULTS,
    ADULT_HEIGHT_RANGES: R.ADULT_HEIGHT_RANGES,
    RATING_LABELS: R.RATING_LABELS,
    RATING_GROUPS: R.RATING_GROUPS,
    POSITION_TEMPLATES: R.POSITION_TEMPLATES,
    TRAITS: R.TRAITS,
    QUIZ: R.QUIZ,
    QUIZ_IDS: R.QUIZ_IDS,
    CAREER_GOALS: R.CAREER_GOALS,
    SUMMER_WORK: R.SUMMER_WORK,
    BUILDS: R.BUILDS,
    WEIGHT_SPREAD: R.WEIGHT_SPREAD,
    CLASSES: R.CLASSES,
    CLASS_IDS: R.CLASS_IDS,
    GROWTH_END_AGE: R.GROWTH_END_AGE,
    HEIGHT_GROWTH: R.HEIGHT_GROWTH,
    ROLL_BAND_MAX: R.ROLL_BAND_MAX,
    ROLL_BAND_MIN: R.ROLL_BAND_MIN,
    TENDENCY_BAND_MAX: R.TENDENCY_BAND_MAX,
    TENDENCY_BAND_MIN: R.TENDENCY_BAND_MIN,
    describeCurve: R.describeCurve(),
  }),
  stepCost: (a) => R.stepCost(...a),
  upgradeCost: (a) => R.upgradeCost(...a),
  nextPointCost: (a) => R.nextPointCost(...a),
  affordableSteps: (a) => R.affordableSteps(...a),
  applyBias: (a) => R.applyBias(...a),
  biasedUpgradeCost: (a) => R.biasedUpgradeCost(...a),
  traitsFromQuiz: (a) => R.traitsFromQuiz(...a),
  startingSheet: (a) => R.startingSheet(...a),
  tendencies: (a) => R.tendencies(...a),
  growthBias: (a) => R.growthBias(...a),
  deriveCharacter: (a) => R.deriveCharacter(...a),
  classify: (a) => R.classify(...a),
  ratingCeiling: (a) => R.ratingCeiling(...a),
  potentialCeiling: (a) => R.potentialCeiling(...a),
  sheetCost: (a) => R.sheetCost(...a),
  validateBuild: (a) => R.validateBuild(...a),
  validateUpgrade: (a) => R.validateUpgrade(...a),
  canCreateAnother: (a) => R.canCreateAnother(...a),
  formatHeight: (a) => R.formatHeight(...a),
  heightSeed: (a) => R.heightSeed(...a),
  identitySeed: (a) => R.identitySeed(...a),
  rollSwings: (a) => R.rollSwings(...a),
  ratingBands: (a) => R.ratingBands(...a),
  expectedAdultHeight: (a) => R.expectedAdultHeight(...a),
  growthCurve: (a) => R.growthCurve(...a),
  heightAtAge: (a) => R.heightAtAge(...a),
  heightOutlook: (a) => R.heightOutlook(...a),
  buildWeight: (a) => R.buildWeight(...a),
  weightRange: (a) => R.weightRange(...a),
  frameWeight: (a) => R.frameWeight(...a),
  weightAt: (a) => R.weightAt(...a),
  scoutingWord: (a) => R.scoutingWord(...a),
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
                          capture_output=True, text=True, timeout=180)
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
# Sample inputs. Deterministic by construction, so a failure is always reproducible.
# ---------------------------------------------------------------------------------------


def sample_answers(quiz, k):
    """Answer set number k: walk each question's options at a different stride."""
    out = {}
    for i, q in enumerate(quiz):
        out[q["id"]] = q["answers"][(k * (i + 3) + i) % len(q["answers"])]["id"]
    return out


FIRST_NAMES = ["Milo", "Dez", "Ari", "Kofi", "Rhys", "Tobias", "Jem", "Ola"]
LAST_NAMES = ["Trask", "Okonkwo", "Vance", "Bello", "Hartmann", "Quist", "Adeyemi", "Falk"]
TOWNS = ["Scarborough, ON", "Gary, IN", "Compton, CA", "Bed-Stuy, NY",
         "Shreveport, LA", "Tacoma, WA", "Reading, PA", "Lubbock, TX"]


def sample_inputs(consts, count=120):
    """Whole creation inputs, covering positions, answers, goals, summers, builds and -
    because the roll is seeded off it - identities."""
    quiz = consts["QUIZ"]
    goals = [g["id"] for g in consts["CAREER_GOALS"]]
    summers = [s["id"] for s in consts["SUMMER_WORK"]]
    builds = [b["id"] for b in consts["BUILDS"]]
    rows = []
    for k in range(count):
        pos = consts["POSITIONS"][k % len(consts["POSITIONS"])]
        lo, hi = consts["HEIGHT_RANGES"][pos]
        rows.append({
            "firstName": FIRST_NAMES[k % len(FIRST_NAMES)],
            "lastName": LAST_NAMES[(k * 3) % len(LAST_NAMES)],
            "hometown": TOWNS[(k * 5) % len(TOWNS)],
            "jersey": k % 100,
            "position": pos,
            "answers": sample_answers(quiz, k),
            "goal": goals[k % len(goals)],
            "summer": summers[k % len(summers)],
            "build": builds[k % len(builds)],
            "heightInches": lo + (k % (hi - lo + 1)),
        })
    return rows


# ---------------------------------------------------------------------------------------
# Static checks (no node call needed beyond --check)
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


def test_rules_has_no_imports():
    """rules.js must stay standalone, or the harness above cannot copy one file and run it.

    It is also what lets commissioner tooling read it without a bundler.
    """
    src = RULES_JS.read_text(encoding="utf-8")
    check("rules.js imports nothing", not re.search(r"^import\s", src, re.M))


def test_page_ids_exist():
    """Each page module only reaches for element ids its own page actually has."""
    pages = {"page-index.js": "index.html", "page-create.js": "create.html", "page-me.js": "me.html",
             "page-career.js": "career.html"}
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
    """Every page must load config.js as a classic script BEFORE its module, or
    window.CV_CONFIG is undefined when supabase.js reads it."""
    bad = []
    for page in ("index.html", "create.html", "me.html", "career.html"):
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


def test_new_columns_are_granted():
    """A column added to characters without a SELECT grant silently 403s the whole site,
    because supabase.js never does select('*'). Pin the two lists to each other."""
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    js = (ROOT / "site/js/supabase.js").read_text(encoding="utf-8")
    grant = re.search(r"grant\s+select\s*\((.*?)\)\s*\n?\s*on public\.characters", sql, re.S)
    granted = set(re.findall(r"[\w\"]+", grant.group(1))) if grant else set()
    granted = {g.strip('"') for g in granted}
    wanted = ["jersey_preference", "hometown", "build", "career_goal", "traits",
              "quiz_answers", "growth_bias", "height_seed", "expected_adult_height"]
    missing = [c for c in wanted if c not in granted]
    check("every new character column is in the SELECT grant", not missing, str(missing))

    cols = re.search(r"CHARACTER_COLUMNS = \[(.*?)\]", js, re.S)
    listed = set(re.findall(r"'([\w]+)'", cols.group(1))) if cols else set()
    missing_js = [c for c in wanted if c not in listed]
    check("CHARACTER_COLUMNS lists them too", not missing_js, str(missing_js))

    # height_seed is derived from the row's own id and must not be insertable
    ins = re.search(r"grant\s+insert\s*\((.*?)\)\s*\n?\s*on public\.characters", sql, re.S)
    ins_cols = set(re.findall(r"[\w\"]+", ins.group(1))) if ins else set()
    check("height_seed is not insertable by a user", "height_seed" not in ins_cols)


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

    # the growth bias is applied AFTER the curve, never inside it
    inside = re.search(r"create or replace function public\.cv_upgrade_cost.*?\$fn\$;", sql, re.S)
    check("cv_upgrade_cost itself knows nothing about the bias",
          bool(inside) and "bias" not in inside.group(0))
    applied = re.search(
        r"new\.cost\s*:=\s*greatest\(1,\s*round\(new\.cost\s*\*\s*bias_pct\s*/\s*100\.0\)\)", sql)
    check("schema.sql applies max(1, round(cost * bias / 100)) after the curve", bool(applied))
    clamped = re.search(r"least\(115,\s*greatest\(85,", sql)
    check("schema.sql clamps the bias to 85..115", bool(clamped))


def test_codec_agreement(consts):
    """The site's rating names must be the codec's names, or an upgrade cannot be written
    into league.dat. Read straight out of commissioner/codec/league_dat.py."""
    src = (ROOT / "commissioner/codec/league_dat.py").read_text(encoding="utf-8")
    m = re.search(r"^RATINGS = (\[.*?\])\n(?=POTENTIALS)", src, re.S | re.M)
    codec_ratings = eval(m.group(1)) if m else None  # noqa: S307 - our own source file
    check("site ratings == codec RATINGS", codec_ratings == consts["RATINGS"],
          "" if codec_ratings == consts["RATINGS"] else str(codec_ratings))


# ---------------------------------------------------------------------------------------
# The cost curve - unchanged by the redesign, and pinned here so it stays that way
# ---------------------------------------------------------------------------------------


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


def test_bias_arithmetic(results, base, consts):
    """max(1, round(cost * bias / 100)), clamped 85..115, and neutral is a no-op."""
    cases = [
        ((10, 100), 10, "neutral bias changes nothing"),
        ((10, 85), 9, "10 at 85% is 9"),
        ((10, 115), 12, "10 at 115% is 12 (round half up)"),
        ((1, 85), 1, "a 1 point step never becomes free"),
        ((3, 85), 3, "3 at 85% rounds to 3"),
        ((40, 90), 36, "40 at 90% is 36"),
        ((10, 40), 9, "a bias below 85 is clamped to 85"),
        ((10, 400), 12, "a bias above 115 is clamped to 115"),
    ]
    bad = []
    for i, (args, want, label) in enumerate(cases):
        got = value(results, base + i, label)
        if got != want:
            bad.append(f"{label}: got {got}, want {want}")
    check("the growth bias is max(1, round(cost * bias / 100)), clamped 85..115",
          not bad, "\n      " + "\n      ".join(bad) if bad else "")
    base += len(cases)

    # and biasedUpgradeCost with no bias is exactly upgradeCost
    same = value(results, base, "biasedUpgradeCost(48,4,rating,100)")
    check("a neutral bias leaves the curve exactly as it was", same == 6, f"got {same}")
    return base + 1


# ---------------------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------------------


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
    check("the two tendencies plus the 16 skills are all 18",
          sorted(consts["SKILL_RATINGS"] + consts["TENDENCY_RATINGS"]) == sorted(ratings))
    check("Fouling is still locked and 3pUsage is still spendable",
          consts["LOCKED_RATINGS"] == ["Fouling"], str(consts["LOCKED_RATINGS"]))


# ---------------------------------------------------------------------------------------
# The quiz
# ---------------------------------------------------------------------------------------


def test_quiz_shape(consts):
    """Fourteen questions, three or four answers each, every weight on a real trait, and
    every question carrying the comment that says what it is really measuring."""
    quiz = consts["QUIZ"]
    traits = set(consts["TRAITS"])
    check(f"the quiz has {len(quiz)} questions, 10 to 16", 10 <= len(quiz) <= 16, str(len(quiz)))

    bad = []
    for q in quiz:
        if not 3 <= len(q["answers"]) <= 4:
            bad.append(f"{q['id']} has {len(q['answers'])} answers")
        if not q.get("measures"):
            bad.append(f"{q['id']} does not say what it measures")
        if not q.get("prompt"):
            bad.append(f"{q['id']} has no prompt")
        for a in q["answers"]:
            if not a.get("text"):
                bad.append(f"{q['id']}/{a['id']} has no text")
            for t in a["weights"]:
                if t not in traits:
                    bad.append(f"{q['id']}/{a['id']} weights unknown trait {t}")
    check("every question is 3-4 answers, documented, and weights only real traits",
          not bad, "\n      " + "\n      ".join(bad[:6]) if bad else "")

    # no answer reads like a stat line
    leaks = [f"{q['id']}/{a['id']}" for q in quiz for a in q["answers"]
             if re.search(r"[+-]\s*\d+\s*(to\s+)?[A-Z]", a["text"])]
    check("no answer is written as a stat adjustment", not leaks, str(leaks))

    ids = [q["id"] for q in quiz]
    check("question ids are unique", len(set(ids)) == len(ids))


def test_no_dominant_answer(consts):
    """No answer may Pareto-dominate another.

    "Dominant" here is the strong reading: answer A dominates B if A is at least as good
    as B on EVERY trait and strictly better on at least one. If any answer did that, it
    would be a free button - the whole quiz would collapse into picking the dominant option
    everywhere. The house rule that prevents it is in the comment above QUIZ: every answer
    carries a negative weight on a trait no sibling is also negative on.

    The career-goal and summer questions are deliberately NOT covered: their answers differ
    in which ratings they discount and which ratings they bump, not only in trait weights,
    so trait-axis dominance is not the right question to ask of them.
    """
    traits = consts["TRAITS"]
    bad = []
    for q in consts["QUIZ"]:
        for a in q["answers"]:
            for b in q["answers"]:
                if a["id"] == b["id"]:
                    continue
                ge = all(a["weights"].get(t, 0) >= b["weights"].get(t, 0) for t in traits)
                gt = any(a["weights"].get(t, 0) > b["weights"].get(t, 0) for t in traits)
                if ge and gt:
                    bad.append(f"{q['id']}: '{a['id']}' dominates '{b['id']}'")
    check("no quiz answer dominates another on every trait", not bad,
          "\n      " + "\n      ".join(bad[:6]) if bad else "")

    # the house rule itself, so a future edit fails loudly rather than subtly
    weak = []
    for q in consts["QUIZ"]:
        for a in q["answers"]:
            negatives = {t for t, w in a["weights"].items() if w < 0}
            if not negatives:
                weak.append(f"{q['id']}/{a['id']} has no downside at all")
    check("every answer costs something somewhere", not weak,
          "\n      " + "\n      ".join(weak[:6]) if weak else "")


def test_quiz_determinism(results, base):
    """The same answers must always produce the same person. There is no randomness in the
    derivation at all - the only PRNG in rules.js is the height model's, and that is seeded
    off the character id."""
    a1 = value(results, base, "traitsFromQuiz #1")
    a2 = value(results, base + 1, "traitsFromQuiz #2")
    check("the same answers derive the same traits, twice", a1 == a2 and a1 is not None,
          "" if a1 == a2 else f"{a1} vs {a2}")

    d1 = value(results, base + 2, "deriveCharacter #1")
    d2 = value(results, base + 3, "deriveCharacter #2")
    same = d1 is not None and json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)
    check("the same answers derive the same sheet, class, bias and height, twice", same)

    d3 = value(results, base + 4, "deriveCharacter, answers in the other order")
    same_order = (d1 is not None and d3 is not None
                  and json.dumps(d1, sort_keys=True) == json.dumps(d3, sort_keys=True))
    check("the order the questions were answered in does not matter", same_order)

    # an empty quiz still derives a legal, boring player rather than throwing
    blank = value(results, base + 5, "deriveCharacter with no answers")
    check("an unanswered quiz still derives a legal player",
          blank is not None and all(50 == v for v in blank["traits"].values()),
          "" if blank is None else str(blank["traits"]))
    return base + 6


# ---------------------------------------------------------------------------------------
# Traits -> sheet
# ---------------------------------------------------------------------------------------


def test_derived_sheets(results, base, consts, inputs):
    """Every position crossed with a spread of quiz answers has to produce a legal,
    playable, genuinely bad fourteen year old."""
    ceiling = consts["START_RATING_CEILING"]
    floor = consts["START_RATING_FLOOR"]
    pot_ceiling = consts["START_POTENTIAL_CEILING"]
    tendencies = consts["TENDENCY_RATINGS"]

    bad_bounds, bad_legal, bad_tendency, bad_room, bad_bias = [], [], [], [], []
    bad_range, bad_band = [], []
    seen_max = 0
    for i, row in enumerate(inputs):
        d = value(results, base + i, f"deriveCharacter #{i}")
        if d is None:
            continue
        for r in consts["RATINGS"]:
            lo, hi = d["ranges"][r]
            if not lo <= d["ratings"][r] <= hi:
                bad_range.append(f"{row['position']} {r}: {d['ratings'][r]} not in {lo}..{hi}")
            if lo > hi:
                bad_range.append(f"{row['position']} {r}: range is backwards")
            limit = (consts["TENDENCY_BAND_MAX"] if r in tendencies
                     else consts["ROLL_BAND_MAX"])
            floor_band = (consts["TENDENCY_BAND_MIN"] if r in tendencies
                          else consts["ROLL_BAND_MIN"])
            if not floor_band <= d["bands"][r] <= limit:
                bad_band.append(f"{row['position']} {r} band {d['bands'][r]} "
                                f"outside {floor_band}..{limit}")
        for r in consts["POTENTIAL_RATINGS"]:
            plo, phi = d["potentialRanges"][r]
            if not plo <= d["potentials"][r] <= phi:
                bad_range.append(
                    f"{row['position']} {r} potential: {d['potentials'][r]} not in {plo}..{phi}")
        for r in consts["SKILL_RATINGS"]:
            v = d["ratings"][r]
            seen_max = max(seen_max, v)
            if v > ceiling or v < floor:
                bad_bounds.append(f"{row['position']} {r} = {v}")
        for r in tendencies:
            v = d["ratings"][r]
            if v < 0 or v > 100:
                bad_tendency.append(f"{row['position']} {r} = {v}")
        for r in consts["POTENTIAL_RATINGS"]:
            p = d["potentials"][r]
            if p < d["ratings"][r]:
                bad_legal.append(f"{row['position']} {r}: rating {d['ratings'][r]} > potential {p}")
            if p > pot_ceiling:
                bad_legal.append(f"{row['position']} {r}: potential {p} > {pot_ceiling}")
        room = sum(1 for r in consts["RATINGS"]
                   if r not in consts["LOCKED_RATINGS"]
                   and d["ratings"][r] < (d["potentials"].get(r) or 100))
        if room < 10:
            bad_room.append(f"{row['position']} has only {room} spendable ratings")
        for r, pct in d["bias"].items():
            if pct < consts["BIAS_MIN"] or pct > consts["BIAS_MAX"]:
                bad_bias.append(f"{row['position']} {r} bias {pct}")
        if d["bias"]["Fouling"] != 100:
            bad_bias.append("Fouling is locked but has a bias")

    check(f"no starting skill is above {ceiling} across {len(inputs)} sampled builds",
          not bad_bounds, f"(highest seen: {seen_max}) " + str(bad_bounds[:4]))
    check("rating <= potential <= 58 on every sampled build", not bad_legal,
          "\n      " + "\n      ".join(bad_legal[:4]) if bad_legal else "")
    check("the two tendencies stay inside 0-100", not bad_tendency, str(bad_tendency[:4]))
    check("every sheet has plenty of room to spend weekly points", not bad_room, str(bad_room[:3]))
    check("every growth bias is inside 85..115, and locked ratings are neutral",
          not bad_bias, str(bad_bias[:4]))
    check("the rolled value always lands inside the band the page showed", not bad_range,
          "\n      " + "\n      ".join(bad_range[:4]) if bad_range else "")
    check("no band is wider than the model allows", not bad_band, str(bad_band[:4]))
    return base + len(inputs)


def test_the_roll(results, base, consts):
    """The roll is seeded off the identity, mirrored in Python, and honestly advertised."""
    js_swings = value(results, base, "rollSwings")
    py_swings = PY_GROWTH.roll_swings(123456789, ROLL_BANDS)
    check("JS and Python draw identical swings from the same seed", js_swings == py_swings,
          f"{js_swings} vs {py_swings}")

    bad_seed = []
    for i, ident in enumerate(IDENTITIES):
        js = value(results, base + 1 + i, f"identitySeed {ident}")
        py = PY_GROWTH.identity_seed(*ident)
        if js != py:
            bad_seed.append(f"{ident}: js {js} != py {py}")
    check("JS and Python hash the same identity to the same seed", not bad_seed,
          str(bad_seed[:3]))
    base += 1 + len(IDENTITIES)

    same_a = value(results, base, "same identity #1")
    same_b = value(results, base + 1, "same identity #2")
    other = value(results, base + 2, "one letter different")
    ok_same = same_a is not None and same_a["ratings"] == (same_b or {}).get("ratings")
    check("the same identity and the same answers roll the same player", ok_same)
    ok_diff = other is not None and other["ratings"] != (same_a or {}).get("ratings")
    check("two people who answer identically still get different players", ok_diff)
    check("the traits are NOT rolled - only the sheet is",
          same_a is not None and other is not None and same_a["traits"] == other["traits"])
    base += 3

    emphatic = value(results, base, "bands after emphatic answers")
    vague = value(results, base + 1, "bands after neutral answers")
    ok_band = (emphatic is not None and vague is not None
               and emphatic["3pShot"] < vague["3pShot"])
    check("the band is tighter where the answers were emphatic", ok_band,
          "" if ok_band else f"{emphatic} vs {vague}")

    word = value(results, base + 2, "scoutingWord")
    check("a rating reads as a scout's phrase, never a number",
          isinstance(word, str) and not re.search(r"\d", word), f"got {word!r}")
    return base + 3


def test_nothing_leaks_before_signing():
    """The create page must not print an exact rating, potential or adult height while the
    quiz is being answered. That is the whole reason the roll exists: if you can watch a
    number move as you flip an answer, the quiz is a stat allocator with extra steps.

    Structural check - the numberless renderer is what the review card calls, and the one
    that prints numbers is only reachable from the reveal after the row is inserted.
    """
    js = (ROOT / "site/js/page-create.js").read_text(encoding="utf-8")
    review = js[js.index("function renderReview("):js.index("function renderSigned(")]
    signed = js[js.index("function renderSigned("):]
    check("the review card renders the numberless scouting sheet",
          "renderScoutingSheet(sheet, derived)" in review
          and "renderDerivedSheet" not in review)
    check("trait numbers are off until he is signed",
          "{ numbers: false }" in review and "{ numbers: true }" in signed)
    check("the exact sheet is only rendered after he is signed",
          "renderDerivedSheet(sheet, derived)" in signed)
    check("the height shown before signing is a range, not the expectation",
          "outlook.expected" not in review and "o.low" in review and "o.high" in review)

    # ...and the player page must not print the *future* of the height curve either. The
    # curve for a 14 year old already exists, but showing it would hand back the one
    # number the roll was introduced to withhold.
    me = (ROOT / "site/js/page-me.js").read_text(encoding="utf-8")
    block = me[me.index("function heightBlock("):me.index("function requestTable(")]
    # heightBlock used to render the curve and filter it to the offseasons that had happened.
    # It now prints the recorded height and nothing else, which withholds the future outright
    # rather than by filtering it - a stronger guarantee, so this asserts the PROPERTY (no part
    # of the future curve reaches the page) instead of the filter that used to deliver it. The
    # old assertion failed against code that had become safer, which is the wrong way round.
    # THREE NEGATIVE CHECKS AND NOTHING POSITIVE IS A TEST THAT CAN PASS ON AN EMPTY STRING.
    # `block` is a slice between two function names; move requestTable above heightBlock and the
    # slice is '', every `not in` is true, and this reports success while checking nothing. So
    # the slice is asserted non-empty and one positive fact is kept about what the block DOES.
    check("the height block was actually found", bool(block.strip()), True)
    check("it prints the recorded height", "formatHeight(character.height_inches)" in block)
    check("the player page never prints the future of the height curve",
          "curve[curve.length - 1]" not in block
          and "growthCurve(" not in block
          and "expectedAdultHeight(" not in block)
    check("and it derives his age rather than assuming one",
          "currentAge(character, cfg.current_season)" in me)


def test_tendency_interaction(results, base):
    """3pUsage is decided by confidence AND discipline together, not by either alone.

    The chucker (high confidence, low discipline) has to shoot far more than the same kid
    with the discipline answers flipped, and the pass-first one has to shoot far less.
    """
    chucker = value(results, base, "tendencies chucker")
    disciplined = value(results, base + 1, "tendencies disciplined")
    meek = value(results, base + 2, "tendencies meek")
    if None in (chucker, disciplined, meek):
        check("3pUsage responds to confidence and discipline together", False)
        return base + 3
    check("high confidence + low discipline shoots the most threes",
          chucker["3pUsage"] > disciplined["3pUsage"] > meek["3pUsage"],
          f"chucker {chucker['3pUsage']} > disciplined {disciplined['3pUsage']} "
          f"> meek {meek['3pUsage']}")
    check("discipline alone moves 3pUsage without touching confidence",
          chucker["3pUsage"] != disciplined["3pUsage"])
    check("a disciplined kid fouls less than a reckless one",
          meek["Fouling"] < chucker["Fouling"],
          f"{meek['Fouling']} vs {chucker['Fouling']}")
    return base + 3


# ---------------------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------------------


FLAT = 15


def hand_sheet(**over):
    """A flat, characterless fourteen year old, plus whatever you want to stand out."""
    sheet = {r: FLAT for r in [
        "InsideScoring", "JumpShot", "FtShot", "3pShot", "Handling", "Passing", "Quickness",
        "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "OReb", "DReb", "Jumping",
        "Strength", "Stamina"]}
    sheet["3pUsage"] = 25
    sheet["Fouling"] = 28
    sheet.update(over)
    return sheet


CLASSIFY_CASES = [
    ("a pure shooter is a Sharpshooter", "sharpshooter", "SG", hand_sheet(
        JumpShot=28, **{"3pShot": 30, "3pUsage": 62}, FtShot=26, InsideScoring=8, Strength=8)),
    ("handle and vision is a Playmaker", "playmaker", "PG", hand_sheet(
        Passing=30, Handling=29, Stealing=22, Quickness=24, Strength=7, OReb=6, Blocking=5)),
    ("blocks and boards is a Rim Protector", "rim_protector", "C", hand_sheet(
        Blocking=30, PostDefense=28, DReb=27, Jumping=25, Strength=22, Handling=5,
        **{"3pShot": 4, "3pUsage": 6})),
    ("a corner shooter who guards is a 3 & D Wing", "three_and_d", "SF", hand_sheet(
        PerimeterDefense=29, Stealing=24, Passing=8, Handling=8,
        **{"3pShot": 28, "3pUsage": 55})),
    ("a big who shoots it is a Stretch Big", "stretch_big", "PF", hand_sheet(
        JumpShot=25, DReb=24, Strength=22, Quickness=7, Handling=6, Stealing=6,
        **{"3pShot": 28, "3pUsage": 58})),
    ("a big who passes is a Point Forward", "point_forward", "SF", hand_sheet(
        Passing=29, Handling=24, DReb=24, InsideScoring=24, Strength=21,
        Stealing=8, Blocking=8, **{"3pUsage": 12})),
    ("strength and the offensive glass is a Big Man", "big_man", "C", hand_sheet(
        InsideScoring=28, Strength=28, OReb=27, PostDefense=25, DReb=23, Handling=5,
        Quickness=6, **{"3pShot": 4, "3pUsage": 5})),
    ("rim pressure with no jumper is a Slasher", "slasher", "SG", hand_sheet(
        InsideScoring=29, Quickness=27, Jumping=26, Handling=24, FtShot=20, Blocking=6,
        **{"3pShot": 5, "3pUsage": 10})),
    ("a sheet with no shape at all is a Glue Guy", "glue_guy", "SF", hand_sheet()),
]


def test_classify(results, base):
    bad = []
    for i, (label, want, _pos, _sheet) in enumerate(CLASSIFY_CASES):
        got = value(results, base + i, label)
        if got is None or got["id"] != want:
            bad.append(f"{label}: got {None if got is None else got['id']}, want {want}")
    check(f"classify() names all {len(CLASSIFY_CASES)} hand-built shapes correctly", not bad,
          "\n      " + "\n      ".join(bad) if bad else "")
    base += len(CLASSIFY_CASES)

    strong = value(results, base, "classify confidence, strong shape")
    flat = value(results, base + 1, "classify confidence, flat shape")
    ok = (strong is not None and flat is not None
          and strong["confidence"] > flat["confidence"])
    check("classify() is less confident about a shapeless sheet than a distinct one", ok,
          "" if ok else f"{strong} vs {flat}")
    has_runner = strong is not None and strong["runnerUp"]["id"] != strong["id"]
    check("classify() always names a different runner-up", has_runner)
    return base + 2


def test_classes_are_labels_only(consts):
    """A class must not carry a cap, a ceiling or a position gate. The whole point of the
    redesign is that the label constrains nothing."""
    forbidden = {"caps", "cap", "ceiling", "max", "signature", "locked"}
    bad = [f"{c['id']}.{k}" for c in consts["CLASSES"] for k in c if k in forbidden]
    check("no class definition carries a cap or a ceiling", not bad, str(bad))
    check("there are at least nine classes", len(consts["CLASSES"]) >= 9,
          str(len(consts["CLASSES"])))
    src = RULES_JS.read_text(encoding="utf-8")
    check("rules.js exports no archetype machinery any more",
          not re.search(r"export (const|function) (ARCHETYPES|CAP_DEFAULT|capsFor|capFor)\b", src))


# ---------------------------------------------------------------------------------------
# The height model: JS against Python
# ---------------------------------------------------------------------------------------


ROLL_BANDS = [6, 2, 4, 3, 8, 5, 0, 6, 6, 2, 3, 4, 5, 6, 2, 3, 4, 5, 2, 3, 4, 5, 2, 3,
              4, 5, 2, 3, 4, 5]

IDENTITIES = [
    ("Milo", "Trask", "Scarborough, ON", 8),
    ("Milo", "Trask", "Scarborough, ON", 9),      # one number apart
    ("milo", "  trask ", "scarborough, on", "8"),  # trimmed and lowercased to the first
    ("", "", "", ""),
    ("Ola", "Adeyemi", "Lagos", 0),
    ("René", "Façade", "Montréal, QC", 23),  # non-ASCII, to exercise the hash
]


HEIGHT_IDS = [
    "7c9f1a2e-0000-4000-8000-000000000001",
    "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
    "d4a1c8b0-1111-4222-9333-444455556666",
    "cv-outlook-sample-0",
    "",                       # the degenerate id
    "Milo Trask",             # a non-uuid, to exercise the hash
    "z" * 64,                 # a long one
]
HEIGHT_CASES = [(cid, start, genes)
                for cid in HEIGHT_IDS
                for start in (62, 70, 76, 80)
                for genes in (0, 18, 50, 73, 100)]


# Every build, across the legal height range, including the ends of it.
WEIGHT_CASES = [(inches, build_id)
                for inches in (60, 64, 68, 70, 72, 76, 80, 84, 88, 95)
                for build_id in ("wiry", "lean", "solid", "strong", "heavy")]


def test_height_agreement(results, base):
    """The JS and the Python must produce the same inches, year by year, for every case.

    This is the test the whole two-language model rests on: the commissioner writes the
    inches into league.dat from the Python, and the site promises a number from the JS. If
    they ever drift, the site is lying.
    """
    bad_seed, bad_curve, bad_expected, bad_outlook = [], [], [], []
    i = base
    for cid, start, genes in HEIGHT_CASES:
        js_seed = value(results, i, "heightSeed"); i += 1
        js_curve = value(results, i, "growthCurve"); i += 1
        js_expected = value(results, i, "expectedAdultHeight"); i += 1
        js_outlook = value(results, i, "heightOutlook"); i += 1
        if js_curve is None:
            continue
        py_seed = PY_GROWTH.height_seed(cid)
        py_curve = PY_GROWTH.growth_curve(cid, start, genes)
        py_expected = PY_GROWTH.expected_adult_height(start, genes)
        py_outlook = PY_GROWTH.height_outlook(start, genes)
        if js_seed != py_seed:
            bad_seed.append(f"{cid!r}: js {js_seed} != py {py_seed}")
        if [p["inches"] for p in js_curve] != [p["inches"] for p in py_curve]:
            bad_curve.append(f"{cid!r} {start}/{genes}: "
                             f"{[p['inches'] for p in js_curve]} != "
                             f"{[p['inches'] for p in py_curve]}")
        elif [p["hundredths"] for p in js_curve] != [p["hundredths"] for p in py_curve]:
            bad_curve.append(f"{cid!r} {start}/{genes}: hundredths differ")
        if js_expected != py_expected:
            bad_expected.append(f"{start}/{genes}: js {js_expected} != py {py_expected}")
        if js_outlook != py_outlook:
            bad_outlook.append(f"{start}/{genes}: {js_outlook} != {py_outlook}")

    check(f"the seed hash agrees across {len(HEIGHT_IDS)} ids", not bad_seed,
          "\n      " + "\n      ".join(bad_seed[:3]) if bad_seed else "")
    check(f"JS and Python growth curves are identical, inch by inch, over "
          f"{len(HEIGHT_CASES)} cases", not bad_curve,
          "\n      " + "\n      ".join(bad_curve[:3]) if bad_curve else "")
    check("the expected adult height agrees", not bad_expected, str(bad_expected[:3]))
    check("the projected range shown on the create page agrees", not bad_outlook,
          str(bad_outlook[:2]))
    return i


def test_weight_agreement(results, base):
    """The weight the browser shows and the weight the commissioner writes must be one number.

    Weight used to be derived in the site and NEVER WRITTEN to league.dat at all - the game
    played everybody at whatever the reserve slot he claimed weighed. That was invisible while
    the site was the only place a weight appeared. Now the builder has a slider and the
    commissioner writes the pounds in, and for everybody created before the slider it writes
    this derived fallback instead. If the two formulas drift, his card and his player disagree
    and nothing raises.
    """
    bad, i = [], base
    for inches, build_id in WEIGHT_CASES:
        js = value(results, i, "buildWeight"); i += 1
        py = PY_GROWTH.build_weight(inches, build_id)
        if js != py:
            bad.append(f"{inches}in {build_id}: js {js} != py {py}")
    check(f"JS and Python agree on the weight for {len(WEIGHT_CASES)} height/build pairs",
          not bad, "; ".join(bad[:4]))
    # and the derived number must sit inside the band the database will accept, or a character
    # made before the slider cannot be re-inserted by his own fallback
    outside = [f"{inches}in {build_id}: {PY_GROWTH.build_weight(inches, build_id)}"
               for inches, build_id in WEIGHT_CASES
               if abs(PY_GROWTH.build_weight(inches, build_id)
                      - round((inches - 60) * 4.6 + 96)) > 50]
    check("every derived weight is inside the check constraint in schema.sql", not outside,
          str(outside[:3]))
    return i


def test_height_model(results, base, consts):
    """The shape of the model: monotone, diminishing, no hard ceiling, and deterministic."""
    # the same four-call-per-case block test_height_agreement walked; the curve is second
    curves = []
    for n, (cid, start, genes) in enumerate(HEIGHT_CASES):
        c = value(results, base + n * 4 + 1, "growthCurve shape")
        if c is not None:
            curves.append((cid, start, genes, c))
    i = base + len(HEIGHT_CASES) * 4

    bad_monotone, bad_start, bad_diminish, bad_stall = [], [], [], []
    for cid, start, genes, c in curves:
        if c[0]["inches"] != start:
            bad_start.append(f"{cid!r} starts at {c[0]['inches']} not {start}")
        hs = [p["hundredths"] for p in c]
        if any(b < a for a, b in zip(hs, hs[1:])):
            bad_monotone.append(f"{cid!r} {start}/{genes} shrinks: {hs}")
        # growth in the back half of the curve is never more than in the front half
        first = hs[4] - hs[0]
        second = hs[-1] - hs[4]
        if second > first:
            bad_diminish.append(f"{cid!r} {start}/{genes}: {first} then {second}")
        # ...and it never actually stops: every offseason adds something
        if any(b == a for a, b in zip(hs, hs[1:])):
            bad_stall.append(f"{cid!r} {start}/{genes} stops growing: {hs}")
    check("nobody ever shrinks", not bad_monotone, str(bad_monotone[:3]))
    check("the curve starts at the height you picked", not bad_start, str(bad_start[:3]))
    check("growth diminishes: ages 18-23 never add more than 14-18", not bad_diminish,
          str(bad_diminish[:3]))
    check("the creep never reaches zero - every offseason still adds something",
          not bad_stall, str(bad_stall[:3]))

    # the last offseason still adds less than the first: gradual, not a cliff
    gradual = [1 for _cid, _s, _g, c in curves
               if (c[1]["hundredths"] - c[0]["hundredths"])
               > (c[-1]["hundredths"] - c[-2]["hundredths"])]
    check("the first offseason always outgrows the last", len(gradual) == len(curves),
          f"{len(gradual)} of {len(curves)}")

    # no hard ceiling: somewhere in this sample, somebody passes his own expectation
    over = [f"{cid} {start}/{genes}" for cid, start, genes, c in curves
            if c[-1]["hundredths"] > PY_GROWTH.expected_adult_hundredths(start, genes)]
    check("there is no hard ceiling - some players grow past their expectation",
          bool(over), f"({len(over)} of {len(curves)} did)")

    # and it is never guaranteed: somebody also falls short
    under = [1 for cid, start, genes, c in curves
             if c[-1]["hundredths"] < PY_GROWTH.expected_adult_hundredths(start, genes)]
    check("and it is never guaranteed - some fall short of it", bool(under),
          f"({len(under)} of {len(curves)} did)")

    # the band the create page shows is real: it has to be more than one inch wide
    wide = [1 for start, genes in [(64, 30), (70, 50), (76, 80)]
            if (PY_GROWTH.height_outlook(start, genes)["ceiling"]
                - PY_GROWTH.height_outlook(start, genes)["floor"]) >= 2]
    check("the projected height band is a real range, not decoration", len(wide) == 3,
          f"{wide}")

    # taller genes must mean a taller adult, all else equal
    tall = PY_GROWTH.expected_adult_height(70, 100)
    short = PY_GROWTH.expected_adult_height(70, 0)
    check("height_genes is the whole lever: 0 to 100 is seven inches of adult height",
          tall - short == 7, f"{short} -> {tall}")

    # past the last offseason he stops growing rather than the function blowing up
    end = consts["GROWTH_END_AGE"]
    at_end = PY_GROWTH.height_at_age("some-id", 70, 50, end)
    at_30 = PY_GROWTH.height_at_age("some-id", 70, 50, 30)
    check("asking about a thirty year old returns his finished height, not an error",
          at_end == at_30, f"{at_end} vs {at_30}")
    check("an offseason past the end adds nothing",
          PY_GROWTH.grew_this_offseason("some-id", 70, 50, 30) == 0)
    return i


# ---------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------


# (character, height now, age) - a fourteen-year-old growing up, including one with no weight
# of his own (made before the column existed) and one at each end of the slider.
GROWN_CASES = [
    ({"weight_lbs": 177, "height_inches": 80, "build": "lean"}, 80, 14),
    ({"weight_lbs": 177, "height_inches": 80, "build": "lean"}, 82, 16),
    ({"weight_lbs": 177, "height_inches": 80, "build": "lean"}, 86, 18),
    ({"weight_lbs": 177, "height_inches": 80, "build": "lean"}, 86, 25),
    ({"weight_lbs": 117, "height_inches": 66, "build": "wiry"}, 66, 14),
    ({"weight_lbs": 117, "height_inches": 66, "build": "wiry"}, 71, 17),
    ({"weight_lbs": 117, "height_inches": 66, "build": "wiry"}, 78, 23),
    ({"weight_lbs": None, "height_inches": 70, "build": "solid"}, 70, 14),
    ({"weight_lbs": None, "height_inches": 70, "build": "solid"}, 75, 19),
    ({"weight_lbs": None, "height_inches": 72, "build": None}, 79, 26),
]


def test_weight_range(results, base, consts):
    """The band the slider offers, and the number that ends up in the save.

    test_weight_agreement above pins buildWeight() itself across both languages. This is the
    half that decides what a person can actually choose: the range is what validateBuild()
    accepts, and whatever it accepts is what reaches league.dat.
    """
    bad_range = []
    i = base
    for height, build_id in WEIGHT_CASES:
        js_range = value(results, i, "weightRange"); i += 1
        py_weight = PY_GROWTH.build_weight(height, build_id)
        if js_range is not None and (js_range["base"] != py_weight
                                     or js_range["low"] != py_weight - consts["WEIGHT_SPREAD"]
                                     or js_range["high"] != py_weight + consts["WEIGHT_SPREAD"]):
            bad_range.append(f"{height}in {build_id}: {js_range} around {py_weight}")
    check("the slider's range is the suggestion plus or minus the spread",
          not bad_range, str(bad_range[:2]))

    # The Python half alone: what actually reaches the save file.
    bad_save = []
    if PY_GROWTH.weight_for_save({"weight_lbs": 206, "height_inches": 80, "build": "lean"}) != 206:
        bad_save.append("a stored weight is not used as-is")
    fallback = PY_GROWTH.weight_for_save({"height_inches": 80, "build": "lean"})
    if fallback != PY_GROWTH.build_weight(80, "lean"):
        bad_save.append(f"a character with no weight got {fallback}")
    if PY_GROWTH.weight_for_save({"weight_lbs": 9000}) != 400:
        bad_save.append("an absurd weight was not clamped")
    if PY_GROWTH.weight_for_save({"weight_lbs": None, "height_inches": None,
                                  "build": None}) <= 0:
        bad_save.append("a character with nothing at all got a nonsense weight")
    check("the weight written into league.dat is his own, or the one his build suggests",
          not bad_save, str(bad_save))
    return i


def test_weight_after_fourteen(results, base, consts):
    """The curve he grows on, and the promise that his own choice survives all of it."""
    bad_frame, bad_now = [], []
    i = base
    for character, height_now, age in GROWN_CASES:
        js_frame = value(results, i, "frameWeight"); i += 1
        js_now = value(results, i, "weightAt"); i += 1
        py_frame = PY_GROWTH.frame_weight(height_now, age)
        py_now = PY_GROWTH.weight_at(character, height_now, age)
        if js_frame != py_frame:
            bad_frame.append(f"{height_now}in at {age}: js {js_frame} != py {py_frame}")
        if js_now != py_now:
            bad_now.append(f"{character} at {height_now}in/{age}: js {js_now} != py {py_now}")
    check(f"JS and Python agree on the frame curve over {len(GROWN_CASES)} cases",
          not bad_frame, "; ".join(bad_frame[:3]))
    check("JS and Python agree on what he weighs after he has grown",
          not bad_now, "; ".join(bad_now[:2]))

    # The shape of it, in Python, where the offseason reads it.
    him = {"weight_lbs": 177, "height_inches": 80, "build": "lean"}
    light = {"weight_lbs": 152, "height_inches": 80, "build": "lean"}
    bad = []
    if PY_GROWTH.weight_at(him, 80, PY_GROWTH.START_AGE) != 177:
        bad.append("at fourteen he does not weigh what he chose")
    series = [PY_GROWTH.weight_at(him, 80 + a - 14, a) for a in range(14, 24)]
    if any(b < a for a, b in zip(series, series[1:])):
        bad.append(f"weight goes down as he grows up: {series}")
    if PY_GROWTH.weight_at(him, 84, 18) - PY_GROWTH.weight_at(light, 84, 18) != 25:
        bad.append("the slider's choice stopped being worth what it was at fourteen")
    if PY_GROWTH.weight_at(him, 80, 19) != PY_GROWTH.weight_at(him, 80, 25):
        bad.append("he keeps filling out past eighteen")
    if PY_GROWTH.weight_at(him, 80, 16) <= PY_GROWTH.weight_at(him, 80, 14):
        bad.append("a character who did not grow an inch does not fill out either")
    if PY_GROWTH.weight_at({"weight_lbs": 400, "height_inches": 95, "build": "heavy"},
                           95, 25) > PY_GROWTH.WEIGHT_MAX:
        bad.append("the int16 is not clamped")
    check("he grows into the frame without ever stopping being himself", not bad, str(bad))

    # And the one that matters in the game: does he put weight on at the rate the league's own
    # players carry it? The SHAPE is what has to be right, not the level - a constant added to
    # frame_weight cancels out of weight_at entirely, because the offset subtracts the same
    # curve at fourteen that it adds back at every later age. So this pins the slope and the
    # maturation, which are the two things that do not cancel.
    #
    # Measured off the pro save: mean height 71.9in -> 169.8 lbs and 83.5in -> 229.7 lbs at a
    # mean age of 26, which is 5.4 lbs an inch across 516 men.
    shape = []
    per_inch = (PY_GROWTH.frame_weight(83, 26) - PY_GROWTH.frame_weight(72, 26)) / 11
    if abs(per_inch - 5.4) > 0.5:
        shape.append(f"{per_inch:.2f} lbs an inch, the save says 5.4")
    filled = PY_GROWTH.frame_weight(76, 18) - PY_GROWTH.frame_weight(76, 14)
    if filled != PY_GROWTH.MATURATION_LBS * 4:
        shape.append(f"four years of filling out is {filled} lbs")
    check("he puts weight on at the rate the league's own players carry it",
          not shape, str(shape))
    return i


def test_build_validation(results, base):
    labels = ["a complete build is accepted",
              "an unanswered question is rejected",
              "no career goal is rejected",
              "a nameless character is rejected",
              "a 5'6\" center is rejected",
              "a jersey number of 100 is rejected",
              "no hometown is rejected",
              "a hand-edited stat sheet is rejected",
              # the weight goes straight into league.dat, so the form is the only thing
              # standing between the slider's range and the save file
              "a weight above the slider's range is rejected",
              "a weight below the slider's range is rejected",
              "no weight at all is rejected"]
    wants = [True, False, False, False, False, False, False, False, False, False, False]
    bad = []
    for i, (label, want) in enumerate(zip(labels, wants)):
        got = value(results, base + i, label)
        if got is None or got["ok"] != want:
            bad.append(f"{label}: ok={None if got is None else got['ok']} "
                       f"errors={[] if got is None else got['errors'][:2]}")
        else:
            check(label, True, "" if want else f"({got['errors'][0]})")
    for line in bad:
        check(line.split(":")[0], False, line)
    return base + len(labels)


def test_upgrade_validation(results, base):
    cases = [
        ("an affordable upgrade is allowed", True),
        ("an upgrade past the potential is refused", False),
        ("an upgrade you cannot afford is refused", False),
        ("queued requests reserve their cost", False),
        ("a potential can be bought all the way to 100", True),
        ("Fouling still cannot be bought", False),
    ]
    for i, (label, want) in enumerate(cases):
        got = value(results, base + i, label)
        ok = got is not None and got["ok"] == want
        check(label, ok, "" if ok else f"got {got}")
    base += len(cases)

    cheap = value(results, base, "a favoured rating costs less")
    dear = value(results, base + 1, "an unfavoured rating costs more")
    ok = cheap is not None and dear is not None and cheap["cost"] < dear["cost"]
    check("the growth bias really does change what a point costs", ok,
          "" if ok else f"{cheap} vs {dear}")
    return base + 2


def test_limits(results, base):
    got = value(results, base, "canCreateAnother")
    check("two live characters means no third", got is not None and got["ok"] is False,
          f"got {got}")
    got = value(results, base + 1, "canCreateAnother with a retired one")
    check("a retired character does not count against the limit",
          got is not None and got["ok"] is True, f"got {got}")
    got = value(results, base + 2, "formatHeight")
    check("height formats as feet and inches", got == "6'4\"", f"got {got}")
    got = value(results, base + 3, "buildWeight")
    check("a 6'2\" heavy fourteen year old weighs something plausible",
          got is not None and 150 <= got <= 230, f"got {got}")
    return base + 4


# ---------------------------------------------------------------------------------------


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
    test_rules_has_no_imports()
    test_page_ids_exist()
    test_pages_wire_up()
    test_no_dark_theme()
    test_no_table_wide_write_grants()
    test_anon_key_only()
    test_new_columns_are_granted()
    test_schema_mirror()

    consts = run_js([{"op": "constants"}])[0]["value"]
    inputs = sample_inputs(consts)

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

    bias_base = len(calls)
    for args in [(10, 100), (10, 85), (10, 115), (1, 85), (3, 85), (40, 90), (10, 40), (10, 400)]:
        calls.append({"op": "applyBias", "args": list(args)})
    calls.append({"op": "biasedUpgradeCost", "args": [48, 4, "rating", 100]})

    # --- determinism -------------------------------------------------------------------
    det_base = len(calls)
    a = sample_answers(consts["QUIZ"], 5)
    opts = {"build": "solid", "goal": "ring", "summer": "defense"}
    calls.append({"op": "traitsFromQuiz", "args": [a, opts]})
    calls.append({"op": "traitsFromQuiz", "args": [dict(a), dict(opts)]})
    who = {"firstName": "Milo", "lastName": "Trask", "hometown": "Scarborough, ON",
           "jersey": 8, "position": "SF", "heightInches": 72, "answers": a,
           "goal": "ring", "summer": "defense", "build": "solid"}
    calls.append({"op": "deriveCharacter", "args": [who]})
    calls.append({"op": "deriveCharacter", "args": [dict(who)]})
    calls.append({"op": "deriveCharacter", "args": [
        {**who, "answers": dict(reversed(list(a.items())))}]})
    calls.append({"op": "deriveCharacter", "args": [
        {"position": "SF", "heightInches": 72, "answers": {}}]})

    # --- the roll ------------------------------------------------------------------------
    roll_base = len(calls)
    calls.append({"op": "rollSwings", "args": [123456789, ROLL_BANDS]})
    for first, last, town, jersey in IDENTITIES:
        calls.append({"op": "identitySeed", "args": [
            {"firstName": first, "lastName": last, "hometown": town, "jersey": jersey}]})
    calls.append({"op": "deriveCharacter", "args": [who]})
    calls.append({"op": "deriveCharacter", "args": [dict(who)]})
    calls.append({"op": "deriveCharacter", "args": [{**who, "lastName": "Trasq"}]})
    # emphatic about shooting vs. nothing in particular
    calls.append({"op": "ratingBands", "args": [
        {**{t: 50 for t in consts["TRAITS"]}, "touch": 100, "confidence": 90, "discipline": 15}]})
    calls.append({"op": "ratingBands", "args": [{t: 50 for t in consts["TRAITS"]}]})
    calls.append({"op": "scoutingWord", "args": ["JumpShot", [12, 18]]})

    # --- the sampled sheets --------------------------------------------------------------
    sheet_base = len(calls)
    for row in inputs:
        calls.append({"op": "deriveCharacter", "args": [row]})

    # --- tendencies, three deliberate personalities --------------------------------------
    tend_base = len(calls)
    calls.append({"op": "tendencies", "args": ["SG", {"confidence": 95, "discipline": 10,
                                                      "touch": 70, "motor": 50, "frame": 50}]})
    calls.append({"op": "tendencies", "args": ["SG", {"confidence": 95, "discipline": 90,
                                                      "touch": 70, "motor": 50, "frame": 50}]})
    calls.append({"op": "tendencies", "args": ["SG", {"confidence": 10, "discipline": 90,
                                                      "touch": 30, "motor": 50, "frame": 50}]})

    # --- classify ------------------------------------------------------------------------
    class_base = len(calls)
    for _label, _want, pos, sheet in CLASSIFY_CASES:
        calls.append({"op": "classify", "args": [sheet, pos]})
    calls.append({"op": "classify", "args": [CLASSIFY_CASES[0][3], "SG"]})
    calls.append({"op": "classify", "args": [hand_sheet(), "SF"]})

    # --- height --------------------------------------------------------------------------
    height_base = len(calls)
    for cid, start, genes in HEIGHT_CASES:
        calls.append({"op": "heightSeed", "args": [cid]})
        calls.append({"op": "growthCurve", "args": [cid, start, genes]})
        calls.append({"op": "expectedAdultHeight", "args": [start, genes]})
        calls.append({"op": "heightOutlook", "args": [start, genes]})

    # --- weight --------------------------------------------------------------------------
    weight_base = len(calls)
    for inches, build_id in WEIGHT_CASES:
        calls.append({"op": "buildWeight", "args": [inches, build_id]})

    # --- validateBuild -------------------------------------------------------------------
    good_answers = sample_answers(consts["QUIZ"], 3)
    identity = {"firstName": "Milo", "lastName": "Trask", "hometown": "Scarborough, ON",
                "jersey": 8}
    seed = run_js([{"op": "deriveCharacter", "args": [{
        **identity, "position": "SG", "heightInches": 70, "answers": good_answers,
        "goal": "bucket", "summer": "jumper", "build": "lean"}]}])
    derived = seed[0]["value"]

    def build(**over):
        b = {**identity, "position": "SG", "heightInches": 70, "build": "lean",
             # what the slider would be sitting on, untouched, for that height and build
             "weightLbs": PY_GROWTH.build_weight(70, "lean"),
             "answers": dict(good_answers), "goal": "bucket", "summer": "jumper",
             "ratings": dict(derived["ratings"]), "potentials": dict(derived["potentials"])}
        b.update(over)
        return b

    unanswered = dict(good_answers)
    unanswered.pop(consts["QUIZ"][0]["id"])
    tampered = dict(derived["ratings"])
    tampered["3pShot"] = 80

    build_base = len(calls)
    for b in (build(),
              build(answers=unanswered),
              build(goal=None),
              build(firstName="", lastName=""),
              build(position="C", heightInches=66),
              build(jersey=100),
              build(hometown=""),
              build(ratings=tampered),
              build(weightLbs=PY_GROWTH.build_weight(70, "lean") + consts["WEIGHT_SPREAD"] + 1),
              build(weightLbs=PY_GROWTH.build_weight(70, "lean") - consts["WEIGHT_SPREAD"] - 1),
              build(weightLbs=None)):
        calls.append({"op": "validateBuild", "args": [b]})

    # --- validateUpgrade -----------------------------------------------------------------
    upgrade_base = len(calls)
    char = {"ratings": dict(derived["ratings"]), "potentials": dict(derived["potentials"]),
            "growth_bias": dict(derived["bias"]), "points_available": 10}
    calls.append({"op": "validateUpgrade", "args": [char, "3pShot", 3, "rating", 0]})
    pot_gap = derived["potentials"]["3pShot"] - derived["ratings"]["3pShot"]
    calls.append({"op": "validateUpgrade", "args": [char, "3pShot", pot_gap + 1, "rating", 0]})
    calls.append({"op": "validateUpgrade", "args": [
        {**char, "points_available": 1}, "3pShot", 3, "rating", 0]})
    calls.append({"op": "validateUpgrade", "args": [char, "3pShot", 3, "rating", 9]})
    calls.append({"op": "validateUpgrade", "args": [
        {**char, "points_available": 400}, "3pShot",
        100 - derived["potentials"]["3pShot"], "potential", 0]})
    calls.append({"op": "validateUpgrade", "args": [char, "Fouling", 1, "rating", 0]})
    # the same ten steps at each end of the bias window
    flat_char = {"ratings": {r: 20 for r in consts["RATINGS"]},
                 "potentials": {r: 60 for r in consts["POTENTIAL_RATINGS"]},
                 "points_available": 500}
    calls.append({"op": "validateUpgrade", "args": [
        {**flat_char, "growth_bias": {"Passing": 85}}, "Passing", 20, "rating", 0]})
    calls.append({"op": "validateUpgrade", "args": [
        {**flat_char, "growth_bias": {"Passing": 115}}, "Passing", 20, "rating", 0]})

    limit_base = len(calls)
    calls.append({"op": "canCreateAnother", "args": [
        [{"status": "active"}, {"status": "pending"}], 2]})
    calls.append({"op": "canCreateAnother", "args": [
        [{"status": "active"}, {"status": "retired"}], 2]})
    calls.append({"op": "formatHeight", "args": [76]})
    calls.append({"op": "buildWeight", "args": [74, "heavy"]})

    # --- the slider's range, and weight after fourteen -------------------------------------
    range_base = len(calls)
    for height, build_id in WEIGHT_CASES:
        calls.append({"op": "weightRange", "args": [height, build_id]})

    grown_base = len(calls)
    for character, height_now, age in GROWN_CASES:
        calls.append({"op": "frameWeight", "args": [height_now, age]})
        calls.append({"op": "weightAt", "args": [character, height_now, age]})

    results = run_js(calls)

    i = test_cost_curve(results, 0)
    i = test_documented_examples(results, i)
    i = test_affordable(results, i)
    test_bias_arithmetic(results, bias_base, consts)
    test_vocabulary(consts)
    test_codec_agreement(consts)
    test_quiz_shape(consts)
    test_no_dominant_answer(consts)
    test_quiz_determinism(results, det_base)
    test_the_roll(results, roll_base, consts)
    test_nothing_leaks_before_signing()
    test_derived_sheets(results, sheet_base, consts, inputs)
    test_tendency_interaction(results, tend_base)
    test_classify(results, class_base)
    test_classes_are_labels_only(consts)
    test_height_agreement(results, height_base)
    test_height_model(results, height_base, consts)
    test_weight_agreement(results, weight_base)
    test_build_validation(results, build_base)
    test_weight_range(results, range_base, consts)
    test_weight_after_fourteen(results, grown_base, consts)
    test_upgrade_validation(results, upgrade_base)
    test_limits(results, limit_base)

    print()
    print(f"cost curve as rules.js describes it: {consts['describeCurve']}")
    print(f"{len(consts['QUIZ'])} quiz questions, "
          f"{sum(len(q['answers']) for q in consts['QUIZ'])} answers, "
          f"{len(consts['TRAITS'])} traits, {len(consts['CLASSES'])} classes, "
          f"{len(consts['RATINGS'])} ratings")
    print(f"{len(inputs)} sampled builds, {len(HEIGHT_CASES)} height cases and "
          f"{len(WEIGHT_CASES) + len(GROWN_CASES)} weight cases checked against "
          f"commissioner/growth.py")
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("all rules tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
