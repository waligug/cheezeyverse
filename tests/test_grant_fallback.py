"""Paying people must survive a code deploy landing before its SQL migration.

PostgREST resolves an RPC by its ARGUMENT NAMES. `grant_week_points` gained a third argument,
`p_per`, when income started scaling with level - so sending p_per to a database that still has
the two-argument function does not fall back to the old behaviour, it fails to find the function
at all (PGRST202). Code and schema cannot land in the same instant, and the server pulls from
GitHub while the migration is run by hand in the Supabase SQL editor, so that gap is a normal
state and not a mistake.

What it would have cost, unhandled: the points step is the LAST thing a Sim Week does, after
every league has been simmed, saved, exported and published. A run would have done all the
basketball and then failed at the moment it went to pay people.

Three properties:
  * an un-migrated database still pays, at the flat rate, and says why;
  * a migrated one sends the level rate and calls once;
  * a genuine failure inside the function still raises, instead of being retried into a second,
    more confusing failure about migrations.

    python tests/test_grant_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import store  # noqa: E402

NOT_FOUND = ('POST /rest/v1/rpc/grant_week_points -> HTTP 404: '
             '{"code":"PGRST202","message":"Could not find the function"}')


def main():
    settings = {"points_per_week": 1, "points_per_week_pro": 3}
    real_rpc, real_settings = store._rpc, store.get_settings
    store.get_settings = lambda: settings
    try:
        calls = []

        def old_database(_name, body):
            calls.append(dict(body))
            if "p_per" in body:
                raise store.StoreError(NOT_FOUND)
            return 7

        store._rpc = old_database
        assert store.grant_week_points(league="pro", weeks=2) == 7
        assert len(calls) == 4, f"expected two attempts per week, got {calls}"
        assert calls[0]["p_per"] == 3 and "p_per" not in calls[1], calls
        # the ledger line must survive the fallback, or the week is paid anonymously
        assert calls[1]["p_reason"] == "week simmed (pro x3)", calls[1]

        calls.clear()

        def new_database(_name, body):
            calls.append(dict(body))
            return 7

        store._rpc = new_database
        assert store.grant_week_points(league="pro", weeks=1) == 7
        assert len(calls) == 1 and calls[0]["p_per"] == 3, calls

        def broken(_name, _body):
            raise store.StoreError("HTTP 500: something genuinely wrong inside the function")

        store._rpc = broken
        try:
            store.grant_week_points(league="pro", weeks=1)
        except store.StoreError as exc:
            assert "genuinely wrong" in str(exc), exc
        else:
            raise AssertionError("a real failure was swallowed")
    finally:
        store._rpc, store.get_settings = real_rpc, real_settings

    print("OK  grant_week_points: pays through a migration gap, and does not mask real errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
