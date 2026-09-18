"""The two stores must answer the same calls.

`simweek.store()` returns `commissioner.store` when Supabase is configured and
`commissioner.localstore` when it is not. Every caller is written against whichever one it
happens to get, so a function that exists on only one of them is a crash that appears the day
the backend changes - and does not appear one minute before.

That is not hypothetical. Switching to Supabase moved seven functions out from under the app at
once: creating a character raised, every sim week raised, and the whole offseason raised. Two of
those were swallowed by `except Exception` blocks that exist for good reasons, so the first
symptom was roster protection silently never running.

This test reads the call sites out of the source rather than listing them by hand, so a call
added next month is covered without anybody remembering to come back here.

    python tests/test_store_parity.py
"""
from __future__ import annotations

import ast
import inspect
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import localstore, store  # noqa: E402

# Names that hold a store module in the code that calls it.
STORE_VARS = {"st", "store", "_store"}

# Functions that are deliberately one-sided, with the reason.
ONLY_LOCAL = {
    # Pricing. schema.sql does this in a trigger, so the browser cannot name its own price;
    # localstore has no trigger and has to do the same arithmetic itself.
    "add_request", "upgrade_cost", "step_cost", "queued_requests",
    "BadRequest", "NotEnoughPoints",
}
ONLY_SUPABASE = {"characters_for_export", "main", "StoreError", "StoreNotConfigured"}


def call_sites():
    """Every `<store var>.<name>(...)` in the commissioner and its tools, with arity."""
    found = {}
    for path in sorted([*(ROOT / "commissioner").rglob("*.py"), *(ROOT / "tools").rglob("*.py")]):
        if path.name in ("store.py", "localstore.py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            # st.foo(...)  or  store().foo(...)  or  simweek.store().foo(...)
            name = None
            if isinstance(owner, ast.Name) and owner.id in STORE_VARS:
                name = owner.id
            elif isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name) \
                    and owner.func.id == "store":
                name = "store()"
            elif isinstance(owner, ast.Call) and isinstance(owner.func, ast.Attribute) \
                    and owner.func.attr == "store":
                name = "store()"
            if not name:
                continue
            kwargs = tuple(sorted(k.arg for k in node.keywords if k.arg))
            found.setdefault(node.func.attr, set()).add(
                (len(node.args), kwargs, f"{path.relative_to(ROOT)}:{node.lineno}"))
    return found


def accepts(func, positional, kwargs):
    try:
        inspect.signature(func).bind(*range(positional), **{k: None for k in kwargs})
        return None
    except TypeError as exc:
        return str(exc)


def main():
    problems = []
    sites = call_sites()
    for name, calls in sorted(sites.items()):
        if name in ONLY_LOCAL or name in ONLY_SUPABASE:
            continue
        for module in (store, localstore):
            func = getattr(module, name, None)
            if func is None:
                where = sorted({c[2] for c in calls})[:3]
                problems.append(f"{module.__name__} has no {name}() - called from {', '.join(where)}")
                continue
            for positional, kwargs, where in sorted(calls):
                why = accepts(func, positional, kwargs)
                if why:
                    problems.append(f"{module.__name__}.{name} rejects the call at {where}: {why}")

    print(f"checked {len(sites)} store function(s) against both backends")
    for row in problems:
        print(f"  FAIL  {row}")
    if problems:
        print(f"\n{len(problems)} mismatch(es)")
        return 1
    print("both stores answer every call the app makes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
