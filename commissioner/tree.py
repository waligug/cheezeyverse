"""The skill tree: its nodes, and what each one does to a sheet.

supabase/skill_tree.sql prices and guards the tree and applies a bought node to the stored sheet.
This file is where the nodes are defined (tools/sync_tree.py writes them into public.tree_nodes),
and what the commissioner uses to put a node into league.dat and to age a character with the
shields he owns. `sheet_after` is apply_upgrade_requests' node arithmetic, step for step, and
tests/test_tree.py holds the two together.

THE SHAPE (balance model and its numbers: docs/SKILL_TREE.md):
  five branches - Scoring, Playmaking, Defense, Rebounding, Athletic
  three tiers each: 15 / 30 / 50 points, opening in prep / college / pro once 20 / 60 / 100
  points have gone into that branch's ratings since the tree opened
  one Signature per player at the top of a branch: 60 points, pro, 160 spent in the branch

Tiers raise ceilings (the game grows every player toward his ceilings on its own), make the
branch cheaper, and at tier 3 halve that branch's aging. The Athletic ratings have no ceiling in
FBPB3, so that branch pays in ratings instead.
"""
from __future__ import annotations

from .characters import POT_BY_RATING

BRANCHES = {
    "Scoring": ["InsideScoring", "JumpShot", "FtShot", "3pShot"],
    "Playmaking": ["Handling", "Passing"],
    "Defense": ["PostDefense", "PerimeterDefense", "Stealing", "Blocking"],
    "Rebounding": ["OReb", "DReb"],
    "Athletic": ["Quickness", "Jumping", "Strength", "Stamina"],
}
#: every spendable rating's branch; 3pUsage is a scoring habit and counts toward Scoring
BRANCH_OF = {r: b for b, rs in BRANCHES.items() for r in rs} | {"3pUsage": "Scoring"}

TIER_COST = {1: 15, 2: 30, 3: 50, 4: 60}
TIER_STAGE = {1: "prep", 2: "college", 3: "pro", 4: "pro"}
TIER_SPENT = {1: 20, 2: 60, 3: 100, 4: 160}
SHIELD = 0.5          # a tier-3 branch ages at this fraction of the usual rate
DISCOUNT = -10        # a tier-2 branch's growth bias change, in percent


def _each(ratings, n):
    return {r: n for r in ratings}


def _tiers(branch, names, blurbs):
    rs = BRANCHES[branch]
    athletic = branch == "Athletic"
    wide = len(rs) == 4
    # Four-rating branches spread 12 ceiling points per tier, two-rating ones put the same 12 on
    # two ratings. Athletic has no ceilings, so it pays a smaller amount of rating instead.
    per = {1: 3 if wide else 6, 2: 3 if wide else 6, 3: 6 if wide else 10}
    out = []
    for tier in (1, 2, 3):
        fx = ({"ratings": _each(rs, 1 if tier < 3 else 2)} if athletic
              else {"potentials": _each(rs, per[tier])})
        if tier == 2:
            fx["bias"] = _each(rs, DISCOUNT)
        if tier == 3:
            fx["shield"] = {branch: SHIELD}
        out.append({
            "id": f"{branch.lower()}-{tier}", "branch": branch, "tier": tier,
            "name": names[tier - 1], "blurb": blurbs[tier - 1],
            "cost": TIER_COST[tier], "stage": TIER_STAGE[tier],
            "requires": [] if tier == 1 else [f"{branch.lower()}-{tier - 1}"],
            "min_spent": TIER_SPENT[tier], "min_ratings": {}, "group_key": None,
            "effects": fx, "sort": tier * 10,
        })
    return out


def _signature(branch, slug, name, blurb, effects, sort):
    return {
        "id": f"{branch.lower()}-sig-{slug}", "branch": branch, "tier": 4, "name": name,
        "blurb": blurb, "cost": TIER_COST[4], "stage": TIER_STAGE[4],
        "requires": [f"{branch.lower()}-3"], "min_spent": TIER_SPENT[4], "min_ratings": {},
        "group_key": "signature", "effects": effects, "sort": 40 + sort,
    }


TREE = (
    _tiers("Scoring",
           ["Shooting Form", "Scorer's Touch", "Bucket Getter"],
           ["Reps until the form stops wobbling. Every scoring ceiling +3.",
            "Scoring ceilings +3, and scoring is 10% cheaper to train.",
            "Scoring ceilings +6, and his scoring ages half as fast."])
    + [_signature("Scoring", "deadeye", "Deadeye",
                  "+9 3PT Shot, +7 Jump Shot, both ceilings +8, and he looks for the three more.",
                  {"ratings": {"3pShot": 9, "JumpShot": 7}, "potentials": {"3pShot": 8, "JumpShot": 8},
                   "tendency": {"3pUsage": 10}}, 1),
       _signature("Scoring", "paint-beast", "Paint Beast",
                  "+9 Inside Scoring and its ceiling +8, +7 Strength.",
                  {"ratings": {"InsideScoring": 9, "Strength": 7}, "potentials": {"InsideScoring": 8}}, 2)]
    + _tiers("Playmaking",
             ["Ball Handler", "Court Vision", "Floor General"],
             ["Both playmaking ceilings +6.",
              "Playmaking ceilings +6, and playmaking is 10% cheaper to train.",
              "Playmaking ceilings +10, and his playmaking ages half as fast."])
    + [_signature("Playmaking", "maestro", "Maestro",
                  "+9 Passing, +7 Handling, both ceilings +8.",
                  {"ratings": {"Passing": 9, "Handling": 7}, "potentials": {"Passing": 8, "Handling": 8}}, 1),
       _signature("Playmaking", "ankle-breaker", "Ankle Breaker",
                  "+9 Handling and its ceiling +8, +7 Quickness.",
                  {"ratings": {"Handling": 9, "Quickness": 7}, "potentials": {"Handling": 8}}, 2)]
    + _tiers("Defense",
             ["Defensive Stance", "Lockdown Instincts", "Anchor"],
             ["Every defensive ceiling +3.",
              "Defensive ceilings +3, and defense is 10% cheaper to train.",
              "Defensive ceilings +6, and his defense ages half as fast."])
    + [_signature("Defense", "rim-protector", "Rim Protector",
                  "+9 Blocking, +7 Post Defense, both ceilings +8.",
                  {"ratings": {"Blocking": 9, "PostDefense": 7}, "potentials": {"Blocking": 8, "PostDefense": 8}}, 1),
       _signature("Defense", "pickpocket", "Pickpocket",
                  "+9 Stealing, +7 Perimeter Defense, both ceilings +8.",
                  {"ratings": {"Stealing": 9, "PerimeterDefense": 7},
                   "potentials": {"Stealing": 8, "PerimeterDefense": 8}}, 2)]
    + _tiers("Rebounding",
             ["Box Out", "Glass Cleaner", "Rebound Machine"],
             ["Both rebounding ceilings +6.",
              "Rebounding ceilings +6, and rebounding is 10% cheaper to train.",
              "Rebounding ceilings +10, and his rebounding ages half as fast."])
    + [_signature("Rebounding", "vacuum", "Vacuum",
                  "+9 Def. Rebounding, +7 Off. Rebounding, both ceilings +8.",
                  {"ratings": {"DReb": 9, "OReb": 7}, "potentials": {"DReb": 8, "OReb": 8}}, 1),
       _signature("Rebounding", "putback-king", "Putback King",
                  "+9 Off. Rebounding, +7 Inside Scoring, both ceilings +8.",
                  {"ratings": {"OReb": 9, "InsideScoring": 7}, "potentials": {"OReb": 8, "InsideScoring": 8}}, 2)]
    + _tiers("Athletic",
             ["Conditioning", "Weight Room", "Longevity"],
             ["+1 Quickness, Jumping, Strength and Stamina.",
              "+1 to all four, and athletic training is 10% cheaper.",
              "+2 to all four, and his body ages half as fast."])
    + [_signature("Athletic", "iron-man", "Iron Man",
                  "+9 Stamina, +7 Strength.",
                  {"ratings": {"Stamina": 9, "Strength": 7}}, 1),
       _signature("Athletic", "pogo-stick", "Pogo Stick",
                  "+9 Jumping, +7 Quickness.",
                  {"ratings": {"Jumping": 9, "Quickness": 7}}, 2)]
)

BY_ID = {n["id"]: n for n in TREE}


# ---------------------------------------------------------------------------- the arithmetic
def sheet_after(ratings, potentials, effects):
    """(ratings, potentials) after one node, in STORE names (a potential is keyed by its rating).

    apply_upgrade_requests' node branch, step for step: ceilings first, each capped at 150; then
    ratings and tendencies, a rating with a ceiling capped at 150 and one without at 100, never
    below 0; and a ceiling never left below its rating.
    """
    r, p = dict(ratings or {}), dict(potentials or {})
    for k, d in (effects.get("potentials") or {}).items():
        p[k] = min(150, int(p.get(k, 0)) + int(d))
    moves = dict(effects.get("ratings") or {})
    moves.update(effects.get("tendency") or {})
    for k, d in moves.items():
        cap = 150 if k in POT_BY_RATING else 100
        r[k] = min(cap, max(0, int(r.get(k, 0)) + int(d)))
        if k in POT_BY_RATING and int(p.get(k, 0)) < r[k]:
            p[k] = r[k]
    return r, p


def bias_after(bias, effects):
    out = dict(bias or {})
    for k, d in (effects.get("bias") or {}).items():
        out[k] = int(out.get(k, 100)) + int(d)
    return out


def save_changes(values, effects):
    """{codec field: new value} for one node applied to a league.dat sheet (codec names)."""
    ratings = {k: values[k] for k in BRANCH_OF if k in values}
    potentials = {r: values[pot] for r, pot in POT_BY_RATING.items() if pot in values}
    r, p = sheet_after(ratings, potentials, effects)
    out = {k: v for k, v in r.items() if values.get(k) != v}
    for rating, v in p.items():
        pot = POT_BY_RATING[rating]
        if values.get(pot) != v:
            out[pot] = v
    return out


def breaker_changes(values, rating, size=3):
    """{codec field: new value} for one Cap Breaker on `rating`'s ceiling."""
    pot = POT_BY_RATING.get(rating)
    if not pot or pot not in values:
        return {}
    return {pot: min(150, int(values[pot]) + int(size))}


def shields(owned, nodes=None):
    """{branch: fraction of normal aging} from the nodes a character owns."""
    nodes = nodes or BY_ID
    out = {}
    for node_id in owned or ():
        node = nodes.get(node_id)
        if not node:
            continue
        for branch, factor in ((node.get("effects") or {}).get("shield") or {}).items():
            out[branch] = min(out.get(branch, 1.0), float(factor))
    return out


def flag(settings, key, default=False):
    """A true/false setting, however the store handed it over (jsonb true, "true", 1)."""
    value = (settings or {}).get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def tree_on(settings):
    """THE switch: settings.skill_tree_enabled, false until the commissioner opens the tree."""
    return flag(settings, "skill_tree_enabled")
