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

Needs Supabase configured: the live table is what says which names are columns at all.

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


def main():
    try:
        sample = store._table("characters", {"select": "*", "limit": "1"})
    except Exception as exc:
        print(f"skipped: could not reach Supabase ({exc})")
        return 0
    if not sample:
        print("skipped: no character exists, so the column set cannot be read")
        return 0

    columns = set(sample[0])
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
