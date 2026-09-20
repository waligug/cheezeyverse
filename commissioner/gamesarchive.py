"""The per-game history, kept across seasons - because the MDB cannot keep it.

WHY THIS EXISTS. `games.json` was rebuilt from `LeagueOutput.mdb` on every publish, which was
fine for exactly as long as the universe had only ever played one season. Measured on two real
exports, independently, on 2026-09-19:

    PlayerGameStats   23 columns, NO season column. Its only time field is `Seasonday`, a day
                      number that restarts at 1 each year.
    Schedule          32 columns, NO season column either.
    A finished-season export held 9,856 stat rows over Seasonday 1-176 with not one
    (Day, Home, Away) repeated. After FBPB3's rollover the same table held ZERO rows, and
    Schedule described next season's 32 preseason and 240 regular fixtures.

So the export REPLACES; it does not accumulate, and there is no column to filter on. The first
publish after a rollover would have written a games.json with zero lines for all seven characters
- 161 nights, including a playoff run - with no error and no warning. Head to head would simply
have said nobody had ever played.

ONE FILE PER LEAGUE PER SEASON, and a finished season's file is NEVER REWRITTEN. That is the
whole safety property: a bug in here can damage the season currently being played, and cannot
reach a season that is over. A single accumulating file would put every year at risk of every
future publish. They live under `universe/history/` and are tracked, because `site/leagues/` is
gitignored AND `restyle(clean=True)` rmtrees it - the one place history certainly cannot survive.

THE SEASON IS STAMPED ONCE, at the top of each file, since every line in it was played that year.
`merge` copies it onto each line on the way out, because the published file holds several seasons
at once and day 5 of 2026 and day 5 of 2027 are different nights - the page matches meetings by
day, and without the season those two would look like the same game.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "universe" / "history"

# What a line or file with no season means. Everything archived before the stamp existed was
# played in the universe's first and only season. A fact with an expiry date, so it is written
# down rather than left for whoever reads this next to infer.
UNSTAMPED_SEASON = 2026


def path_for(key, season):
    return ARCHIVE / f"games-{key}-{int(season)}.json"


def archived_seasons(key):
    """[(season, payload)] for a league, oldest first. Empty when nothing is archived."""
    out = []
    pattern = re.compile(rf"^games-{re.escape(str(key))}-(\d+)\.json$")
    if not ARCHIVE.exists():
        return out
    for path in sorted(ARCHIVE.iterdir()):
        m = pattern.match(path.name)
        if not m:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue        # a damaged year must not take the others down with it
        out.append((int(payload.get("season", m.group(1))), payload))
    out.sort(key=lambda pair: pair[0])
    return out


def save(key, season, payload):
    """Write ONE season's file, atomically. Never touches another season."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["season"] = int(season)
    target = path_for(key, season)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)
    return target


def _stamp(payload, season):
    """Every game line in `payload`, carrying the season it was played in."""
    season = int(season)
    for entry in payload.get("characters") or []:
        for line in entry.get("games") or []:
            yield entry, dict(line, season=season)


def merge(history, fresh, season):
    """Archived seasons + this season's export -> the games.json the site publishes.

    `history` is [(season, payload)] as `archived_seasons` returns it. `fresh` is what the MDB
    gave for `season`, and it wins for its own year: it was read from the file the game just
    wrote. Earlier years are carried through untouched, because nothing can re-derive them.

    A character in the history but ABSENT from the export keeps his games. That is not a corner
    case - it is what happens to everybody the instant the rollover empties PlayerGameStats.
    """
    season = int(season) if season is not None else None
    people, order = {}, []

    def slot(entry):
        ident = entry.get("id") or entry.get("name")
        if ident not in people:
            people[ident] = {**{k: v for k, v in entry.items() if k != "games"}, "games": {}}
            order.append(ident)
        return people[ident]

    for archived_season, payload in history or []:
        for entry, gline in _stamp(payload, archived_season or UNSTAMPED_SEASON):
            row = slot(entry)
            row["games"][(gline["season"], int(gline.get("day", 0)))] = gline

    # AN EMPTY EXPORT REPLACES NOTHING. Dropping the archived copy of this season is right when
    # the export actually holds the season - it is how a re-sim after a fix corrects a night
    # rather than leaving both versions for every average to count twice. It is catastrophic
    # when the export is empty, which is the normal state straight after FBPB3's rollover: the
    # archive's own lines for the current season would be deleted by the very merge that exists
    # to preserve them. Caught by the test the day a real 2027 file existed to lose.
    fresh_has_games = any(entry.get("games") for entry in (fresh or {}).get("characters") or [])

    if fresh and season is not None:
        for entry in fresh.get("characters") or []:
            row = slot(entry)
            # His details - team, since_day, since_source - are fresher in the export than in any
            # archive, so they win. His GAMES merge by key rather than replacing wholesale.
            games = row["games"]
            row.update({k: v for k, v in entry.items() if k != "games"})
            row["games"] = games
            if fresh_has_games:
                for stale in [k for k in games if k[0] == season]:
                    del games[stale]
        for entry, gline in _stamp(fresh, season):
            slot(entry)["games"][(season, int(gline.get("day", 0)))] = gline

    characters = []
    for ident in order:
        row = people[ident]
        lines = [row["games"][k] for k in sorted(row["games"])]
        characters.append({**{k: v for k, v in row.items() if k != "games"}, "games": lines})
    characters.sort(key=lambda c: str(c.get("name") or ""))

    base = fresh or (history[-1][1] if history else {})
    payload = {k: v for k, v in base.items() if k not in ("characters", "season")}
    payload["characters"] = characters
    payload["seasons"] = sorted({s for s, _ in (history or [])}
                                | ({season} if season is not None else set()))
    return payload
