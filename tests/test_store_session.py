"""Supabase calls reuse their connection - and a write is never sent twice.

THE MEASUREMENT. `requests.request()` opens a new TCP connection and a new TLS handshake for
every call. Measured twice, from two directions, against this project from this machine: a fresh
GET of `settings` takes 1.53-1.95 s and the same GET on a kept-alive connection takes
0.06-0.07 s. Twenty-five times. Settings and characters are read in every phase of a Sim Week -
thirteen store calls in a dry run of seven days - so the handshake, not the query and not the
database, was most of what talking to Supabase cost.

THE PART THAT NEEDS A TEST IS NOT THE SPEED. Pooling introduces a failure that fresh connections
cannot have: the far end closes a kept-alive socket between calls, so the next request fails on
a connection that looked fine. The answer to it is a retry, and a retry is exactly the wrong
thing for half of this API.

    GET / HEAD     idempotent. Asking twice is asking once.
    POST           grant_week_points, activate_character, apply_upgrade_requests, snapshots.
                   A replay pays somebody twice or activates a character twice. simweek writes
                   activations ONE AT A TIME so that a failure can name exactly who was already
                   written; a silent replay defeats the one place the code is most careful.
    PATCH          a rating write. Same.

AND WHY THIS IS TESTED BY COUNTING ATTEMPTS rather than by reading a policy. The first version
mounted an HTTPAdapter with urllib3's Retry and `allowed_methods` left at its default, which
excludes POST - and that looks like a guarantee and is not one. `allowed_methods` gates only the
READ-error branch; the connect-error and "other"-error branches carry no method check, so a POST
can be replayed by a policy that appears to forbid it. A Codex session caught that in review. So
the retry lives in `_request` where the method is checked in plain code, and this test counts
what actually leaves the machine.

    python tests/test_store_session.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from commissioner import store  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self._payload = payload if payload is not None else [{"ok": True}]
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._payload


class FakeSession:
    """Records every attempt; fails the first `fail_times` of them."""

    def __init__(self, fail_times=0):
        self.attempts = []
        self.fail_times = fail_times
        self.closed = False

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.attempts.append({"method": method, "url": url, "headers": headers,
                              "params": params, "json": json, "timeout": timeout})
        if len(self.attempts) <= self.fail_times:
            raise requests.ConnectionError("connection aborted (stale keep-alive)")
        return FakeResponse()

    def close(self):
        self.closed = True


def with_sessions(*sessions):
    """Hand out these fake sessions in order, and report how many were used."""
    made = []

    def factory():
        if not made or getattr(store._local, "session", None) is None:
            nxt = sessions[len(made)] if len(made) < len(sessions) else sessions[-1]
            made.append(nxt)
            store._local.session = nxt
        return store._local.session

    return factory, made


def run(method, path, sessions, **kw):
    """Call _request against fake sessions. Returns (result or exception, sessions used).

    Entirely offline, and not only because the transport is fake: the configuration is stubbed
    too, so this passes on a machine with no .env and never reads the real service key. A test
    that needs production credentials to run is a test that gets skipped on the machine where
    somebody is trying to reproduce a failure.
    """
    cfg = store.cfg
    saved = {"session": store._session, "ok": cfg.is_configured, "url": cfg.supabase_url,
             "key": cfg.supabase_service_key, "timeout": cfg.timeout}
    factory, made = with_sessions(*sessions)
    store._session = factory
    cfg.is_configured = lambda: True
    cfg.supabase_url = lambda: "https://example.test"
    cfg.supabase_service_key = lambda: "test-key-not-a-real-one"
    cfg.timeout = lambda: 30
    store._local.session = None
    try:
        return store._request(method, path, **kw), made
    except Exception as exc:                                     # noqa: BLE001
        return exc, made
    finally:
        store._session = saved["session"]
        cfg.is_configured, cfg.supabase_url = saved["ok"], saved["url"]
        cfg.supabase_service_key, cfg.timeout = saved["key"], saved["timeout"]
        store._local.session = None


def main():
    # ---- one session per thread, reused --------------------------------------------------
    store._local.session = None
    a1, a2 = store._session(), store._session()
    assert a1 is a2, "a second call built a second session - the connection is not being reused"

    seen = {}

    def grab(name):
        seen[name] = store._session()

    threads = [threading.Thread(target=grab, args=(f"t{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(s) for s in seen.values()}) == 3, \
        "threads shared a session; requests.Session is not documented as thread-safe and the " \
        "panel answers on several threads while a sim runs on another"
    assert all(s is not a1 for s in seen.values()), "a worker thread got the main thread's session"

    # ---- a healthy call goes out once, with everything forwarded --------------------------
    good = FakeSession()
    out, _ = run("GET", "/rest/v1/characters", [good],
                 params={"select": "id"}, prefer="count=exact")
    assert not isinstance(out, Exception), out
    assert len(good.attempts) == 1, good.attempts
    sent = good.attempts[0]
    assert sent["params"] == {"select": "id"}, sent
    assert sent["headers"]["Prefer"] == "count=exact", "prefer header was dropped"
    # The stub key, not the real one: this test never reads production credentials.
    assert sent["headers"]["apikey"] == "test-key-not-a-real-one", "the key stopped being sent"
    assert sent["timeout"], "the timeout stopped being sent"
    assert sent["url"].endswith("/rest/v1/characters"), sent["url"]

    # ---- a GET on a dead connection is asked again, on a NEW session ----------------------
    dead, fresh = FakeSession(fail_times=1), FakeSession()
    out, used = run("GET", "/rest/v1/settings", [dead, fresh])
    assert not isinstance(out, Exception), f"a stale connection should be retried: {out}"
    assert len(dead.attempts) == 1 and len(fresh.attempts) == 1, \
        f"expected one attempt each, got {len(dead.attempts)} and {len(fresh.attempts)}"
    assert dead.closed, "the dead session was left open, so its socket is still in the pool"
    assert len(used) == 2, "the retry reused the session that just failed"

    # ---- and only once. A GET that keeps failing raises, it does not loop ------------------
    broken = FakeSession(fail_times=99)
    out, _ = run("GET", "/rest/v1/settings", [broken, FakeSession(fail_times=99)])
    assert isinstance(out, store.StoreError), out
    assert len(broken.attempts) == 1, broken.attempts
    assert "twice" in str(out), f"the error should say it tried twice: {out}"

    # ---- A WRITE IS NEVER REPLAYED --------------------------------------------------------
    for method, path in (("POST", "/rest/v1/rpc/grant_week_points"),
                         ("POST", "/rest/v1/rpc/activate_character"),
                         ("POST", "/rest/v1/snapshots"),
                         ("PATCH", "/rest/v1/characters")):
        once = FakeSession(fail_times=1)
        spare = FakeSession()
        out, _ = run(method, path, [once, spare], body={"p": 1})
        assert isinstance(out, store.StoreError), f"{method} {path} should surface the failure"
        assert len(once.attempts) == 1, \
            f"{method} {path} was attempted {len(once.attempts)} times. It may have reached " \
            "Supabase and failed on the way back, and nothing here can tell that apart from " \
            "never arriving."
        assert not spare.attempts, f"{method} {path} was replayed on a second connection"

    # ---- REPLAYABLE is the whole policy, and it stays small --------------------------------
    assert set(store.REPLAYABLE) == {"GET", "HEAD"}, \
        f"REPLAYABLE has changed to {store.REPLAYABLE} - every verb in it is replayed on a " \
        "connection failure, so adding a write to it pays somebody twice"

    print("OK  store session: pooled per thread, a stale GET is asked once more on a fresh "
          "connection, and POST/PATCH are attempted exactly once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
