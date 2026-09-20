"""The card Discord gets when somebody's character turns up in the universe for the first time.

WHY THE COMMISSIONER POSTS IT AND NOT THE SITE. The webhook is a secret - anyone holding it can
post to the server as the commissioner - so it cannot live in a public page's JavaScript. The
site celebrates the build on screen; this is the part everybody else sees.

WHEN. At activation, not at creation, and the difference is the content: until a sim runs he has
no team, no jersey and no row in league.dat, so the card would be an announcement with nothing
in it. It is posted from inside the same write that marks him active, so a character who failed
to land is never announced.

THE POSITION LINE IS ON PURPOSE. FBPB3's coaches build their own depth charts and will play a
character wherever the roster needs him - a listed centre turning up at power forward is the
game working, not a bug, and it is the single most common "something is broken" question. The
card says so the first time anybody sees him, instead of the answer living in a Discord reply
six weeks later.
"""
from __future__ import annotations

from . import archetypes
from . import notify
from . import settings as cfgenv
from .growth import build_weight, format_height
from .universe import config as cfg

# Discord embed colour, per level: prep, college, pro.
COLOURS = {"prep": 0x4F9DDE, "college": 0xE0A23C, "pro": 0xD05A6E}


def card(character, league_key, league_name, team=None, matches=None):
    """The embed for one arrival. Pure - builds a dict, sends nothing."""
    first = (character.get("first_name") or "").strip()
    last = (character.get("last_name") or "").strip()
    name = f"{first} {last}".strip() or "A new character"
    inches = character.get("height_inches")
    weight = character.get("weight_lbs") or build_weight(inches, character.get("build"))
    pos = character.get("position") or "?"

    bits = [b for b in (format_height(inches) if inches else None,
                        f"{int(weight)} lbs" if weight else None,
                        pos, league_name) if b]
    embed = {
        "title": name,
        "description": " · ".join(bits),
        "color": COLOURS.get(league_key, 0x4F9DDE),
        "fields": [],
    }

    site = (cfgenv.get("SITE_URL", "") or "").strip()
    if site and character.get("id"):
        embed["url"] = f"{site.rstrip('/')}/career.html?id={character['id']}"

    if matches is None:
        matches = archetypes.most_like(character.get("ratings") or {}, 3)
    if matches:
        best, rest = matches[0], matches[1:]
        value = f"**{best['name']}**"
        if best.get("pos") or best.get("team"):
            value += f" ({', '.join(x for x in (best.get('pos'), best.get('team')) if x)})"
        if rest:
            value += "\n" + " · ".join(m["name"] for m in rest)
        embed["fields"].append({"name": "Plays like", "value": value, "inline": True})

    best_at = _best_ratings(character.get("ratings") or {}, 3)
    if best_at:
        embed["fields"].append({
            "name": "Best at",
            "value": "\n".join(f"{field} {value}" for field, value in best_at),
            "inline": True,
        })

    if team:
        embed["fields"].append({"name": "Signed with", "value": team_name(league_key, team),
                                "inline": True})

    embed["footer"] = {"text": f"Listed at {pos} - but the coaches build their own depth charts, "
                               "so don't be surprised to see him somewhere else."}
    return embed


def team_name(league_key, abbrev):
    """"SAS" -> "Saskatoon Berries", or the abbreviation if the league does not know it."""
    try:
        for t in cfg.BY_KEY[league_key].teams:
            if t.abbrev == abbrev:
                return f"{t.city} {t.nickname}"
    except Exception:                                            # noqa: BLE001
        pass
    return str(abbrev)


def _best_ratings(ratings, count):
    """His `count` best ratings, ignoring the ones that are not a skill."""
    skills = [(k, int(v)) for k, v in (ratings or {}).items()
              if k in archetypes.SHAPE and str(v).lstrip("-").isdigit()]
    skills.sort(key=lambda kv: -kv[1])
    return skills[:count]


def arrival(character, league_key, league_name, team=None, log=print):
    """Post the card. Returns True if Discord took it.

    Never raises - `notify.post_embed` swallows everything. An arrival that cannot be announced
    is still an arrival, and the sim it is running inside has already played the basketball.
    """
    try:
        return notify.post_embed(card(character, league_key, league_name, team), log=log)
    except Exception as exc:                                     # noqa: BLE001
        log(f"could not build the arrival card ({exc.__class__.__name__}); the run is unaffected")
        return False
