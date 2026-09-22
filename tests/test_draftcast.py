"""The draft broadcast must never be able to cost anybody a pick.

By the time a selection is announced, a character has already been stamped onto a pro roster, had
his old slot handed back and had his draft position written to the store. Discord returning a 500
at that moment must change nothing, so every method here swallows everything and `run_draft` never
asks whether the broadcast worked.

THE TWO THINGS THAT WOULD ACTUALLY BREAK IT:

  * A 429 dropped on the floor. `notify.post` has no rate-limit handling at all - it logs the
    HTTPError and discards the message - and a draft posts twenty messages in a minute, so the one
    that gets dropped is somebody's pick. This waits and retries instead.
  * message_id surviving between picks. `WebhookMessage` is built to POST once and then PATCH that
    same message for ever, which is right for a progress card and catastrophic here: pick #2 would
    edit pick #1 and the channel would end up with one message that keeps changing.

    python tests/test_draftcast.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import draftcast  # noqa: E402
from commissioner.simstatus import RateLimited, Refused  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class Transport:
    """Stands in for WebhookMessage. Records what was sent and can misbehave on cue."""

    def __init__(self, fail=None):
        self.sent = []
        self.ids = []
        self.fail = list(fail or [])
        self.message_id = None
        self.closed = False

    def send(self, payload):
        # Record the id the caller left behind: a new message requires it to be None.
        self.ids.append(self.message_id)
        if self.fail:
            raise self.fail.pop(0)
        self.sent.append(payload)
        self.message_id = "123456789"      # what a real POST would leave set

    def close(self):
        self.closed = True


def pick(n, team="LCH", name="Dodger Manson"):
    return {"pick": n, "round": 1, "team": team, "need": "PF",
            "reason": "Highest ceiling left on the board (72).",
            "character": {"first_name": name, "last_name": "", "position": "C",
                          "height_inches": 86}}


def run():
    print("every pick is its own message, never an edit of the last one")
    t = Transport()
    cast = draftcast.DraftCast(2030, transport=t, delay=0, sleep=lambda s: None)
    cast.open(3, ["LCH", "TIL", "PLY"])
    for n in (1, 2, 3):
        cast.pick(pick(n))
    check("four messages went out", len(t.sent), 4)
    check("none of them was a PATCH", t.ids, [None, None, None, None])
    check("and the cast counted them", cast.sent, 4)

    print("the words carry the pick, the player and the reason")
    body = t.sent[1]["embeds"][0]
    check("the title names the team and the man", "LCH select Dodger Manson" in body["title"], True)
    check("the reason is the description", body["description"].startswith("Highest ceiling"), True)
    check("pings are disabled", t.sent[1]["allowed_mentions"], {"parse": []})

    print("a rookie deal is shown when there is one")
    t2 = Transport()
    c2 = draftcast.DraftCast(2030, transport=t2, delay=0, sleep=lambda s: None)
    c2.pick(pick(1), contract={"team": "LCH", "rate": 6, "years": 4})
    fields = t2.sent[0]["embeds"][0].get("fields") or []
    check("the deal is a field", any("6 skill points a week" in f["value"] for f in fields), True)

    print("a 429 waits and the pick still lands")
    waited = []
    t3 = Transport(fail=[RateLimited(1.5)])
    c3 = draftcast.DraftCast(2030, transport=t3, delay=0, sleep=waited.append)
    c3.pick(pick(1))
    check("it was sent on the retry", len(t3.sent), 1)
    check("and it waited what Discord asked for", 1.5 in waited, True)
    check("nothing was counted as failed", c3.failed, 0)

    print("a refusal is given up on, quietly, without retrying")
    t4 = Transport(fail=[Refused(404), Refused(404)])
    said = []
    c4 = draftcast.DraftCast(2030, transport=t4, delay=0, log=said.append, sleep=lambda s: None)
    check("pick reports failure", c4.pick(pick(1)), False)
    check("nothing was sent", len(t4.sent), 0)
    check("it said so", any("refused" in m for m in said), True)
    check("and never printed a URL", any("http" in m for m in said), False)

    print("a transport that explodes cannot take the draft with it")
    class Boom:
        message_id = None
        def send(self, payload): raise RuntimeError("network on fire")
        def close(self): pass
    c5 = draftcast.DraftCast(2030, transport=Boom(), delay=0, log=lambda m: None,
                             sleep=lambda s: None)
    for call in (lambda: c5.open(1, ["LCH"]), lambda: c5.pick(pick(1)),
                 lambda: c5.on_the_clock(pick(1)), lambda: c5.close([pick(1)]),
                 c5.finish):
        try:
            call()
            ok = True
        except Exception as exc:                                        # noqa: BLE001
            ok = f"raised {exc}"
        check("survives a broken transport", ok, True)

    print("no webhook configured is a quiet no-op, not a crash")
    c6 = draftcast.DraftCast(2030, transport=None, delay=0, log=lambda m: None)
    c6.transport = None
    check("open does nothing", c6.open(1, ["LCH"]), False)
    check("pick does nothing", c6.pick(pick(1)), False)

    print("the delay is honoured between picks, and is zero when asked")
    slept = []
    t7 = Transport()
    c7 = draftcast.DraftCast(2030, transport=t7, delay=8, sleep=slept.append)
    c7.pick(pick(1))
    check("it paused", slept, [8.0])
    slept.clear()
    c8 = draftcast.DraftCast(2030, transport=Transport(), delay=0, sleep=slept.append)
    c8.pick(pick(1))
    check("and does not pause when the delay is zero", slept, [])

    print()
    if FAILS:
        print("FAILED")
        for f in FAILS:
            print("  " + f)
        return 1
    print("OK  draft cast: one message per pick and never an edit, a 429 is waited out rather "
          "than dropped, and nothing it can do raises into the draft")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
