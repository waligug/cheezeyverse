"""Every site script must actually parse as an ES module, and its imports must exist.

`node --check foo.js` is not enough and it is worse than useless, because it exits 0. Node
decides how to parse a `.js` file by sniffing it, and under that sniffing an outright syntax
error inside an import list - `import { a,, b } from './x.js'` - passes the check silently.
Three pages shipped in exactly that state for a few minutes on 2026-09-17 and the only symptom
was a blank page: no console message anybody would see, nothing in the terminal, nothing in git.

Copying each file to `.mjs` forces module parsing, which catches it.

The second half is the one that actually bit: a page can parse perfectly and still die at load
with "does not provide an export named X" if it imports something the module does not export.
That is a link error, not a syntax error, so no parser will find it. This walks the imports.

    python tests/test_site_syntax.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"

IMPORT = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"](\.[^'\"]*)['\"]", re.S)
EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)",
                    re.M)


def js_files():
    return sorted([*SITE.glob("*.js"), *(SITE / "js").glob("*.js")])


def parses(path, tmp):
    """Node, forced to treat it as a module."""
    copy = Path(tmp) / (path.stem + ".mjs")
    shutil.copy2(path, copy)
    out = subprocess.run(["node", "--check", str(copy)], capture_output=True, text=True)
    if out.returncode == 0:
        return None
    lines = [ln for ln in (out.stderr or out.stdout).splitlines() if ln.strip()]
    return " / ".join(lines[:3])


def exported_names(path):
    text = path.read_text(encoding="utf-8")
    names = set(EXPORT.findall(text))
    for block in re.findall(r"^export\s*\{([^}]*)\}", text, re.M):
        for part in block.split(","):
            part = part.strip()
            if not part:
                continue
            names.add(part.split(" as ")[-1].strip())
    return names


def main():
    files = js_files()
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        for path in files:
            why = parses(path, tmp)
            if why:
                problems.append(f"{path.relative_to(ROOT)} does not parse as a module: {why}")

    for path in files:
        text = path.read_text(encoding="utf-8")
        for names, target in IMPORT.findall(text):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT)} imports {target}, which does not exist")
                continue
            have = exported_names(resolved)
            for raw in names.split(","):
                want = raw.strip().split(" as ")[0].strip()
                if not want:
                    continue
                if want not in have:
                    problems.append(
                        f"{path.relative_to(ROOT)} imports {want!r} from {target}, "
                        "which does not export it")

    print(f"checked {len(files)} site script(s): module syntax and every named import")
    for row in problems:
        print(f"  FAIL  {row}")
    if problems:
        print(f"\n{len(problems)} problem(s)")
        return 1
    print("every site script parses as a module and every import resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
