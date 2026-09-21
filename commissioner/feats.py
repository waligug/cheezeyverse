"""On-demand feats from already exported real-player box scores. Never part of a sim."""
from __future__ import annotations
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from . import notify, settings
from .codec.league_dat import find_season_day

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
LEDGER = ROOT / "universe" / "feats_sent.json"
THRESHOLDS = {"pts": 25, "reb": 15, "ast": 10, "stl": 5, "blk": 5, "tpm": 6}
LABELS = {"pts": "points", "reb": "rebounds", "ast": "assists", "stl": "steals", "blk": "blocks", "tpm": "threes"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sent_ids():
    return set(read(LEDGER)) if LEDGER.exists() else set()


def last_run_bounds(root, run):
    """Use actual pre-run checkpoints, not guessed day counts across playoff skips."""
    start = datetime.fromisoformat(run["started_at"]).astimezone().replace(tzinfo=None)
    end = datetime.fromisoformat(run["at"]).astimezone().replace(tzinfo=None)
    bounds = {}
    for league in run["leagues"]:
        matches = []
        for folder in (root / "backups").glob("*-CV_" + league.title()):
            try:
                stamp = datetime.strptime(folder.name[:15], "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            if start <= stamp <= end and (folder / "league.dat").exists():
                matches.append(folder)
        if not matches:
            raise ValueError(f"No pre-sim checkpoint for {league}; use the season scan instead.")
        day = find_season_day((min(matches) / "league.dat").read_bytes())
        if not day or day[1] != int(run["season"]):
            raise ValueError(f"Cannot verify the last sim's starting day for {league}.")
        bounds[league] = day[0]
    return bounds


def collect(site, season, bounds=None):
    events = []
    for league in ("prep", "college", "pro"):
        if bounds is not None and league not in bounds:
            continue
        path = Path(site) / "leagues" / league / "games.json"
        if not path.exists():
            continue
        for player in read(path).get("characters", []):
            best = {}
            games = sorted(player.get("games", []), key=lambda g: (int(g.get("season") or 0), int(g["day"])))
            for index, game in enumerate(games):
                tags = []
                for stat, threshold in THRESHOLDS.items():
                    value = int(game.get(stat) or 0)
                    if value >= threshold:
                        tags.append(f"{value} {LABELS[stat]}")
                    elif index and value > best.get(stat, 0) and value >= {"pts": 10, "reb": 5, "ast": 5, "stl": 3, "blk": 3, "tpm": 3}[stat]:
                        tags.append(f"career high: {value} {LABELS[stat]}")
                    best[stat] = max(best.get(stat, 0), value)
                doubles = sum(int(game.get(k) or 0) >= 10 for k in ("pts", "reb", "ast", "stl", "blk"))
                if doubles >= 2:
                    tags.append("triple-double" if doubles >= 3 else "double-double")
                if int(game.get("fga") or 0) >= 10 and int(game.get("fgm") or 0) == 0:
                    tags.append(f"0-for-{game['fga']} from the field")
                if not tags or int(game.get("season") or 0) != season:
                    continue
                if bounds is not None and int(game["day"]) < bounds[league]:
                    continue
                identity = f"{league}|{player['id']}|{season}|{game['day']}|{game.get('opp')}"
                events.append({"id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                    "name": player["name"], "league": league, "season": season, "day": game["day"],
                    "opponent": game.get("opp", "unknown"), "playoff": bool(game.get("playoff")),
                    "feats": tags, "line": f"{game.get('pts', 0)} PTS / {game.get('reb', 0)} REB / {game.get('ast', 0)} AST"})
    return sorted(events, key=lambda e: (e["day"], e["league"], e["name"]), reverse=True)


def scan(scope, root=ROOT):
    if scope not in ("last", "season"):
        raise ValueError("Choose last sim or current season.")
    if (root / "universe" / "run_in_progress.json").exists():
        raise ValueError("A sim is running or needs recovery; finish it before scanning feats.")
    runs = [r for r in read(root / "universe" / "sim_runs.json")
            if r.get("ok") and not r.get("dry_run") and r.get("season") and r.get("started_at")]
    if not runs:
        raise ValueError("No successful simulation has been recorded yet.")
    run = max(runs, key=lambda r: r["started_at"])
    bounds = last_run_bounds(root, run) if scope == "last" else None
    events = collect(root / "site", int(run["season"]), bounds)
    sent = sent_ids()
    for event in events:
        event["sent"] = event["id"] in sent
    return {"scope": scope, "season": run["season"], "through": run["at"], "events": events,
            "configured": bool(settings.get("DISCORD_FEATS_WEBHOOK_URL", ""))}


def send(events):
    """Serialize sends and checkpoint accepted batches, so repeat clicks skip delivered feats."""
    if not LOCK.acquire(blocking=False):
        raise ValueError("A statistical-feats post is already running.")
    try:
        settings.reload()
        if not settings.get("DISCORD_FEATS_WEBHOOK_URL", ""):
            raise ValueError("Set DISCORD_FEATS_WEBHOOK_URL in .env for #statistical-feats first.")
        sent = sent_ids()
        pending = [e for e in events if e["id"] not in sent]
        delivered = 0
        while pending:
            batch, lines = [], ["**Cheezeyverse statistical feats**"]
            while pending:
                e = pending[0]
                line = (f"**{e['name']}** — {e['league']} {e['season']}, day {e['day']}"
                        f"{' (playoffs)' if e['playoff'] else ''} vs {e['opponent']}: "
                        + "; ".join(e["feats"]) + f". {e['line']}")
                if batch and len("\n".join(lines + [line])) > 1800:
                    break
                pending.pop(0); batch.append(e); lines.append(line)
            ok = notify._send({"content": "\n".join(lines), "allowed_mentions": {"parse": []}},
                              setting="DISCORD_FEATS_WEBHOOK_URL")
            if not ok:
                raise ValueError(f"Discord delivery failed after {delivered} feats. Delivered batches are saved; retry skips them.")
            sent.update(e["id"] for e in batch)
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            temp = LEDGER.with_suffix(".tmp")
            temp.write_text(json.dumps(sorted(sent)), encoding="utf-8")
            temp.replace(LEDGER)
            delivered += len(batch)
        return delivered
    finally:
        LOCK.release()
