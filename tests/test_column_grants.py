"""Every character column the site reads or writes must be one the schema grants it.

`weight_lbs` is why this exists. Adding a column to `public.characters` means adding its name
to FOUR separate lists, and each one fails differently when it is forgotten:

  create table / alter table   the column does not exist at all; the insert is rejected
  grant insert (...)           a 403 on creation, loud but confusing - RLS is not the problem
  grant select (...)           silent. The field reads back `undefined` on every page, and the
                               signed card cheerfully prints "about undefined lbs"
  CHARACTER_COLUMNS            silent, twice: site/js/supabase.js for the website and
                               commissioner/store.py for the commissioner. The website never
                               says select('*') precisely because a column without a grant
                               would 403, so the list is the only thing that asks for it.

Only the third and fourth are silent, and those are the ones a person cannot see in testing
unless they happen to look at the one card that shows the field. So this reads all four out of
the source and compares them - no Supabase connection, no network, nothing deployed.

What it CANNOT tell you: whether the schema in supabase/schema.sql has actually been run
against the live project. Editing that file deploys nothing. tests/test_character_columns.py
is the one that asks the live database, and it needs credentials.

    python tests/test_column_grants.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "supabase" / "schema.sql"
SITE_STORE = ROOT / "site" / "js" / "supabase.js"
COMMISSIONER_STORE = ROOT / "commissioner" / "store.py"

failures = []


def check(name, cond, detail=""):
    print(f'{"PASS" if cond else "FAIL"}  {name}' + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def schema_text():
    return SCHEMA.read_text(encoding="utf-8")


def table_columns(sql):
    """Every column `public.characters` has, from the create table and the alter tables."""
    body = re.search(r"create table if not exists public\.characters \((.*?)\n\);", sql, re.S)
    if not body:
        raise SystemExit("could not find the characters table in schema.sql")
    cols = set()
    for line in body.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        m = re.match(r'"?([a-z_]+)"?\s+(uuid|text|int|bigint|boolean|jsonb|date|timestamptz)', line)
        if m:
            cols.add(m.group(1))
    cols |= set(re.findall(r"add column if not exists\s+\"?([a-z_]+)\"?", sql))
    return cols


def granted(sql, verb):
    """The column list from `grant <verb> (...) on public.characters`."""
    m = re.search(rf"grant\s+{verb}\s+\((.*?)\)\s*\n?\s*on public\.characters", sql, re.S)
    if not m:
        raise SystemExit(f"could not find the {verb} grant on public.characters")
    inner = re.sub(r"--[^\n]*", "", m.group(1))
    return {c.strip().strip('"') for c in inner.split(",") if c.strip()}


def js_selected():
    """CHARACTER_COLUMNS in site/js/supabase.js."""
    text = SITE_STORE.read_text(encoding="utf-8")
    m = re.search(r"export const CHARACTER_COLUMNS = \[(.*?)\]\.join", text, re.S)
    if not m:
        raise SystemExit("could not find CHARACTER_COLUMNS in site/js/supabase.js")
    return set(re.findall(r"'([a-z_]+)'", re.sub(r"//[^\n]*", "", m.group(1))))


def js_inserted():
    """The keys createCharacter() actually sends, read out of the row literal."""
    text = SITE_STORE.read_text(encoding="utf-8")
    m = re.search(r"export async function createCharacter\(build\) \{.*?const row = \{(.*?)\n  \};",
                  text, re.S)
    if not m:
        raise SystemExit("could not find the insert row in createCharacter()")
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return set(re.findall(r"^\s{4}([a-z_]+):", body, re.M))


def py_selected():
    """CHARACTER_COLUMNS in commissioner/store.py - one string in several pieces."""
    text = COMMISSIONER_STORE.read_text(encoding="utf-8")
    m = re.search(r"CHARACTER_COLUMNS = \((.*?)\n\)", text, re.S)
    if not m:
        raise SystemExit("could not find CHARACTER_COLUMNS in commissioner/store.py")
    joined = "".join(re.findall(r'"([^"]*)"', re.sub(r"#[^\n]*", "", m.group(1))))
    return {c for c in joined.split(",") if c}


def main():
    sql = schema_text()
    columns = table_columns(sql)
    can_select = granted(sql, "select")
    can_insert = granted(sql, "insert")
    site_reads = js_selected()
    site_writes = js_inserted()
    commissioner_reads = py_selected()

    check("the characters table parsed", len(columns) > 20, f"{len(columns)} columns")

    missing = sorted(site_reads - can_select)
    check("every column the website selects is granted to anon/authenticated",
          not missing, f"not granted: {missing}")

    missing = sorted(site_writes - can_insert)
    check("every column the website inserts is grant insert'able",
          not missing, f"not granted: {missing}")

    missing = sorted((site_reads | site_writes | commissioner_reads) - columns)
    check("every column either store asks for exists on the table",
          not missing, f"not on the table: {missing}")

    # claimed_slot is deliberately one-sided: the commissioner needs it, the public must not
    # see it, and characters_for_export() strips it back out again.
    leaked = sorted(({"claimed_slot"} & (site_reads | can_select)))
    check("claimed_slot is still not readable by the website", not leaked, str(leaked))

    # Named columns, and the lists each one has to be in. The commissioner is listed only
    # where it genuinely reads the column: expected_adult_height it recomputes from height and
    # genes (see commissioner/growth.py), so its absence there is correct, not an oversight.
    # weight_lbs is the opposite case - nothing can recompute a number a person chose.
    named = {
        "weight_lbs": ("table", "select grant", "insert grant", "website select",
                       "commissioner select"),
        "build": ("table", "select grant", "insert grant", "website select",
                  "commissioner select"),
        "height_inches": ("table", "select grant", "insert grant", "website select",
                          "commissioner select"),
        "traits": ("table", "select grant", "insert grant", "website select",
                   "commissioner select"),
        "expected_adult_height": ("table", "select grant", "insert grant", "website select"),
    }
    lists = {"table": columns, "select grant": can_select, "insert grant": can_insert,
             "website select": site_reads, "commissioner select": commissioner_reads}
    for col, needed in named.items():
        absent = [where for where in needed if col not in lists[where]]
        check(f"{col} is in every list that has to carry it", not absent, str(absent))

    print()
    print(f"{len(columns)} columns on public.characters; website selects {len(site_reads)}, "
          f"inserts {len(site_writes)}; commissioner selects {len(commissioner_reads)}")
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        return 1
    print("every character column lines up across schema.sql and both stores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
