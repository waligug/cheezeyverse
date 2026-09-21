"""Build the public Cheezeyverse story feed from facts the game has recorded."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .leaguenews import snapshot
from .seasonbonus import (
    award_counts, bracket_season, export_date, player_honours, season_award_winners,
)

LEAGUE_NAMES = {"prep": "Prep", "college": "College", "pro": "Pro"}
STAT_LABELS = {"pts": ("point", "points"), "reb": ("rebound", "rebounds"),
               "ast": ("assist", "assists"), "stl": ("steal", "steals"),
               "blk": ("block", "blocks")}


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
    highs = ", ".join(
        f"{int(game.get(key) or 0)} {STAT_LABELS[key][0 if int(game.get(key) or 0) == 1 else 1]}"
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


def _player_number(page):
    found = re.search(r"player(\d+)", str(page or ""), re.I)
    return int(found.group(1)) if found else None


def _league_characters(characters, league):
    """Characters identified in this save by the stable FBPB player id."""
    return [c for c in characters
            if (c.get("league_player_ids") or {}).get(league) is not None]


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
    ranks = {_player_number(p.get("page")): i + 1 for i, (_, p) in enumerate(scored)}
    season = _season((stats or {}).get("season"))
    out = []
    for c in characters:
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        pid = int((c.get("league_player_ids") or {}).get(league))
        if pid not in ranks:
            continue
        out.append(_event(
            "award_race", f"{name} is #{ranks[pid]} in the CV MVP Watch",
            f"Unofficial live rank among {len(scored)} qualified {LEAGUE_NAMES.get(league, league)} "
            "players, using per-game points, rebounds, assists, steals and blocks.",
            [str(c["id"])], league, season, None, "neutral", player=name,
            rank=ranks[pid], field=len(scored)))
    return out


def _source_season(html_dir, fallback=None):
    # During the following regular season playoffs.htm still describes last year's bracket.
    # The caller's live-season setting is therefore the authority when it is available.
    return fallback or _season(export_date(html_dir)) or bracket_season(html_dir)


def _award_and_trade_events(html_dir, league, characters, season=None):
    """Immutable news from one live or archived HTML export.

    Honours use the player-page id. Name-only tables are accepted only when the exported roster
    contains exactly one player with that name; an ambiguous award is safer omitted than attached
    to the wrong person.
    """
    html_dir = Path(html_dir)
    if not html_dir.is_dir():
        return []
    season = _source_season(html_dir, season)
    league_chars = _league_characters(characters, league)
    if not league_chars:
        return []
    by_pid = {int((c.get("league_player_ids") or {})[league]): c for c in league_chars}
    name_counts = {}
    honours = player_honours(html_dir, by_pid)
    for player in honours:
        name_counts[player["name"]] = name_counts.get(player["name"], 0) + 1
    unique_names = {}
    for c in league_chars:
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        if name_counts.get(name) == 1:
            unique_names[name] = c

    events = []
    for honour in honours:
        c = by_pid.get(_player_number(honour.get("page")))
        if not c:
            continue
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        for year in sorted(honour.get("all_stars") or []):
            events.append(_event("award", f"{name} made the All-Star team",
                                 f"Official {year} {LEAGUE_NAMES.get(league, league)} All-Star selection.",
                                 [str(c["id"])], league, year, None, "good", player=name))
        for year, tier in sorted((honour.get("all_league") or {}).items()):
            suffix = "st" if tier == 1 else "nd" if tier == 2 else "rd"
            events.append(_event("award", f"{name} made All-League {tier}{suffix} team",
                                 f"Official {year} {LEAGUE_NAMES.get(league, league)} selection.",
                                 [str(c["id"])], league, year, None, "good", player=name))

    counts = award_counts(html_dir)
    winners = season_award_winners(html_dir)
    for name, c in unique_names.items():
        awards = counts.get(name, {})
        facts = []
        if awards.get("potw"):
            facts.append(f"{awards['potw']} Player of the Week selection"
                         f"{'s' if awards['potw'] != 1 else ''}")
        if awards.get("potm"):
            facts.append(f"{awards['potm']} Player of the Month selection"
                         f"{'s' if awards['potm'] != 1 else ''}")
        if facts:
            events.append(_event("award", f"{name}'s {season} weekly and monthly awards",
                                 "; ".join(facts) + ".", [str(c["id"])], league,
                                 season, None, "good", player=name))
        if name in winners:
            events.append(_event("award", f"{name} won a season award",
                                 f"Official {season} season-award selection.", [str(c["id"])],
                                 league, season, None, "good", player=name))

    for date, team, action in snapshot(html_dir):
        if not re.search(r"\btrad(?:e|ed|es|ing)\b", action, re.I):
            continue
        matched = [(name, c) for name, c in unique_names.items()
                   if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", action, re.I)]
        if matched:
            events.append(_event("trade", "Trade wire", action,
                                 [str(c["id"]) for _, c in matched], league,
                                 _season(date) or season, None, "neutral",
                                 player=", ".join(name for name, _ in matched),
                                 date=date, team=team))
    return events


def _archived_html(site, save):
    """Finished-season exports retained by seasonflow.archive_finished()."""
    root = Path(site).parent / "backups"
    return sorted(root.glob(f"*-offseason-{save}/finished-season/html")) if root.is_dir() else []


def build_story_feed(site, doc_root, characters, current_season=None):
    """Return the complete feed. Missing league exports simply contribute nothing."""
    site, doc_root = Path(site), Path(doc_root)
    characters = list(characters or [])
    previous = _read(site / "data" / "stories.json") or {}
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
        events.extend(_mvp_watch(stats, _league_characters(characters, league), league))
        events.extend(_award_and_trade_events(html_dir, league, characters, current_season))
        for archived in _archived_html(site, save):
            events.extend(_award_and_trade_events(archived, league, characters))

    type_order = {"trade": 8, "award": 7, "playoff": 6, "rivalry": 5,
                  "career_high": 4, "hot_streak": 3, "cold_streak": 2,
                  "disaster": 1, "award_race": 0}
    # Keep immutable news that an earlier publish recorded even if a rollover backup is later
    # pruned or the site is published from a replacement machine. Current-season award counts
    # are deliberately rebuilt because they grow during the year.
    for event in previous.get("events", []):
        if (event.get("type") in ("award", "trade") and event.get("season")
                and current_season and int(event["season"]) < int(current_season)):
            events.append(event)
    # A current export can repeat historical player-page honours also found in an archived
    # export. Stable event ids collapse those copies before sorting.
    events = list({event["id"]: event for event in events}.values())
    events.sort(key=lambda e: (e.get("season") or 0, e.get("day") or 999,
                               type_order.get(e["type"], 0), e["title"]), reverse=True)
    players = {}
    for c in characters:
        name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
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
