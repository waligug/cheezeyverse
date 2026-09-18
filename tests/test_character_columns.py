"""Every character field the commissioner reads must be one the store actually selects.

CHARACTER_COLUMNS is an explicit list rather than `*`, deliberately: it keeps claimed_slot out
of the public export and documents what the app depends on. The cost is that a field left out of
it comes back as None, and None is indistinguishable from the field genuinely being empty.

That is not a theoretical failure. Four fields were missing at various points in one day:

  league_player_ids   a character looked as though he had never been placed in a league
  level_history       his career page had no history
  declared            the offseason found nobody eligible for the draft and reported a clean run
  college_years       same
  traits              height_genes read as None, so apply_growth skipped EVERY character and
                      reported "0 grew" - height growth simply would not have happened, ever,
                      to anybody, and it looked like a valid result

Each one was found by accident, and each one failed silently in the direction of "nothing to do".
This checks the whole set at once, by reading what the code actually asks for.

Needs Supabase configured. It reads the column list from PostgREST's schema description, so
it works on an empty universe - which is exactly when the old row-sampling version skipped.

    python tests/test_character_columns.py
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store  # noqa: E402

# Names that hold a character dict in the code that reads it.
HOLDERS = r"(?:c|ch|character|row|x|mover|found|holder)"
READ = [re.compile(rf"\b{HOLDERS}\.get\(\"([a-z_]+)\""), re.compile(rf"\b{HOLDERS}\[\"([a-z_]+)\"\]")]


def fields_the_code_reads():
    used = set()
    for path in [*(ROOT / "commissioner").rglob("*.py"), *(ROOT / "tools").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        for pattern in READ:
            used.update(pattern.findall(text))
    return used


def table_columns():
    """The characters table's columns, from PostgREST's own schema description.

    Read from the schema rather than from a row, because a row is not always there. The first
    version of this test sampled one character and SKIPPED when the table was empty - which is
    the state right after every rebuild, and the state the universe is in the moment before the
    first person signs up. It exited 0 while checking nothing, and a skip that exits 0 is
    indistinguishable from a pass to anything reading exit codes. A guard written against
    silent failure should not have one.
    """
    d = store._request("GET", "/rest/v1/")
    props = ((d.get("definitions") or {}).get("characters") or {}).get("properties") or {}
    return set(props)


def main():
    try:
        columns = table_columns()
    except Exception as exc:
        print(f"FAIL  could not read the characters schema from Supabase ({exc})")
        return 1
    if not columns:
        print("FAIL  Supabase described no columns for `characters`")
        return 1
    selected = set(re.findall(r"[a-z_]+", store.CHARACTER_COLUMNS))
    used = fields_the_code_reads()

    # Only names that are really columns; the rest are locals and other dicts.
    missing = sorted((used & columns) - selected)

    print(f"{len(columns)} columns on characters, {len(selected & columns)} selected, "
          f"{len(used & columns)} read by the code")
    for f in missing:
        print(f"  FAIL  the code reads {f!r}, which CHARACTER_COLUMNS does not select - "
              "it will always read as None")
    if missing:
        print(f"\n{len(missing)} field(s) will silently be None")
        return 1
    print("every character field the code reads is selected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
