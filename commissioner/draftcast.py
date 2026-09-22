"""Draft night, live in Discord, one pick at a time.

WHY NOT `notify.post`. It has no rate-limit handling at all: a 429 is caught as a generic
HTTPError, logged, and the message is dropped (`notify.py`, the `except urllib.error.HTTPError`
branch). That is fine for the one message it was built for and wrong for a draft, where twenty
posts land in a minute and the one that gets dropped is somebody's pick.

WHY NOT `SimStatus`. It is a single card that edits itself, and it renders through a module-level
`render()` with no hook. A draft board that rewrites one message is a progress bar; a draft is a
sequence of moments, and the moment is the point.

So this borrows the half of `simstatus` worth borrowing - `WebhookMessage`, the only
POST-then-capture-the-id transport in the project, and its `RateLimited` / `Refused` mapping,
which is the only Discord rate-limit handling that exists - and posts a new message per pick.

IT MUST NEVER FAIL A DRAFT. By the time a pick is announced, a character has already been stamped
onto a pro roster, had his slot handed back, and had his draft position written to the store.
Losing that because Discord returned a 500 would be absurd, so every public method here swallows
everything. The draft does not check whether the broadcast worked, and must not.
"""
from __future__ import annotations

import time

from . import draft as draftlib
from . import notify
from .simstatus import RateLimited, Refused, WebhookMessage

# Long enough to read one pick before the next arrives, short enough that twenty picks do not
# outlast the offseason around them. Overridable per call; zero in tests and dry runs.
DELAY = 8.0
# Discord's own floor is 5/second per webhook. Two failed 429 waits in a row means something is
# badly wrong and the draft should get on with it rather than stand on the clock.
MAX_WAITS = 2
COLOR = 0xD89A18          # the same gold the statistical feats post in
COLOR_OPEN = 0x4CAF50     # the board is open
COLOR_DONE = 0x3E7CB1     # and closed


def _clean(value, limit):
    """Strip what Discord treats as markup or a ping. Same defence `feats.event_embed` uses."""
    return str(value).replace("`", "").replace("@", "＠")[:limit]


class DraftCast:
    """Announces a draft. Construct, `open()`, `pick()` per selection, `close()`."""

    def __init__(self, season, *, transport=None, delay=DELAY, log=print,
                 setting="DISCORD_DRAFT_WEBHOOK_URL", sleep=time.sleep):
        self.season = season
        self.delay = max(0.0, float(delay or 0))
        self.log = log
        self.sleep = sleep
        self.sent = 0
        self.failed = 0
        self._own_transport = transport is None
        if transport is not None:
            self.transport = transport
        else:
            # Its own channel when there is one, the main one when there is not. `feats` set the
            # precedent for a second webhook; falling back means a universe that never configured
            # a draft channel still gets its draft, rather than silently getting nothing.
            target = notify.url(setting) or notify.url()
            self.transport = WebhookMessage(target) if target else None

    # ---- the plumbing ----------------------------------------------------------------------
    def _post(self, embed):
        """One message. Honours a 429 by waiting; gives up on anything permanent."""
        if self.transport is None:
            return False
        payload = {"content": "", "allowed_mentions": {"parse": []}, "embeds": [embed]}
        for _ in range(MAX_WAITS + 1):
            try:
                # Every message is a NEW one, so the id from the first POST must not be kept -
                # WebhookMessage would PATCH the previous pick on top of itself and the board
                # would be one message that keeps changing, which is the thing this is not.
                self.transport.message_id = None
                self.transport.send(payload)
                self.sent += 1
                return True
            except RateLimited as exc:
                self.sleep(max(0.0, float(exc.args[0] if exc.args else 5.0)))
                continue
            except Refused as exc:
                status = exc.args[0] if exc.args else "?"
                self._log(f"draft cast refused (HTTP {status}); the draft is unaffected")
                self.failed += 1
                return False
            except Exception as exc:                                    # noqa: BLE001
                self._log(f"draft cast failed ({type(exc).__name__}); the draft is unaffected")
                self.failed += 1
                return False
        self.failed += 1
        return False

    def _log(self, message):
        try:
            self.log(message)
        except Exception:                                               # noqa: BLE001
            pass

    def _pause(self):
        if self.delay:
            try:
                self.sleep(self.delay)
            except Exception:                                           # noqa: BLE001
                pass

    # ---- the broadcast ---------------------------------------------------------------------
    def open(self, count, order):
        """The board is open. Named teams, so the first message is worth reading on its own."""
        try:
            head = ", ".join(order[:5]) + ("…" if len(order) > 5 else "")
            return self._post({
                "author": {"name": "CHEEZEYVERSE · DRAFT NIGHT"},
                "title": _clean(f"The {self.season} draft is open", 200),
                "color": COLOR_OPEN,
                "description": (f"**{count}** player{'' if count == 1 else 's'} declared.\n"
                                f"Order, worst record first: {_clean(head, 300)}"),
            })
        except Exception:                                               # noqa: BLE001
            return False

    def on_the_clock(self, pick):
        """Who is about to choose. The pause before a pick is most of the drama."""
        try:
            posted = self._post({
                "color": COLOR,
                "description": _clean(f"**{pick['team']}** are on the clock "
                                      f"with pick #{pick['pick']}…", 300),
            })
            self._pause()
            return posted
        except Exception:                                               # noqa: BLE001
            return False

    def pick(self, pick, contract=None):
        """One selection, with the reason the team actually used."""
        try:
            character = pick.get("character") or {}
            fields = []
            if contract and contract.get("rate") is not None:
                fields.append({
                    "name": "Rookie deal",
                    "value": _clean(f"{contract.get('years', 0)} yr · "
                                    f"{contract['rate']} skill points a week", 200),
                    "inline": True,
                })
            if pick.get("need"):
                fields.append({"name": "Their hole", "value": _clean(pick["need"], 60),
                               "inline": True})
            embed = {
                "author": {"name": f"CHEEZEYVERSE · {self.season} DRAFT"},
                "title": _clean(f"#{pick['pick']} · {pick['team']} select "
                                f"{draftlib.describe(character)}", 220),
                "color": COLOR,
                "description": _clean(pick.get("reason") or "", 1200),
                "footer": {"text": _clean(f"Round {pick.get('round', 1)} · pick "
                                          f"{pick['pick']}", 180)},
            }
            if fields:
                embed["fields"] = fields
            posted = self._post(embed)
            self._pause()
            return posted
        except Exception:                                               # noqa: BLE001
            return False

    def close(self, picks):
        """The finished board, so the channel keeps one message worth scrolling back to."""
        try:
            lines = []
            for p in picks[:25]:
                name = draftlib.describe(p.get("character") or {})
                lines.append(f"**{p['pick']}.** {p['team']}  {name}")
            body = "\n".join(lines) or "Nobody declared."
            return self._post({
                "author": {"name": "CHEEZEYVERSE · DRAFT NIGHT"},
                "title": _clean(f"The {self.season} draft is complete", 200),
                "color": COLOR_DONE,
                "description": _clean(body, 3800),
            })
        except Exception:                                               # noqa: BLE001
            return False

    def finish(self):
        try:
            if self._own_transport and self.transport is not None:
                self.transport.close()
        except Exception:                                               # noqa: BLE001
            pass
