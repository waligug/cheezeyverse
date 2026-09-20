"""Which real NBA player does this character play like?

The comparison is honest because it is on the same scale: FBPB3 ships
`PlayerFiles/2015 Season Start.csv`, 579 real players rated in exactly the fields the codec
writes. `tools/nba_archetypes.py` turns that file into `site/data/nba-archetypes.json`; this
module answers questions against it, for the Discord card and anything else on this side. The
builder asks the same question in JavaScript against the same file.

STYLE, NOT LEVEL, which is the whole reason this is not a nearest-neighbour search. A
fourteen-year-old with everything in the twenties is not "most like" the worst player in the
league - but that is precisely what matching on raw ratings answers, for every character, so
they would all come back matched to the same few end-of-bench names and the feature would be
worthless. Each player is reduced to a SHAPE first: his ratings centred on his own mean and
scaled by his own spread, which says what he is good at RELATIVE TO HIMSELF. DeMarcus Cousins
reads as "scores inside, cannot shoot, rebounds" whether he is rated 98 or 45, and so does a
prep kid built the same way.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data" / "nba-archetypes.json"

# The ratings that describe HOW somebody plays. Stamina and Fouling are deliberately out: they
# say how long he lasts and how carelessly he defends, not what kind of player he is.
SHAPE = ["InsideScoring", "JumpShot", "FtShot", "3pShot", "3pUsage", "Handling", "Passing",
         "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "OReb", "DReb",
         "Strength", "Quickness", "Jumping"]

_cache = {"at": None, "players": None}


def shape_of(ratings, fields=None):
    """Ratings as a shape. None when there is no spread at all - no real row, but a made-up
    one can be flat, and a zero vector would match everything equally."""
    keys = fields or SHAPE
    values = [float((ratings or {}).get(k) or 0) for k in keys]
    if not values:
        return None
    mean = sum(values) / len(values)
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    if spread < 1e-6:
        return None
    return [(v - mean) / spread for v in values]


def similarity(a, b):
    """Cosine similarity between two shapes; 1.0 is the same style of player."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def players(path=DATA):
    """The archetype list, read once and re-read only if the file changes underneath."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return []
    if _cache["at"] != stamp:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        _cache.update({"at": stamp, "players": payload.get("players") or [],
                       "fields": payload.get("fields") or SHAPE})
    return _cache["players"] or []


def most_like(ratings, count=3, path=DATA, pool=None):
    """The closest real players to a set of ratings, best first. [] when we cannot say.

    Never raises and never guesses: a missing archetype file, an empty one, or a character with
    no ratings all come back as "no answer", because a card that omits the line is fine and a
    card that names the wrong player is not.
    """
    # `pool` is for the tool that BUILDS the file: it has the players in memory, straight from
    # the roster CSV, and there may be no JSON on disk yet to read them back from.
    pool = pool if pool is not None else players(path)
    if not pool:
        return []
    mine = shape_of(ratings, _cache.get("fields"))
    if mine is None:
        return []
    scored = [(similarity(mine, p.get("shape")), p) for p in pool]
    scored.sort(key=lambda sp: -sp[0])
    return [{**p, "score": round(s, 3)} for s, p in scored[:count] if s > 0]
