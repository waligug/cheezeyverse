"""No tracked text file may contain a stray control character.

This exists because it has happened twice, both times from a scripted edit rather than typing:

  * `site/config.js` got a literal 0x01 byte, because a Python replacement string contained
    `\\1` outside a raw string;
  * `docs/SERVER.md` got a literal 0x08 (backspace), because a path containing `\\bin` was
    written through a non-raw string.

Neither is visible. The Markdown one renders as `GitHubCLIin` - a path that silently does not
exist - and the JavaScript one broke a file that still looked perfect in every editor. A byte
nobody can see is exactly the kind of damage worth spending a test on.

Tabs, newlines and carriage returns are fine. Everything else below 0x20 is not.

    python tests/test_no_control_chars.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWED = {0x09, 0x0A, 0x0D}          # tab, newline, carriage return
TEXT = {".py", ".js", ".mjs", ".md", ".html", ".htm", ".css", ".json",
        ".sql", ".txt", ".csv", ".bat", ".yml", ".yaml", ".gitignore"}


def tracked_files():
    """Tracked files, AND new ones not yet added - which is where the damage actually happens.

    This checked `git ls-files` alone and therefore passed, cleanly, on a source file that had a
    literal 0x08 in it - because the file was new and not yet committed. That is precisely the
    wrong moment to stop looking: a file is most likely to be written by a script on the run
    that creates it, and if the byte survives to the commit it is then tracked, invisible, and
    blamed on something else. The third occurrence of this bug was found by reading bytes by
    hand, with this test reporting no problems.

    `--exclude-standard` keeps .gitignore honoured, so backups, saves and .env stay out.
    """
    paths = []
    for args in (["git", "ls-files"],
                 ["git", "ls-files", "--others", "--exclude-standard"]):
        out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        if out.returncode:
            sys.exit("not a git repository, or git is unavailable")
        paths += [ROOT / line for line in out.stdout.splitlines() if line.strip()]
    return paths


def main():
    checked, problems = 0, []
    for path in tracked_files():
        if path.suffix.lower() not in TEXT or not path.exists():
            continue
        checked += 1
        raw = path.read_bytes()
        for i, byte in enumerate(raw):
            if byte < 0x20 and byte not in ALLOWED:
                line = raw[:i].count(b"\n") + 1
                context = raw[max(0, i - 30):i + 30].decode("utf-8", "replace").replace("\n", " ")
                problems.append(
                    f"{path.relative_to(ROOT)}:{line} has byte 0x{byte:02X}  ...{context}...")
                break                  # one report per file is enough to go and look

    print(f"checked {checked} tracked text file(s) for invisible control characters")
    for row in problems:
        print(f"  FAIL  {row}")
    if problems:
        print(f"\n{len(problems)} file(s) contain a character nobody can see")
        return 1
    print("no stray control characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
