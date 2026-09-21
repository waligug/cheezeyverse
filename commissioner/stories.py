"""Build the public Cheezeyverse story feed from facts the game has recorded."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .leaguenews import snapshot
from .seasonbonus import award_counts, player_honours, season_award_winners

LEAGUE_NAMES = {"prep": "Prep", "college": "College", "pro": "Pro"}
STAT_LABELS = {"pts": "points", "reb": "rebounds", "ast": "assists",
               "stl": "steals", "blk": "blocks"}


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _season(label):
    found = re.search(r"\b(20\d\d)\b", str(label or ""))
    return int(found.group()) if found else None


def _event(kind, title, detail, character_ids, league, season=None, day=None,
           tone="neutral", **extra):
    ids = sorted(set(character_ids))
    stable = "|".join([kind, league or "", str(season or ""), str(day or ""), *ids, title])
    return {
        "id": hashlib.sha1(stable.encode()).hexdigest()[:16],
        "type": kind, "title": title, "detail": detail, "character_ids": ids,
        "league": league, "season": season, "day": day, "tone": tone, **extra,
    }


def _game_label(game):
    place = "vs" if game.get("home") else "at"
    result = "won" if game.get("won") else "lost"
    score = game.get("score") or []
    score_text = f" {score[0]}-{score[1]}" if len(score) == 2 else ""
    return f"{place} {game.get('opp', 'unknown')}; {result}{score_text}"


def _career_high(player, league):
    games = player.get("games") or []
    if not games:
        return None
    best = {key: max(games, key=lambda g: (int(g.get(key) or 0), int(g.get("day") or 0)))
            for key in STAT_LABELS}
    scorer = best["pts"]
    highs = ", ".join(f"{int(game.get(key) or 0)} {STAT_LABELS[key]}"
                      for key, game in best.items())
    return _event("career_high", f"{player['name']}'s career-high board", highs + ".",
                  [player["id"]], league, scorer.get("season"), scorer.get("day"), "good",
                  player=player["name"], game=_game_label(scorer))


def _streaks(player, league):
    games = sorted(player.get("games") or [], key=lambda g: (g.get("season") or 0, g.get("day") or 0))
    by_season = {}
    for game in games:
        by_season.setdefault(game.get("season"), []).append(game)
    events = []
    for season, rows in by_season.items():
        if len(rows) < 3:
            continue
        windows = [rows[i:i + 3] for i in range(len(rows) - 2)]
        hot = max(windows, key=lambda w: sum(int(g.get("pts") or 0) for g in w))
        hot_avg = sum(int(g.get("pts") or 0) for g in hot) / 3
        season_avg = sum(int(g.get("pts") or 0) for g in rows) / len(rows)
        if hot_avg >= max(8, season_avg * 1.35):
            events.append(_event(
                "hot_streak", f"{player['name']} caught fire",
                f"Averaged {hot_avg:.1f} points across three straight appearances, from day "
                f"{hot[0]['day']} through day {hot[-1]['day']}.", [player["id"]], league,
                season, hot[-1].get("day"), "good", player=player["name"]))
        cold = min(windows, key=lambda w: (sum(int(g.get("fgm") or 0) for g in w) /
                                           max(1, sum(int(g.get("fga") or 0) for g in w))))
        made = sum(int(g.get("fgm") or 0) for g in cold)
        attempts = sum(int(g.get("fga") or 0) for g in cold)
        if attempts >= 12 and made / attempts <= .25:
            events.append(_event(
                "cold_streak", f"The rim froze out {player['name']}",
                f"Shot {made}-for-{attempts} ({made / attempts:.1%}) across three appearances, "
                f"from day {cold[0]['day']} through day {cold[-1]['day']}.",
                [player["id"]], league, season, cold[-1].get("day"), "bad",
                player=player["name"]))
    out = []
    for kind in ("hot_streak", "cold_streak"):
        matches = [e for e in events if e["type"] == kind]
        if matches:
            out.append(max(matches, key=lambda e: (e.get("season") or 0, e.get("day") or 0)))
    return out


def _disaster(player, league):
    candidates = []
    for game in player.get("games") or []:
        misses = int(game.get("fga") or 0) - int(game.get("fgm") or 0)
        score = misses + 2 * int(game.get("to") or 0) + max(0, -int(game.get("pm") or 0)) / 3
        shots = int(game.get("fga") or 0)
        made = int(game.get("fgm") or 0)
        turnovers = int(game.get("to") or 0)
        plus_minus = int(game.get("pm") or 0)
        awful_shooting = shots >= 8 and made / shots <= .25 and plus_minus <= -5
        turnover_meltdown = turnovers >= 4 and plus_minus <= -8
        scoreless_volume = shots >= 5 and int(game.get("pts") or 0) <= 2
        if awful_shooting or turnover_meltdown or scoreless_volume:
            candidates.append((score, game))
    if not candidates:
        return None
    _, game = max(candidates, key=lambda item: item[0])
    turnovers = int(game.get("to") or 0)
    line = (f"{game.get('pts', 0)} points on {game.get('fgm', 0)}-for-{game.get('fga', 0)}, "
            f"{turnovers} {'turnover' if turnovers == 1 else 'turnovers'} and a "
            f"{int(game.get('pm') or 0):+d} plus/minus")
    return _event("disaster", f"A night {player['name']} would delete from the tape",
                  f"{line}; {_game_label(game)}.", [player["id"]], league,
                  game.get("season"), game.get("day"), "funny", player=player["name"])


def _playoffs(player, league):
    games = [g for g in player.get("games") or [] if g.get("playoff")]
    if not games:
        return None
    best = max(games, key=lambda g: (int(g.get("pts") or 0) + int(g.get("reb") or 0)
                                     + int(g.get("ast") or 0), int(g.get("pts") or 0)))
    wins = sum(bool(g.get("won")) for g in games)
    assists = int(best.get("ast") or 0)
    return _event("playoff", f"{player['name']} in the postseason",
                  f"{len(games)} playoff appearances, {wins}-{len(games) - wins}. Best line: "
                  f"{best.get('pts', 0)} points, {best.get('reb', 0)} rebounds and "
                  f"{assists} {'assist' if assists == 1 else 'assists'} ({_game_label(best)}).",
                  [player["id"]], league, best.get("season"), best.get("day"), "good",
                  player=player["name"])


def _rivalries(players, league):
    events = []
    for i, one in enumerate(players):
        for two in players[i + 1:]:
            one_nights = {(g.get("season"), g.get("day")): g for g in one.get("games") or []}
            meetings = []
            for game in two.get("games") or []:
                mine = one_nights.get((game.get("season"), game.get("day")))
                if mine and mine.get("opp") == game.get("team") and game.get("opp") == mine.get("team"):
                    meetings.append((mine, game))
            if not meetings:
                continue
            a_wins = sum(bool(a.get("won")) for a, _ in meetings)
            b_wins = len(meetings) - a_wins
            leader = (f"{one['name']} leads {a_wins}-{b_wins}" if a_wins > b_wins else
                      f"{two['name']} leads {b_wins}-{a_wins}" if b_wins > a_wins else
                      f"the series is tied {a_wins}-{b_wins}")
            last = meetings[-1][0]
            events.append(_event("rivalry", f"{one['name']} vs {two['name']}",
                                 f"They have shared the floor {len(meetings)} time"
                                 f"{'s' if len(meetings) != 1 else ''}; {leader}.",
                                 [one["id"], two["id"]], league, last.get("season"),
                                 last.get("day"), "neutral",
                                 players=[one["name"], two["name"]]))
    return events


def _mvp_watch(stats, characters, league):
    players = (stats or {}).get("players") or []
    scored = []
    for p in players:
        games = int(p.get("G") or 0)
        if games < 3:
            continue
        score = (int(p.get("PTS") or 0) + 1.2 * int(p.get("REB") or 0)
                 + 1.5 * int(p.get("AST") or 0) + 3 * int(p.get("STL") or 0)
                 + 3 * int(p.get("BLK") or 0)) / games
        scored.append((score, p))
    scored.sort(key=lambda item: (-item[0], item[1].get("name", "")))
    ranks = {p.get("name"): i + 1 for i, (_, p) in enumerate(scored)}
    season = _season((stats or {}).get("season"))
    out = []
    for c in characters:
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        if name not in ranks:
            continue
        out.append(_event(
            "award_race", f"{name} is #{ranks[name]} in the CV MVP Watch",
            f"Unofficial live rank among {len(scored)} qualified {LEAGUE_NAMES.get(league, league)} "
            "players, using per-game points, rebounds, assists, steals and blocks.",
            [str(c["id"])], league, season, None, "neutral", player=name,
            rank=ranks[name], field=len(scored)))
    return out


def build_story_feed(site, doc_root, characters, current_season=None):
    """Return the complete feed. Missing league exports simply contribute nothing."""
    site, doc_root = Path(site), Path(doc_root)
    characters = list(characters or [])
    by_name = {f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(): c for c in characters}
    known_ids = {str(c.get("id")) for c in characters}
    events = []
    for league, save in (("prep", "CV_Prep"), ("college", "CV_College"), ("pro", "CV_Pro")):
        games_data = _read(site / "leagues" / league / "games.json") or {}
        tracked = [p for p in games_data.get("characters", []) if str(p.get("id")) in known_ids]
        for player in tracked:
            for event in (_career_high(player, league), _disaster(player, league),
                          _playoffs(player, league)):
                if event:
                    events.append(event)
            events.extend(_streaks(player, league))
        events.extend(_rivalries(tracked, league))

        html_dir = doc_root / "leaguedata" / save / "html"
        stats = _read(site / "leagues" / league / "stats.json") or {}
        events.extend(_mvp_watch(stats, [c for c in characters if c.get("league") == league], league))

        counts = award_counts(html_dir)
        honours = {p["name"]: p for p in player_honours(html_dir)}
        season_winners = season_award_winners(html_dir)
        for name, c in by_name.items():
            awards, honour = counts.get(name, {}), honours.get(name, {})
            facts = []
            if awards.get("potw"):
                facts.append(f"{awards['potw']} Player of the Week")
            if awards.get("potm"):
                facts.append(f"{awards['potm']} Player of the Month")
            stars = sorted(honour.get("all_stars") or [])
            if stars:
                facts.append("All-Star: " + ", ".join(map(str, stars)))
            league_teams = honour.get("all_league") or {}
            for year, tier in sorted(league_teams.items()):
                suffix = "st" if tier == 1 else "nd" if tier == 2 else "rd"
                facts.append(f"{year} All-League {tier}{suffix} team")
            if name in season_winners:
                facts.append("season award winner")
            if facts:
                years = stars + list(league_teams.keys()) + ([current_season] if current_season else [])
                events.append(_event("award", f"{name}'s award shelf", "; ".join(facts) + ".",
                                     [str(c["id"])], league, max(years) if years else None,
                                     None, "good", player=name))

        for date, team, action in snapshot(html_dir):
            if not re.search(r"\btrad(?:e|ed|es|ing)\b", action, re.I):
                continue
            matched = [(name, c) for name, c in by_name.items()
                       if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", action, re.I)]
            if matched:
                events.append(_event("trade", "Trade wire", action,
                                     [str(c["id"]) for _, c in matched], league,
                                     _season(date), None, "neutral",
                                     player=", ".join(name for name, _ in matched),
                                     date=date, team=team))

    type_order = {"trade": 8, "award": 7, "playoff": 6, "rivalry": 5,
                  "career_high": 4, "hot_streak": 3, "cold_streak": 2,
                  "disaster": 1, "award_race": 0}
    events.sort(key=lambda e: (e.get("season") or 0, e.get("day") or 999,
                               type_order.get(e["type"], 0), e["title"]), reverse=True)
    players = {}
    for name, c in by_name.items():
        cid = str(c["id"])
        players[cid] = {"name": name, "league": c.get("league"),
                        "events": sum(cid in e["character_ids"] for e in events)}
    return {"generated": datetime.now().isoformat(timespec="seconds"),
            "events": events, "players": players}


def write_story_feed(site, doc_root, characters, current_season=None):
    data = build_story_feed(site, doc_root, characters, current_season)
    target = Path(site) / "data" / "stories.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data
