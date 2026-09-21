"""The interesting bits of a season, in the words a group chat would use.

WHY THIS EXISTS. The offseason report named who grew and who improved most, and stopped there.
Everything that actually happened on a court - the night somebody went for 24, the fact that one
of them led his whole league in blocks, that two of them met twice and one swept it - was sitting
in data nobody read. A count is a log line; a scoreline is news.

WHERE THE DATA COMES FROM, and why it is not parsed again here. Every publish already writes
`site/leagues/<key>/stats.json` (season totals, league ranks, true shooting, the standings table
and the playoff seeds) and `games.json` (every character's game-by-game lines, with the score,
the opponent and whether it was a playoff game). Both are current as of the last sim, which at
offseason time is the end of the season. Re-parsing four hundred player pages to learn what two
files on disk already say would be a second implementation of the four parsing traps documented
in seasonbonus, and a slow one.

EVERY LINE IS EARNED. Nothing here says "a good season" or "solid numbers". A line appears only
when a specific number supports it, and if the data is missing the line is simply absent - the
report is shorter rather than vaguer. That is also why there is no "most improved" here: the
offseason already names one, from rating history, and a second opinion computed a different way
would just disagree with it in public.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ARCHIVE = ROOT / "universe" / "history"

# Fewer than this and a rate is noise: a man who played twice and shot 100% did not shoot 100%.
MIN_GAMES = 8
# A league placing is only worth saying out loud this high up.
NOTABLE_RANK = 10
CATEGORY_WORD = {"PTS": "points", "REB": "rebounds", "AST": "assists",
                 "STL": "steals", "BLK": "blocks"}
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def _ordinal(n):
    return ORDINAL.get(n, f"{n}th")


def _load(league, name):
    path = SITE / "leagues" / league / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _our_games(games_json, season):
    """{name: [game, ...]} for this season only, characters with any minutes."""
    out = {}
    for c in (games_json or {}).get("characters") or []:
        rows = [g for g in (c.get("games") or [])
                if int(g.get("season") or 0) == int(season) and (g.get("min") or 0) > 0]
        if rows:
            out[c.get("name")] = rows
    return out


def _best_game(rows):
    """His loudest night, scored on points and then on everything else as a tie-break."""
    return max(rows, key=lambda g: (g.get("pts") or 0,
                                    (g.get("reb") or 0) + (g.get("ast") or 0)))


def _line_for_game(name, g):
    bits = [f'{g.get("pts", 0)} pts']
    if (g.get("reb") or 0) >= 5:
        bits.append(f'{g["reb"]} reb')
    if (g.get("ast") or 0) >= 4:
        bits.append(f'{g["ast"]} ast')
    if (g.get("blk") or 0) >= 3:
        bits.append(f'{g["blk"]} blk')
    if (g.get("stl") or 0) >= 3:
        bits.append(f'{g["stl"]} stl')
    score = g.get("score") or []
    where = "beating" if g.get("won") else "losing to"
    result = f'{where} the {g.get("opp")}'
    if len(score) == 2:
        result = f'{where} the {g.get("opp")} {score[0]}-{score[1]}'
    tag = " in the playoffs" if g.get("playoff") else ""
    return f'**{name}** - {", ".join(bits)}, {result}{tag}'


def _doubles(rows):
    dd = sum(1 for g in rows if sum(1 for k in ("pts", "reb", "ast", "stl", "blk")
                                    if (g.get(k) or 0) >= 10) >= 2)
    td = sum(1 for g in rows if sum(1 for k in ("pts", "reb", "ast", "stl", "blk")
                                    if (g.get(k) or 0) >= 10) >= 3)
    return dd, td


def for_league(league, season, names):
    """Discord lines about one league. `names` is the set of our characters in it."""
    stats, games = _season_stats(league, season), _load(league, "games.json")
    if not stats and not games:
        return []
    lines = []
    ours = _our_games(games, season) if games else {}
    ours = {n: r for n, r in ours.items() if n in names} if names else ours
    if stats and stats.get("season") is not None and _season_number(stats["season"]) != int(season):
        stats = None  # Never attach next season's standings to archived games.
    by_name = {p["name"]: p for p in (stats or {}).get("players") or []}
    table = {r["name"]: r for r in (stats or {}).get("table") or []}

    # ---- the single best night anybody had -------------------------------------------------
    if ours:
        who, rows = max(ours.items(), key=lambda kv: _best_game(kv[1]).get("pts") or 0)
        lines.append("**Game of the season** · " + _line_for_game(who, _best_game(rows)))

    # ---- where each of them finished in the league ------------------------------------------
    placed = []
    for name in sorted(ours):
        row = by_name.get(name)
        if not row:
            continue
        got = [(cat, row["rank"][cat], row.get(cat, 0))
               for cat in (row.get("rank") or {})
               if row["rank"][cat] <= NOTABLE_RANK]
        got.sort(key=lambda t: t[1])
        bits = ", ".join(f'{_ordinal(rank)} in {CATEGORY_WORD.get(cat, cat)} ({value})'
                         for cat, rank, value in got[:2])
        # 'w' and 'l', which is what standings_rows emits. Reading it as wins/losses printed
        # every team's record as "?-?" - the kind of thing that looks like missing data and is
        # really a misspelt key.
        rec = table.get(row.get("team")) or {}
        record = (f' · {row.get("team")} {rec["w"]}-{rec["l"]}'
                  if "w" in rec and "l" in rec else "")
        placed.append(f'- **{name}** {bits}{record}')
    if placed:
        lines.append("")
        lines.append("**Where they finished**")
        lines.extend(placed)

    # ---- double-doubles, which is the stat people actually brag about -------------------------
    dds = []
    for name, rows in ours.items():
        dd, td = _doubles(rows)
        if td:
            dds.append((td * 100 + dd, f'{name} {td} triple-double{"s" if td > 1 else ""}'))
        elif dd:
            dds.append((dd, f'{name} {dd}'))
    if dds:
        dds.sort(reverse=True)
        lines.append(f'**Double-doubles** · ' + ", ".join(t for _, t in dds[:5]))

    # ---- one of them met another one ---------------------------------------------------------
    # Said from the WINNER's side, once per pair. Reporting it from whichever name came first
    # alphabetically produced a wall of "Chris Zimmer won 0", which is true, unreadable, and
    # manages to be less interesting than the thing that actually happened.
    meetings, seen = [], set()
    teams = {name: rows[0].get("team") for name, rows in ours.items() if rows}
    for a, rows in ours.items():
        for b, team_b in teams.items():
            if a == b or (b, a) in seen or (a, b) in seen or not team_b:
                continue
            head = [g for g in rows if g.get("opp") == team_b]
            if not head:
                continue
            seen.add((a, b))
            a_won = sum(1 for g in head if g.get("won"))
            b_won = len(head) - a_won
            if a_won == b_won:
                meetings.append(f'{a} and {b} split their {len(head)}')
            else:
                winner, w, loser, l = ((a, a_won, b, b_won) if a_won > b_won
                                       else (b, b_won, a, a_won))
                swept = " (swept)" if l == 0 else ""
                meetings.append(f'{winner} took {w} of {len(head)} from {loser}{swept}')
    if meetings:
        lines.append("")
        lines.append("**Head to head** · " + "; ".join(sorted(meetings)[:4]))

    # ---- the cold streak nobody wants named, which is why it is funny -------------------------
    cold = []
    for name, rows in ours.items():
        tpa = sum(g.get("tpa") or 0 for g in rows)
        tpm = sum(g.get("tpm") or 0 for g in rows)
        if len(rows) >= MIN_GAMES and tpa >= 15 and tpm * 5 <= tpa:      # 20% or worse
            cold.append(f'{name} {tpm}-for-{tpa} from three')
    if cold:
        lines.append("**Ice cold** · " + ", ".join(cold[:3]))

    # ---- and what the league itself did --------------------------------------------------------
    best = (stats or {}).get("players") or []
    if best:
        top = best[0]
        lines.append(f'**{league.title()} league leader** · {top["name"]} '
                     f'({top.get("PTS", 0)} pts for the {top.get("team")})')
    return lines


def for_season(season, store=None, leagues=("prep", "college", "pro")):
    """Every league's takeaways, headed and joined, ready to post. [] when there is nothing."""
    out = []
    for key in leagues:
        names = set()
        if store is not None:
            try:
                names = {f'{c["first_name"]} {c["last_name"]}'
                         for c in store.characters(league=key)}
            except Exception:                                       # noqa: BLE001
                names = set()
        try:
            lines = for_league(key, season, names)
        except Exception as exc:                                    # noqa: BLE001
            # Never fatal. This is colour on a report; an offseason must not die for a
            # scoreline, and a season with no games yet simply has nothing to say.
            lines = []
            print(f"  no takeaways for {key}: {exc}")
        if lines:
            out.append(f'__**{key.title()}**__')
            out.extend(lines)
            out.append("")
    return out


def season_records(season, store):
    """Participation records from archived box scores, captured before players move leagues."""
    records = []
    names = {f'{c["first_name"]} {c["last_name"]}' for c in store.characters()}
    for key in ("prep", "college", "pro"):
        stats = _season_stats(key, season) or {}
        table = {r["name"]: r for r in stats.get("table", [])} if _season_number(stats.get("season")) == int(season) else {}
        players = {p["name"]: p for p in stats.get("players", [])} if table else {}
        games = _our_games(_load(key, "games.json"), season)
        for name in sorted(names & (set(games) | set(players))):
            rows = games.get(name, [])
            regular = [g for g in rows if not g.get("playoff") and isinstance(g.get("won"), bool)]
            playoffs = [g for g in rows if g.get("playoff") and isinstance(g.get("won"), bool)]
            team = players.get(name, {}).get("team")
            standing = table.get(team, {})
            records.append(dict(name=name, league=key, team=team,
                team_w=standing.get("w"), team_l=standing.get("l"),
                wins=sum(g["won"] for g in regular), losses=sum(not g["won"] for g in regular),
                playoff_wins=sum(g["won"] for g in playoffs),
                playoff_losses=sum(not g["won"] for g in playoffs), games=len(regular)))
    return records


def _season_number(value):
    match = re.fullmatch(r"(?:Season\s+)?(\d{4})", str(value or "").strip())
    return int(match[1]) if match else None


def _season_stats(league, season):
    path = ARCHIVE / f"overview-{league}-{int(season)}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if _season_number(data.get("season")) == int(season):
            return data
    data = _load(league, "stats.json")
    if data and data.get("season") is not None and _season_number(data["season"]) != int(season):
        return None
    return data


def archive_overview(league, season, html_dir):
    from .seasonbonus import league_stats
    data = league_stats(html_dir)
    if not data.get("table"):
        raise ValueError(f"{league}: finished-season standings missing")
    data.update(league=league, season=int(season))
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"overview-{league}-{int(season)}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    return data
