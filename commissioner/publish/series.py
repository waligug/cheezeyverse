"""Playoff series: every player's line in every playoff game, kept per season, and the series
summaries the site's series page reads (site/series.html).

WHERE IT COMES FROM. LeagueOutput.mdb, the same export headtohead.py reads: `Schedule` rows with
Type 2 are the playoff games (with scores and the box page's name), and `PlayerGameStats` has one
row per player per game. A team plays at most one game a day, so a line belongs to the game its
team played on its Seasonday.

WHY AN ARCHIVE. The MDB holds one season and FBPB3's rollover empties it (see gamesarchive.py),
so a postseason not written down before the offseason is gone. `universe/history/
series-<league>-<season>.json` holds every line of every playoff game. NOT `playoffs-...`: that
name belongs to statsarchive's postseason TOTALS, and sharing it once overwrote them. It is only ever
REPLACED BY A FILE WITH AT LEAST AS MANY GAMES - a rollover's empty export, or a stale one, can
never shrink a postseason that was captured - and never written for a season the bracket page
does not describe.

WHAT THE SITE GETS. `site/leagues/<key>/series-<season>.json`, rebuilt from the archive on every
publish (restyle can rmtree the league folder, the archive cannot be touched by it): each series
with its round, seeds, wins, games, per-player totals, a game MVP for every game and a series MVP.

THE MVPs use John Hollinger's Game Score, the standard single-game box-score rating:
    PTS + 0.4 FGM - 0.7 FGA - 0.4 (FTA - FTM) + 0.7 ORB + 0.3 DRB + STL + 0.7 AST + 0.7 BLK
        - 0.4 PF - TOV
Game MVP: the best Game Score on the team that won the game. Series MVP: the best TOTAL Game
Score on the team that won the series - total, not average, so a man who was great for seven
games beats one who was great for two.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "universe" / "history"

STATS = {
    "Minutes": "min", "Points": "pts", "Rebounds": "reb", "OffensiveRebounds": "oreb",
    "Assists": "ast", "Steals": "stl", "Blocks": "blk", "Turnovers": "to", "Fouls": "pf",
    "FGM": "fgm", "FGA": "fga", "FTM": "ftm", "FTA": "fta", "3PM": "tpm", "3PA": "tpa",
    "PlusMinus": "pm",
}
TOTALS = list(STATS.values())

SQL = {
    "games": "SELECT Day, Home, Away, HomeScore, AwayScore, BoxName FROM Schedule WHERE Type = 2",
    "lines": "SELECT * FROM PlayerGameStats WHERE Seasonday >= {first}",
    "names": "SELECT ID, Name FROM Player",
}


def _int(value, default=0):
    try:
        return int(float(str(value).strip() or default))
    except (TypeError, ValueError):
        return default


def path_for(key, season):
    return ARCHIVE / f"series-{key}-{int(season)}.json"


# ------------------------------------------------------------------------------ capture


def capture(mdb_path, key, season, query=None):
    """Every playoff game in the MDB with every player's line, or None when there are none."""
    if query is None:
        from ..headtohead import query
    games = query(mdb_path, SQL["games"])
    if not games:
        return None
    first = min(_int(g["Day"]) for g in games)
    lines = query(mdb_path, SQL["lines"].format(first=first))
    names = {_int(r["ID"]): str(r.get("Name") or "").strip() for r in query(mdb_path, SQL["names"])}

    out = {}
    for g in games:
        day = _int(g["Day"])
        out[(day, g["Home"])] = out[(day, g["Away"])] = {
            "day": day, "box": str(g.get("BoxName") or "").strip(),
            "home": g["Home"], "away": g["Away"],
            "hs": _int(g["HomeScore"]), "as": _int(g["AwayScore"]), "lines": [],
        }
    for row in lines:
        game = out.get((_int(row["Seasonday"]), row["Team"]))
        if game is None:
            continue            # a line from a day with no playoff game for his team
        line = {"id": _int(row["ID"]), "name": names.get(_int(row["ID"]), f"Player {row['ID']}"),
                "team": row["Team"], "start": str(row.get("Starter")).lower() == "true"}
        for col, short in STATS.items():
            line[short] = _int(row.get(col))
        game["lines"].append(line)
    unique = {id(g): g for g in out.values()}.values()
    played = [g for g in unique if g["hs"] or g["as"]]     # an unplayed fixture has 0-0
    if not played:
        return None
    played.sort(key=lambda g: (g["day"], g["box"]))
    return {"league": key, "season": int(season),
            "generated": datetime.now().isoformat(timespec="seconds"), "games": played}


def archive(payload):
    """Write the season's file unless a file with MORE games is already there. Returns the path
    written, or None when it kept what it had."""
    target = path_for(payload["league"], payload["season"])
    if target.exists():
        try:
            have = json.loads(target.read_text(encoding="utf-8"))
            if len(have.get("games") or []) > len(payload["games"]):
                return None
        except (OSError, ValueError):
            pass        # a damaged file is replaced by a good one
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)
    return target


def archived(key):
    """[(season, payload)] for a league, oldest first."""
    pattern = re.compile(rf"^series-{re.escape(str(key))}-(\d+)\.json$")
    out = []
    if ARCHIVE.exists():
        for path in sorted(ARCHIVE.iterdir()):
            m = pattern.match(path.name)
            if not m:
                continue
            try:
                out.append((int(m.group(1)), json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue    # a damaged year must not take the others down with it
    return out


# ---------------------------------------------------------------------------- summarise


def game_score(line):
    g = lambda k: _int(line.get(k))
    return round(g("pts") + 0.4 * g("fgm") - 0.7 * g("fga") - 0.4 * (g("fta") - g("ftm"))
                 + 0.7 * g("oreb") + 0.3 * (g("reb") - g("oreb")) + g("stl") + 0.7 * g("ast")
                 + 0.7 * g("blk") - 0.4 * g("pf") - g("to"), 1)


def _round_name(number, rounds):
    if number == rounds:
        return "League Finals"
    if number == rounds - 1 and rounds >= 3:
        return "Conference Finals"
    if number == 1:
        return "1st Round"
    return f"Round {number}"


def summarise(payload, lengths=(), seeds=None):
    """The site's view of one postseason. `lengths` is LeagueSpec.playoff_rounds (series lengths,
    leading zeros for rounds the bracket does not have); `seeds` maps team -> seed."""
    seeds = seeds or payload.get("seeds") or {}
    games = sorted(payload.get("games") or [], key=lambda g: (g["day"], g.get("box", "")))
    by_pair = {}
    for g in games:
        by_pair.setdefault(frozenset((g["home"], g["away"])), []).append(g)

    # A team plays one series per round, one after another: a series' round is one more than the
    # number of series either team had already started.
    started = sorted(by_pair.items(), key=lambda kv: kv[1][0]["day"])
    played_before, rounds_of = {}, {}
    for pair, _ in started:
        number = 1 + max(played_before.get(t, 0) for t in pair)
        rounds_of[pair] = number
        for t in pair:
            played_before[t] = played_before.get(t, 0) + 1
    live = [int(n) for n in lengths if int(n) > 0]
    total_rounds = max(len(live), max(rounds_of.values(), default=0))

    out = []
    for pair, series_games in started:
        number = rounds_of[pair]
        length = live[number - 1] if number - 1 < len(live) else None
        wins = {t: 0 for t in pair}
        rows = []
        for n, g in enumerate(series_games, 1):
            winner = g["home"] if g["hs"] > g["as"] else g["away"]
            wins[winner] += 1
            lines = [dict(l, gmsc=game_score(l)) for l in g["lines"]]
            best = max((l for l in lines if l["team"] == winner), key=lambda l: (l["gmsc"], l["pts"]),
                       default=None)
            rows.append({"n": n, "day": g["day"], "box": g.get("box", ""), "home": g["home"],
                         "away": g["away"], "hs": g["hs"], "as": g["as"], "winner": winner,
                         "mvp": best and {"id": best["id"], "name": best["name"], "team": best["team"],
                                          "gmsc": best["gmsc"],
                                          **{k: best[k] for k in ("pts", "reb", "ast", "stl", "blk")}},
                         "lines": lines})
        clinch = (length + 1) // 2 if length else None
        leader = max(wins, key=wins.get)
        decided = clinch is not None and wins[leader] >= clinch
        # higher seed first, as the bracket lists them; between equal seeds the winner, and
        # failing that the home team of game 1 - so a final between two #1s reads "Hams 4-3".
        first = series_games[0]["home"]
        teams = sorted(pair, key=lambda t: (seeds.get(t, 99), not (decided and t == leader), t != first))

        players = {t: {} for t in teams}
        for row in rows:
            for l in row["lines"]:
                p = players[l["team"]].setdefault(l["id"], {"id": l["id"], "name": l["name"],
                                                           "team": l["team"], "gp": 0, "gs": 0,
                                                           "gmsc": 0.0, **{k: 0 for k in TOTALS}})
                p["gp"] += 1
                p["gs"] += 1 if l["start"] else 0
                p["gmsc"] = round(p["gmsc"] + l["gmsc"], 1)
                for k in TOTALS:
                    p[k] += _int(l.get(k))
        mvp = None
        if decided:
            pick = max(players[leader].values(), key=lambda p: (p["gmsc"], p["pts"]), default=None)
            if pick:
                mvp = {k: pick[k] for k in ("id", "name", "team", "gp", "gmsc", *TOTALS)}
        out.append({
            "id": "~".join(sorted(t.lower() for t in pair)),
            "round": number, "round_name": _round_name(number, total_rounds), "length": length,
            "teams": [{"name": t, "seed": seeds.get(t), "wins": wins[t]} for t in teams],
            "winner": leader if decided else None, "mvp": mvp, "games": rows,
            "players": {t: sorted(players[t].values(), key=lambda p: (-p["min"], p["name"]))
                        for t in teams},
        })
    out.sort(key=lambda s: (s["round"], s["games"][0]["day"], s["id"]))
    final = [s for s in out if s["round"] == total_rounds]
    champion = final[0]["winner"] if len(final) == 1 else None
    return {"league": payload.get("league"), "season": payload.get("season"),
            "generated": payload.get("generated"), "rounds": total_rounds, "champion": champion,
            "series": out}


# ------------------------------------------------------------------------------ publish


def _seeds(html_dir):
    """team -> seed, from the bracket page's own "#seed Team wins" entries."""
    from .restyle import bracket_entries
    path = Path(html_dir) / "playoffs.htm"
    if not path.exists():
        return {}, None
    html = path.read_text(encoding="latin-1", errors="replace")
    year = re.search(r"(\d{4})\s+Playoff Bracket", html)
    return ({e["team"]: e["seed"] for e in bracket_entries(html)},
            int(year.group(1)) if year else None)


def write(src, dst, key, season, refresh=True, query=None):
    """Capture this season's playoffs (when `refresh`), then write a series file per archived
    season into `dst`. Never fatal to a publish: returns a small report, or raises only on a
    bug the caller logs."""
    from ..universe import config as cfg
    src, dst = Path(src), Path(dst)
    seeds, bracket_year = _seeds(src)
    report = {"captured": None, "seasons": []}
    if refresh and season:
        mdb = src.parent / "LeagueOutput.mdb"
        # Only when the bracket page describes THIS season: after a rollover it still shows the
        # last one, and the export beside it is the new season's.
        if mdb.exists() and bracket_year == int(season):
            payload = capture(mdb, key, season, query=query)
            if payload:
                payload["seeds"] = seeds
                written = archive(payload)
                report["captured"] = {"games": len(payload["games"]), "written": bool(written)}
    lengths = cfg.BY_KEY[key].playoff_rounds
    for year, payload in archived(key):
        summary = summarise(payload, lengths)
        (dst / f"series-{year}.json").write_text(json.dumps(summary, separators=(",", ":")),
                                                  encoding="utf-8")
        report["seasons"].append(year)
    return report
