"""_activate_pending returns the same shape from every exit, and writes nothing by itself.

This exists because of a real failure, in front of the owner, on the first sim after the change
that introduced the third value: the "nobody is pending" early return still handed back two, so
every ordinary week - the common case, since characters are created rarely - died at

    activated, expect_a, activations = _activate_pending(...)
    ValueError: not enough values to unpack (expected 3, got 2)

The function is hard to call in full (it needs a save, a manifest and a store), which is exactly
why the one path that needs none of that went untested. Both things checked here need neither:
the early return is called for real, and every other `return` is read off the source.

    python tests/test_activate_returns.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import simweek  # noqa: E402

SOURCE = ROOT / "commissioner" / "simweek.py"


class _NoPending:
    """A store with nobody waiting - and which fails the test if anything else is asked of it."""

    def pending_characters(self):
        return []

    def __getattr__(self, name):
        raise AssertionError(f"_activate_pending touched the store: {name}()")


def main():
    said = []
    out = simweek._activate_pending("prep", None, _NoPending(), said.append, season=2026)
    assert isinstance(out, tuple) and len(out) == 3, out
    assert out == ([], [], []), out
    assert said == [], said

    # Every `return` in the function, read from the source: they must all hand back three
    # values. A tuple of a different length is the bug above, and it only shows up at a call
    # site, in a league that happens to have nobody pending.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_activate_pending")
    # Its own returns only: the function defines helpers inside itself (team_of, and the thunk
    # that does the store writes), and those return whatever they like.
    nested = {id(n) for helper in ast.walk(func)
              if isinstance(helper, ast.FunctionDef) and helper is not func
              for n in ast.walk(helper)}
    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return) and id(n) not in nested]
    assert returns, "no returns found - has the function been renamed?"
    for node in returns:
        assert isinstance(node.value, ast.Tuple) and len(node.value.elts) == 3, \
            f"line {node.lineno} returns {ast.dump(node.value)[:60]}, not a 3-tuple"

    # And the call site unpacks exactly that many.
    call_lines = [line for line in SOURCE.read_text(encoding="utf-8").splitlines()
                  if "_activate_pending(" in line and "def " not in line]
    assert call_lines, "the call site moved"
    for line in call_lines:
        names = line.split("=")[0].strip()
        assert names.count(",") == 2, f"call site unpacks {names!r}, not three values"

    print(f"OK  _activate_pending: {len(returns)} returns, all three values, and it writes nothing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
