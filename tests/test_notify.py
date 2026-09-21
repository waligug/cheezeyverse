"""Discord notifications must be useful, and must never be able to fail a week.

The second half is the point. By the time the end-of-run message fires, the sim has played
three leagues, written three saves, exported two thousand pages and pushed the site. Losing any
of that because a webhook was deleted, or Discord returned a 500, or the machine briefly had no
network, would be absurd - so every call here is wrapped, and this test holds that shut.

It also checks the report says the things worth saying. A message that fires reliably and
contains nothing anybody wanted is not better than no message.

    python tests/test_notify.py
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import notify  # noqa: E402
from commissioner import simweek  # noqa: E402

STEPS = [
    {"step": "backup", "league": "prep", "message": "backing up CV_Prep"},
    {"step": "apply", "league": "prep", "message": "Liam Zimmel claimed ARI (was Jaime Fore)"},
    {"step": "apply", "league": "prep", "message": "Tim Turner: Blocking 32->33"},
    {"step": "sim", "league": "prep", "message": "simming 7 days of Cheezeyverse Prep"},
    {"step": "publish", "league": None, "message": "the public site is live"},
    {"step": "points", "league": "prep", "message": "1 point(s) to 7 character(s) in prep"},
]
RESULT = {"leagues": ["prep", "college", "pro"], "days": 7, "dry_run": False}


def main():
    # Test processes must be unable to reach the production webhook from .env.  This protects
    # both notify.post and the background SimStatus worker even when a new test forgets a mock.
    assert notify._running_under_tests()
    assert notify.url() == "", "a test process can see the live Discord webhook"

    # ---- the report says the things worth saying ------------------------------------------
    text = simweek._discord_report(STEPS, RESULT, 659.3)
    for wanted in ("Sim done", "1 week(s)", "11 min", "Liam Zimmel claimed ARI",
                   "1 point(s) to 7 character(s)", "GitHub Pages deployment pending"):
        assert wanted in text, f"missing {wanted!r} from:\n{text}"
    # a rating change is not news; the placement and the payout are
    assert "Blocking" not in text, text

    failed = [r for r in STEPS if r["step"] != "publish"]
    assert "did NOT publish" in simweek._discord_report(failed, RESULT, 600), "silent about a failed push"

    # 28 days pays four weeks, and the message should say so rather than "1 week"
    assert "4 week(s)" in simweek._discord_report(STEPS, {**RESULT, "days": 28}, 1210)

    # ---- the estimate tracks the measured baseline ------------------------------------------
    # Real runs: 7 days took 11.0 min, 28 days took 20.2 min.
    assert notify.estimate_minutes(7) == 11, notify.estimate_minutes(7)
    assert notify.estimate_minutes(28) == 20, notify.estimate_minutes(28)
    assert notify.estimate_minutes(0) >= 1

    # ---- nothing here can raise ------------------------------------------------------------
    real_url = notify.url
    try:
        notify.url = lambda: ""
        assert notify.post("nobody is listening") is False, "posted with no webhook configured"

        notify.url = lambda: "https://discord.example/webhook"
        for boom in (
            urllib.error.HTTPError("u", 404, "Not Found", {}, None),   # webhook deleted
            urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None),
            urllib.error.URLError("no network"),
            TimeoutError("slow"),
            ValueError("something unforeseen"),
        ):
            def explode(*_a, **_k):
                raise boom
            notify.urllib.request.urlopen = explode
            said = []
            assert notify.post("hello", log=said.append) is False
            assert said, f"{boom.__class__.__name__} was swallowed without a word"
            # the webhook itself must never reach a log line
            assert "discord.example/webhook" not in " ".join(said), said

        # an empty message is not worth a request
        assert notify.post("   ") is False

        # over-long messages are trimmed, not rejected by Discord
        sent = {}

        class Fake:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def capture(req, timeout=None):
            sent["len"] = len(req.data)
            return Fake()

        notify.urllib.request.urlopen = capture
        assert notify.post("x" * 5000) is True
        assert sent["len"] < 2100, sent
    finally:
        notify.url = real_url

    print("OK  notify: the report carries the news, and nothing in it can fail a week")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
