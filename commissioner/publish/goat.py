"""The GOAT score's raw material, per league: `goat.json` beside careers.json.

Nate, 2026-09-25: a GOAT formula across all three leagues, with the weighting adjustable on the
page, that considers team load - did he carry his team or get carried. So this module computes
the COMPONENTS and the browser combines them with whatever weights the sliders say. No score
is fixed here; changing the formula is a page change, not a republish.

THE COMPONENTS, per player:

  season value   His efficiency ABOVE REPLACEMENT that season (replacement = the 20th percentile
                 per-minute efficiency among players with 10+ games), scaled so 100 = the average
                 of that season's top ten. Scaling per season is what makes a broken year (2031
                 pro, half the league floored) count like any other instead of inflating
                 whoever played in it.
  career         Sum of his season values - longevity.
  peak           Mean of his best three - dominance.
  playoffs       The same measure over his playoff lines, summed.
  share          His season value over his team's total that season: how much of the team he
                 was. 0.2 is a fair share for a starter; 0.35+ is carrying.
  titles         Seasons the game names him champion, each with the share he had that year, so
                 the page can tell a ring he carried from a ring he rode.
  honours        MVP, Playoff MVP, All-League 1st/2nd/3rd, All-Defensive, All-Star, DPOY,
                 Rookie of the Year - as the game's own player pages list them.

HONOURS ONLY COUNT IN SEASONS HE PLAYED HERE. A character is stamped onto a reserve row that
already existed, and FBPB3 keeps a player's history on the ROW - so a page can list honours won
by whoever held it before him (the same reason publish.strip_foreign_history exists). Any honour
for a season the stats archive does not show him playing in this league is dropped.

HONOURS ARE KEPT, not re-read. The player pages only exist for players still in the save, and
retired players' pages survive only in the offseason backups, which get pruned. Every publish
merges what it can see into universe/history/honours-<league>.json and never removes from it.
"""
from __future__ import annotations

import glob
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "universe" / "history"
DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
SAVES = {"prep": "CV_Prep", "college": "CV_College", "pro": "CV_Pro"}

MIN_GAMES = 10              # a season shorter than this is not a season to rank anybody by
REPLACEMENT_PCT = 0.20      # replacement level = this percentile of per-minute efficiency
FAIR_SHARE = 0.20           # one starter's worth of a team
MIN_CAREER = 40             # below this and no honours, a career is not published here

# item text on a player page (after "2033 CV ") -> our key. Order matters: the more specific
# phrase first ("All-Star Game MVP" must not count as an All-Star selection).
HONOURS = [
    (re.compile(r"Playoff MVP$"), "finals_mvp"),
    (re.compile(r"All-Star Game MVP$"), None),
    (re.compile(r"Rookie Game"), None),
    (re.compile(r"Most Valuable Player$"), "mvp"),
    (re.compile(r"Defensive Player of the Year$"), "dpoy"),
    (re.compile(r"Rookie of the Year$"), "roy"),
    (re.compile(r"All-League (First|1st) Team$"), "all_league_1"),
    (re.compile(r"All-League (Second|2nd) Team$"), "all_league_2"),
    (re.compile(r"All-League (Third|3rd) Team$"), "all_league_3"),
    (re.compile(r"All-Defensive"), "all_defensive"),
    (re.compile(r"All-Star$"), "all_star"),
    (re.compile(r"Champion$"), "title"),
]
ITEM = re.compile(r"^(20\d\d)\s+(.+)$")


def efficiency(p):
    """The site's EFF: points + boards + assists + steals + blocks, minus misses and turnovers."""
    return (p.get("Points", 0) + p.get("Rebounds", 0) + p.get("Assists", 0) + p.get("Steals", 0)
            + p.get("Blocks", 0) - (p.get("FGA", 0) - p.get("FGM", 0))
            - (p.get("FTA", 0) - p.get("FTM", 0)) - p.get("Turnovers", 0))


def _who(name, dob=""):
    return (str(name or "").strip().lower(), str(dob or "").strip())


def season_values(players):
    """{(name, dob): (value, team, games)} for one season's lines. See the module docstring."""
    real = [p for p in players if p.get("Games", 0) >= MIN_GAMES and p.get("Minutes", 0) > 0]
    if not real:
        return {}
    per = sorted(efficiency(p) / p["Minutes"] for p in real)
    replacement = per[int(len(per) * REPLACEMENT_PCT)]
    raw = {}
    for p in players:
        if p.get("Games", 0) <= 0:
            continue
        above = max(0.0, efficiency(p) - replacement * p.get("Minutes", 0))
        raw[_who(p.get("name"), p.get("dob"))] = (above, p.get("team", ""), p.get("Games", 0))
    top = sorted((v for v, _, _ in raw.values()), reverse=True)[:10]
    scale = statistics.mean(top) if top and statistics.mean(top) > 0 else 1.0
    return {k: (100.0 * v / scale, team, g) for k, (v, team, g) in raw.items()}


# ---- honours ---------------------------------------------------------------------------------
def _page_honours(page):
    """(name, {season: [key, ...]}) from one player page, or None."""
    from ..seasonbonus import _text
    items = [i.strip() for i in _text(page).split("&nbsp;")]
    name = ""
    for i, item in enumerate(items):
        if re.match(r"#\d+\s+\S+\s*\|", item):
            name = next((prev for prev in reversed(items[:i]) if prev), "")
            break
    if not name:
        return None
    found = defaultdict(list)
    for item in items:
        m = ITEM.match(item)
        if not m or len(item) > 80:
            continue
        season, text = int(m.group(1)), m.group(2).strip()
        for pattern, key in HONOURS:
            if pattern.search(text):
                if key:
                    found[season].append(key)
                break
    return name, found


def _html_dirs(key):
    """Every export we can still read for this league: the live one and every archived season."""
    save = SAVES[key]
    dirs = [DOCS / "leaguedata" / save / "html"]
    for d in sorted(glob.glob(str(ROOT / "backups" / f"*-{save}" / "finished-season" / "html"))):
        dirs.append(Path(d))
    return [d for d in dirs if (d / "players").is_dir()]


def collect_honours(key, dirs=None):
    """{lower name: {season(str): [keys]}}, merged into the durable honours file and returned.

    An ARCHIVED export never changes, so each is read once and remembered in `read_dirs`; only
    the live export is re-read every publish. Reading all ten every time cost 80 s a league.
    """
    path = HISTORY / f"honours-{key}.json"
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    done = set(kept.get("read_dirs") or [])
    merged = {n: {s: list(v) for s, v in seasons.items()}
              for n, seasons in (kept.get("players") or {}).items()}
    live = DOCS / "leaguedata" / SAVES[key] / "html"
    for d in (dirs if dirs is not None else _html_dirs(key)):
        if str(d) in done and Path(d) != live:
            continue
        for page in (d / "players").glob("player*.htm"):
            try:
                got = _page_honours(page)
            except Exception:                                           # noqa: BLE001
                continue
            if not got:
                continue
            name, found = got
            slot = merged.setdefault(name.strip().lower(), {})
            for season, keys in found.items():
                have = slot.setdefault(str(season), [])
                for k in keys:
                    if k not in have:
                        have.append(k)
        if Path(d) != live:
            done.add(str(d))
    HISTORY.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"read_dirs": sorted(done), "players": merged},
                              separators=(",", ":"), sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return merged


def champions(key):
    """{season: (champion team abbrev, finals MVP name)} from the live champs.htm.

    The page keeps one row per season for the life of the league, so the live export alone is
    the whole history. A second source for titles: the player page is authoritative when it has
    the line, but the team that won is not in doubt either, and a man who was on that roster
    that season was on the team that won it.
    """
    from ..seasonbonus import _text
    from ..universe import config as cfg
    text = _text(DOCS / "leaguedata" / SAVES[key] / "html" / "champs.htm").replace("&nbsp;", " ")
    abbrev = {t.nickname.lower(): t.abbrev for t in cfg.BY_KEY[key].teams}
    out = {}
    for m in re.finditer(r"(20\d\d)\s+(.+?)\s+\d+\s+.+?\s+\d+\s+(?:PG|SG|SF|PF|C)\s+(.+?)(?=\s+20\d\d\s|\s+Fast Break|$)", text):
        team = abbrev.get(m.group(2).strip().lower())
        if team:
            out[int(m.group(1))] = (team, m.group(3).strip())
    return out


# ---- the components ----------------------------------------------------------------------------
def build(key, history=None, playoff_history=None, honours=None):
    """The goat.json payload for one league."""
    from .. import statsarchive
    history = history if history is not None else statsarchive.archived_seasons(key)
    playoff_history = (playoff_history if playoff_history is not None
                       else statsarchive.archived_seasons(key, "playoffs"))
    honours = honours if honours is not None else collect_honours(key)
    try:
        champs = champions(key)
    except Exception:                                                   # noqa: BLE001
        champs = {}

    people = {}
    for season, payload in history:
        values = season_values(payload.get("players") or [])
        team_total = defaultdict(float)
        for (v, team, _g) in values.values():
            team_total[team] += v
        names = {_who(p.get("name"), p.get("dob")): p.get("name", "") for p in payload.get("players") or []}
        for who, (v, team, games) in values.items():
            row = people.setdefault(who, {"name": names.get(who, who[0]), "dob": who[1],
                                          "seasons": [], "playoffs": 0.0})
            share = v / team_total[team] if team_total[team] > 0 else 0.0
            row["seasons"].append({"s": season, "team": team, "g": games,
                                   "v": round(v, 1), "share": round(share, 3)})
    for season, payload in playoff_history:
        for who, (v, _team, _g) in season_values(payload.get("players") or []).items():
            if who in people:
                people[who]["playoffs"] += v

    out = []
    for who, row in people.items():
        seasons = row["seasons"]
        ranked = [s for s in seasons if s["g"] >= MIN_GAMES]
        best = sorted((s["v"] for s in ranked), reverse=True)[:3]
        played = {s["s"] for s in seasons}
        counts = defaultdict(int)
        titles = []
        # A NAME CAN BELONG TO TWO PEOPLE. Honours are keyed by name (the page does not state a
        # birthday), so they are only credited to a name that is unique in this league's
        # archive; a shared name gets none rather than both getting each other's.
        title_seasons = set()
        for s_str, keys in (honours.get(who[0]) or {}).items():
            s = int(s_str)
            if s not in played:
                continue                  # somebody else's season on the same row
            for k in set(keys):
                if k == "title":
                    title_seasons.add(s)
                else:
                    counts[k] += 1
        for x in seasons:
            champ = champs.get(x["s"])
            if champ and champ[0] == x["team"]:
                title_seasons.add(x["s"])
        for s in sorted(title_seasons):
            share = next((x["share"] for x in seasons if x["s"] == s), 0.0)
            titles.append({"s": s, "share": share})
        weighted = [s for s in ranked if s["g"] > 0]
        load = (sum(s["share"] * s["g"] for s in weighted) / sum(s["g"] for s in weighted)
                if weighted else 0.0)
        out.append({
            "name": row["name"], "dob": row["dob"],
            "from": min(played), "to": max(played), "years": len(played),
            "team": sorted(seasons, key=lambda x: x["s"])[-1]["team"],
            "career": round(sum(s["v"] for s in seasons), 1),
            "peak": round(statistics.mean(best), 1) if best else 0.0,
            "playoffs": round(row["playoffs"], 1),
            "load": round(load, 3),
            "carry": round(sum(max(0.0, s["share"] - FAIR_SHARE) * 100 for s in ranked), 1),
            "titles": sorted(titles, key=lambda t: t["s"]),
            "honours": dict(counts),
            "seasons": sorted(seasons, key=lambda x: x["s"]),
        })
    dupes = defaultdict(int)
    for r in out:
        dupes[r["name"].strip().lower()] += 1
    for r in out:
        if dupes[r["name"].strip().lower()] > 1:
            r["titles"], r["honours"], r["shared_name"] = [], {}, True
    out.sort(key=lambda r: -(r["career"] * 0.5 + r["peak"] * 2))
    # SIZE: every season line for every body the game ever made was ~300 KB a league. A GOAT board
    # has no use for a career that never produced anything and never won anything, so those go;
    # everybody with a real career, a ring or an honour stays.
    out = [r for r in out if r["career"] >= MIN_CAREER or r["titles"] or r["honours"]]
    return {"league": key, "generated": datetime.now().isoformat(timespec="seconds"),
            "fair_share": FAIR_SHARE,
            "seasons": [s for s, _ in history], "players": out}


def write(dst, key):
    """Write goat.json into the league's published folder. Never fatal, like its siblings."""
    try:
        payload = build(key)
        if not payload["players"]:
            return None
        target = Path(dst) / "goat.json"
        target.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        return {"players": len(payload["players"]), "seasons": len(payload["seasons"])}
    except Exception as exc:                                            # noqa: BLE001
        return {"error": str(exc)}
