"""Tell the Discord server what the commissioner is doing.

A sim takes twenty minutes and happens on a machine in another room. Without this, the only
person who knows a sim is running is whoever pressed the button, and the only way anybody else
finds out their player moved is by reloading the site until it changes.

THE ONE RULE: THIS MUST NEVER FAIL A WEEK.
Every call is wrapped and every failure is swallowed into a log line. The sim has already
played the basketball, written the saves and pushed the site by the time most of these fire -
losing all of that because Discord returned a 500, or because the webhook was deleted, would be
absurd. Same rule the git push runs under, for the same reason.

THE URL IS A SECRET, in the sense that anybody holding it can post to the server as the
commissioner. It lives in the environment (DISCORD_WEBHOOK_URL), never in the repo, and this
module never logs it - failures name the status code, not the address.

Unset means silent. A universe with no webhook configured is a normal universe, not a broken
one, so nothing here complains about its absence.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import settings as cfg

TIMEOUT = 8          # a slow webhook must not hold up the pipeline behind it
MAX_LENGTH = 1900    # Discord rejects a message over 2000 characters outright


def _running_under_tests():
    """Keep every test runner away from the configured live Discord webhook.

    Most tests replace ``post`` or ``SimStatus`` with a fake, but that is too fragile for a
    machine whose normal .env contains the production webhook.  Put the safety boundary here,
    where both the one-line notifier and the live status card obtain their target.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return True
    script = Path(sys.argv[0] or "").resolve()
    if script.name.startswith("test_") and "tests" in {part.casefold() for part in script.parts}:
        return True
    return any(name.startswith("tests.") or name.startswith("test_") for name in sys.modules)


def url():
    if (_running_under_tests()
            and os.environ.get("CHEEZEYVERSE_ALLOW_TEST_DISCORD") != "1"):
        return ""
    return (cfg.get("DISCORD_WEBHOOK_URL", "") or "").strip()


def post(text, log=print):
    """Send one message. Returns True if Discord took it, False for every other outcome.

    Never raises. Never prints the webhook.
    """
    target = url()
    if not target:
        return False
    body = str(text).strip()
    if not body:
        return False
    if len(body) > MAX_LENGTH:
        body = body[:MAX_LENGTH - 1] + "…"
    return _send({"content": body}, log)


def post_embed(embed, text=None, log=print):
    """Send one embed - a card with a title, fields and a colour - and optionally a line above it.

    Same rule as `post`: never raises, never prints the webhook, and a refusal is a log line.
    """
    if not embed:
        return False
    payload = {"embeds": [embed]}
    if text:
        payload["content"] = str(text)[:MAX_LENGTH]
    return _send(payload, log)


def _send(payload, log=print):
    target = url()
    if not target:
        return False
    request = urllib.request.Request(
        target,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "cheezeyverse-commissioner"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        # 404 almost always means the webhook was deleted in Discord; 429 is rate limiting.
        log(f"Discord refused the message ({exc.code}); the run is unaffected")
    except Exception as exc:
        log(f"could not reach Discord ({exc.__class__.__name__}); the run is unaffected")
    return False


def estimate_minutes(days, leagues=3):
    """Roughly how long a run of `days` will take, for the "about N minutes" line.

    From the baseline measured on 2026-09-18 across five real runs: about 480 s of fixed cost
    per run - loading, saving, exporting, publishing - plus about 8.7 s per simmed day per
    league. Deliberately rounded up to the minute and described as "about": a number that
    reads as precise will be held against us the first time a run takes 30 seconds longer.
    """
    seconds = 480 + 8.7 * max(0, int(days or 0)) * max(1, int(leagues or 1))
    return max(1, round(seconds / 60))
