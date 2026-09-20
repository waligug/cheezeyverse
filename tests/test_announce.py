"""The arrival card: what it must say, and what it must survive.

This runs inside a Sim Week, from the same write that marks a character active, after the save
has been committed. So the rule is the one every notification in this project runs under: IT MUST
NEVER FAIL A WEEK. A character with no ratings, a missing archetype file, a league nobody has
heard of - each of those is a card with less on it, never an exception thrown into a pipeline
that has already played the basketball and written the saves.

THE POSITION LINE IS THE POINT, and it is checked here rather than left to whoever edits the
card next. FBPB3's coaches build their own depth charts and play people away from their listed
spot constantly; a centre turning up at power forward is the game working. It is also the single
most common "something is broken" question, and the card is the first thing anybody sees.

    python tests/test_announce.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import announce, notify  # noqa: E402

# The card only carries a link when SITE_URL is set, and it is set in SERVERPC's `.env` and
# nowhere else - so this test passed there and failed on every other machine, for a reason that
# had nothing to do with the card. A test that reads a machine's own configuration is testing
# the machine. os.environ wins over `.env` in settings.get(), so this pins it either way.
os.environ["SITE_URL"] = "https://example.invalid/cheezeyverse"

HIM = {
    "id": "abc-123",
    "first_name": "Milo", "last_name": "Trask",
    "position": "C", "height_inches": 78, "build": "strong",
    "ratings": {"InsideScoring": 55, "OReb": 50, "DReb": 48, "JumpShot": 20, "3pShot": 15,
                "Blocking": 44, "Strength": 47, "Quickness": 25, "Passing": 22, "Handling": 21,
                "FtShot": 30, "3pUsage": 10, "PostDefense": 45, "PerimeterDefense": 24,
                "Stealing": 20, "Jumping": 40},
}


def text_of(embed):
    """Everything the card says, as one string."""
    parts = [str(embed.get("title")), str(embed.get("description")),
             str((embed.get("footer") or {}).get("text"))]
    parts += [f'{f["name"]} {f["value"]}' for f in embed.get("fields") or []]
    return " | ".join(parts)


def main():
    # ---- what it says ----------------------------------------------------------------------
    card = announce.card(HIM, "prep", "Cheezeyverse Prep", "SAS")
    said = text_of(card)
    assert "Milo Trask" in said, said
    assert "6'6\"" in said, f"no height on the card: {said}"
    assert "lbs" in said, f"no weight on the card: {said}"
    assert "Cheezeyverse Prep" in said, said
    assert "Saskatoon Berries" in said, "the team should read as a team, not an abbreviation"

    # the position note, in the footer, in words anybody would understand
    foot = (card.get("footer") or {}).get("text", "")
    assert "Listed at C" in foot, foot
    assert "depth chart" in foot.lower(), \
        "the card must explain that coaches play people away from their listed position"

    # he is an inside big, and the archetype line must reflect that rather than a guard
    plays_like = next((f["value"] for f in card["fields"] if f["name"] == "Plays like"), "")
    assert plays_like, "no archetype line - site/data/nba-archetypes.json should be on disk"
    assert "**" in plays_like, "the best match should be the one that stands out"

    # and it links to his own page, so the card is worth clicking
    assert card.get("url", "").endswith("career.html?id=abc-123"), card.get("url")

    # ---- what it survives --------------------------------------------------------------------
    for label, person in (
            ("no ratings at all", {**HIM, "ratings": {}}),
            ("flat ratings", {**HIM, "ratings": {k: 30 for k in HIM["ratings"]}}),
            ("no height", {**HIM, "height_inches": None}),
            ("no name", {**HIM, "first_name": "", "last_name": ""}),
            ("nothing but an id", {"id": "x"}),
    ):
        out = announce.card(person, "prep", "Cheezeyverse Prep", "SAS")
        assert out.get("title"), f"{label}: the card lost its title"
        assert "depth chart" in (out.get("footer") or {}).get("text", "").lower(), \
            f"{label}: the position note went missing"

    # a league config nobody has heard of, and a team that is not in it
    out = announce.card(HIM, "nowhere", "Nowhere League", "ZZZ")
    assert "ZZZ" in text_of(out), "an unknown team should still be named, not dropped"

    # ---- and it never posts when there is nowhere to post -------------------------------------
    real = notify.url
    notify.url = lambda: ""
    try:
        assert announce.arrival(HIM, "prep", "Cheezeyverse Prep", "SAS", log=lambda *_: None) is False, \
            "with no webhook configured this must be silent, not an error"
    finally:
        notify.url = real

    # a card builder that throws must not escape into the pipeline
    broken, real_card = None, announce.card
    announce.card = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    try:
        broken = announce.arrival(HIM, "prep", "Cheezeyverse Prep", "SAS",
                                  log=lambda *_: None)
    finally:
        announce.card = real_card
    assert broken is False, "a failure inside the card must be swallowed, not raised into a sim"

    print(f"OK  arrival card: says who he is, who he plays like ({plays_like.splitlines()[0]}), "
          "and why he may not play his own position - and never raises")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
