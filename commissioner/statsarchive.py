"""Every season's stat line, kept for good - because the export only keeps the current one.

WHY THIS EXISTS, and it is the same reason `gamesarchive` exists. FBPB3's `LeagueOutput.mdb` is
a snapshot of the save as it stands: `SeasonStats` holds one row per player per season and it is
rebuilt from the league file every time. The league file is what retires players, and a retired
player stops being exported - so a career that ended is a career that vanishes from the next
export. Measured on 2026-09-20: four pro players aged 34-35 retired at the 2026 rollover and are
already gone from the save entirely.

So the moment a season's numbers exist, they get written down here and never rewritten. That
makes this the ONLY durable record of what anybody did, which is the whole point of an all-time
leaderboard: a career record that only covers players who happen to still be active is not a
career record.

IDENTITY IS NAME AND BIRTHDAY, NOT THE ID. `SeasonStats.ID` is stable across seasons today - 247
of pro's IDs appear in both 2026 and 2027 - and `HistoricalID` is empty in every row, so the id
is all the file gives us. But ids are assigned by the game, retirement frees them, and nothing
promises a draftee will not be handed a dead man's number. If that ever happens, summing by id
would silently weld two people into one career, and the result would look entirely plausible.
So each row carries the name and birthday the player had that season, and `careers()` refuses to
merge two seasons that disagree about who the id belonged to.

    python -m commissioner.statsarchive --capture      # write down what the MDBs currently hold
    python -m commissioner.statsarchive --leaders pro  # who the all-time leaders are
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "universe" / "history"

# The counting stats worth keeping. Rates (PPG, FG%) are deliberately NOT stored: they are
# derivable, and a stored average is one more thing that can disagree with the totals it came
# from. Everything here adds up across seasons, which is what a career total is.
COUNTING = ["Games", "GamesStarted", "Minutes", "Points", "Rebounds", "OffensiveRebounds",
            "Assists", "Steals", "Blocks", "Turnovers", "Fouls", "FGM", "FGA", "3PM", "3PA",
            "FTM", "FTA", "PlusMinus", "DoubleDoubles", "TripleDoubles", "DQ"]
# Who he was that season. Kept per season on purpose: he ages, he changes team, and a career
# page wants to show that rather than only his last known state.
IDENTITY = ["name", "dob", "age", "team", "position"]

# THE POSTSEASON IS ITS OWN TABLE, and `SeasonStats` is the REGULAR SEASON ONLY. Checked against
# the live prep MDB: for 2028, id 41 reads 30 games in SeasonStats and 2 in PlayoffStats, and the
# two never overlap. So a playoff career is a SEPARATE archive rather than a filter over this
# one, and the season totals a career page already showed do not change by adding it.
#
# PlayoffStats carries every COUNTING field except GamesStarted, which it simply does not have;
# `_int` returns 0 for a missing column, so a playoff line reports 0 starts rather than lying.
KINDS = {"stats": "SeasonStats", "playoffs": "PlayoffStats"}


def path_for(key, season, kind="stats"):
    return ARCHIVE / f"{kind}-{key}-{int(season)}.json"


def _int(value):
    try:
        return int(float(str(value).strip() or 0))
    except (TypeError, ValueError):
        return 0


def read_people(mdb):
    """{id: identity} from the Player table.

    Split out so a caller reading more than one stats table pays for this join ONCE. Every
    `query()` spawns a PowerShell + ADODB process, and `_write_careers` runs per league on every
    publish inside every Sim Week, so reading it per kind doubled the subprocess count for a
    table that is identical both times.
    """
    from .headtohead import query
    people = {}
    for row in query(mdb, "SELECT ID, Name, BirthMonth, BirthDay, BirthYear, Age, "
                          "CurrentTeam, PositionNumber FROM Player"):
        people[str(row.get("ID"))] = {
            "name": (row.get("Name") or "").strip(),
            "dob": f'{_int(row.get("BirthMonth"))}/{_int(row.get("BirthDay"))}/'
                   f'{_int(row.get("BirthYear"))}',
            "birth_year": _int(row.get("BirthYear")),
            "position": _int(row.get("PositionNumber")),
        }
    return people


def read_mdb(mdb, kind="stats", people=None):
    """{season: [row]} straight out of one LeagueOutput.mdb, with names attached.

    `kind` picks the table: "stats" is the regular season, "playoffs" the postseason. See KINDS.
    `people` is a prebuilt identity map from `read_people`, so a caller reading both tables does
    not run the same Player join twice.

    The join is done in Python rather than in Access SQL for the reason headtohead already
    gives about this database: its own SQL dialect refuses things that look ordinary, and a
    query that fails here fails in the middle of a publish.
    """
    from .headtohead import query
    people = read_people(mdb) if people is None else people
    seasons = {}
    for row in query(mdb, f"SELECT * FROM {KINDS[kind]}"):
        season = _int(row.get("Season"))
        if not season:
            continue
        who = people.get(str(row.get("ID")), {})
        entry = {"id": str(row.get("ID")), "team": (row.get("Team") or "").strip(),
                 "name": who.get("name", ""), "dob": who.get("dob", ""),
                 # Player.Age is the player's age on the CURRENT export date. Using it on every
                 # SeasonStats row made a 2026 line get older every time it was recaptured in
                 # 2027 or 2028. Seasons in this universe use season - birth year everywhere
                 # else (growth, age-out, career headers), so derive the historical age here.
                 "age": max(0, season - who.get("birth_year", season)),
                 "position": who.get("position", 0)}
        for field in COUNTING:
            entry[field] = _int(row.get(field))
        seasons.setdefault(season, []).append(entry)
    return seasons


def save(key, season, rows, source="", overwrite=False, kind="stats"):
    """Write ONE league's ONE season, atomically. A season already written is left alone.

    A finished season cannot change, so rewriting one can only ever damage it - the same rule
    `gamesarchive` runs under, and for the same reason: a bug in here must not be able to reach
    a year that is already history.
    """
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    target = path_for(key, season, kind)
    if target.exists() and not overwrite:
        return None
    body = {"league": key, "season": int(season), "source": source, "kind": kind,
            "fields": COUNTING, "players": rows}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)
    return target


def archived_seasons(key, kind="stats"):
    """[(season, payload)] for a league, oldest first. `kind` is "stats" or "playoffs"."""
    out = []
    pattern = re.compile(rf"^{re.escape(kind)}-{re.escape(str(key))}-(\d+)\.json$")
    if not ARCHIVE.exists():
        return out
    for path in sorted(ARCHIVE.iterdir()):
        m = pattern.match(path.name)
        if not m:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue            # one damaged year must not take the rest of history with it
        out.append((int(payload.get("season", m.group(1))), payload))
    out.sort(key=lambda pair: pair[0])
    return out


def _identity(row):
    """What makes this row a person. Name and birthday, the same pair the codec finds people by."""
    return (row.get("name", "").strip().lower(), row.get("dob", "").strip())


def careers(key, history=None, kind="stats"):
    """One row per player, totals summed across every archived season.

    `kind` selects the archive: "stats" for the regular season, "playoffs" for the postseason.
    A playoff career is built the same way from the same fields, so every rate and the
    efficiency figure mean exactly what they mean on the regular-season board.

    Rows whose id matches but whose identity does NOT are kept apart, because that is a recycled
    id and not a career. Each career carries the seasons it is made of, so a page can show the
    year-by-year line as well as the total.
    """
    people = {}
    for season, payload in (history if history is not None else archived_seasons(key, kind)):
        for row in payload.get("players") or []:
            who = _identity(row)
            if not who[0]:
                continue                   # a row with no name cannot be attributed to anybody
            slot = people.setdefault(who, {
                "name": row.get("name", ""), "dob": row.get("dob", ""),
                "ids": [], "seasons": [], "first_season": season, "last_season": season,
                **{f: 0 for f in COUNTING},
            })
            if row.get("id") and row["id"] not in slot["ids"]:
                slot["ids"].append(row["id"])
            for field in COUNTING:
                slot[field] += int(row.get(field) or 0)
            slot["seasons"].append({"season": season, "team": row.get("team", ""),
                                    "age": row.get("age", 0),
                                    **{f: int(row.get(f) or 0) for f in COUNTING}})
            slot["first_season"] = min(slot["first_season"], season)
            slot["last_season"] = max(slot["last_season"], season)
            slot["age"] = row.get("age", slot.get("age", 0))
            slot["team"] = row.get("team", "")
    return [_with_rates(row) for row in people.values()]


def _with_rates(row):
    """Per-game averages and shooting percentages, worked out from the totals."""
    games = row.get("Games") or 0
    def per(field):
        return round(row.get(field, 0) / games, 1) if games else 0.0
    row["ppg"], row["rpg"], row["apg"] = per("Points"), per("Rebounds"), per("Assists")
    row["spg"], row["bpg"], row["mpg"] = per("Steals"), per("Blocks"), per("Minutes")
    fga, fta, tpa = row.get("FGA", 0), row.get("FTA", 0), row.get("3PA", 0)
    row["fg_pct"] = round(100 * row.get("FGM", 0) / fga, 1) if fga else 0.0
    row["ft_pct"] = round(100 * row.get("FTM", 0) / fta, 1) if fta else 0.0
    row["tp_pct"] = round(100 * row.get("3PM", 0) / tpa, 1) if tpa else 0.0
    # Efficiency, the NBA's own formula: what he produced minus what he wasted.
    row["efficiency"] = (row.get("Points", 0) + row.get("Rebounds", 0) + row.get("Assists", 0)
                         + row.get("Steals", 0) + row.get("Blocks", 0)
                         - (fga - row.get("FGM", 0)) - (fta - row.get("FTM", 0))
                         - row.get("Turnovers", 0))
    row["epg"] = round(row["efficiency"] / games, 1) if games else 0.0
    return row


def leaders(rows, stat="Points", count=10, min_games=0):
    """The top `count` by one stat. Ties broken by games, so a long career wins a dead heat."""
    live = [r for r in rows if (r.get("Games") or 0) >= min_games]
    live.sort(key=lambda r: (-(r.get(stat) or 0), -(r.get("Games") or 0), r.get("name", "")))
    return live[:count]


def capture(key, mdb, log=print, overwrite_current=None, force=False, kinds=("stats", "playoffs")):
    """Write down every season this MDB holds, regular season and postseason. Returns the
    seasons newly written, in either archive.

    `overwrite_current` is the season still being played: its file is rewritten each time so it
    keeps up, while every finished season is written once and then left alone forever. That
    matters more for the postseason than the regular season, because a playoff archive for the
    current year goes from absent, to a first round, to a full bracket, and only the last of
    those is the truth.
    """
    if not Path(mdb).exists():
        log(f"  no MDB for {key}; nothing captured")
        return []
    written = []
    people = None
    for kind in kinds:
        try:
            if people is None:
                people = read_people(mdb)
            seasons = sorted(read_mdb(mdb, kind, people).items())
        except Exception as exc:                                        # noqa: BLE001
            # THE POSTSEASON MUST NOT BE ABLE TO COST US THE REGULAR SEASON. An MDB without a
            # PlayoffStats table - an older export, or a schema that moves - is skipped rather
            # than taking down a capture that runs inside every publish.
            #
            # THE REGULAR SEASON IS NOT SWALLOWED, and the asymmetry is the whole point. publish
            # calls this with `log=lambda m: None`, so a swallowed failure is silent everywhere:
            # capture would return [], careers() would read the archive it already had, and
            # careers.json would be rewritten with a fresh `generated` and stale contents. A
            # broken Access driver or a moved schema would look like a clean publish forever.
            # Raising hands it back to _write_careers' own handler, which prints the reason and
            # leaves the published file alone - which is what happened before playoffs existed.
            if kind == "stats":
                raise
            log(f"  {key}: cannot read {KINDS.get(kind, kind)} ({exc}); skipped")
            continue
        # A SEASON OLDER THAN ONE WE ALREADY HOLD CANNOT BE RECOVERED FROM TODAY'S MDB.
        #
        # FBPB3 keeps a player's season history on his ROW, and a row changes hands. A character
        # is stamped onto a reserve row when he is promoted - `characters.stamp_character` renames
        # it and rewrites its birthday - and the row's past seasons follow the new name. After the
        # 2029 rollover the live college MDB credited Chris Zimmer with 32 games in 2028, a season
        # he spent in prep, while the prep slot he vacated carried HIS 2029 scoring under the
        # filler's name. Both directions, every promotion, for as long as the universe runs.
        #
        # The archives escaped it because each season was written while the rows still had the
        # right names, and `save()` refuses to rewrite a finished season. This keeps that true in
        # the one case that guard does not cover: a season the MDB still holds that we have NO
        # archive for - a restored backup, a machine rebuilt, an archive that failed to write. On
        # that path `save()` sees no file, writes happily, and bakes in the wrong attribution
        # forever. The right answer there is that the season is simply not recoverable, and
        # keeping the gap is better than filling it with a name that was not there at the time.
        #
        # `force` still overrides, because repairing a season written wrong is a real need, and
        # the current season is always rewritten - the rows are correct while it is being played.
        held = {int(s) for s, _ in archived_seasons(key, kind)}
        newest = max(held) if held else None
        for season, rows in seasons:
            current = overwrite_current is not None and int(season) == int(overwrite_current)
            if (newest is not None and int(season) < newest and int(season) not in held
                    and not current and not force):
                log(f"  {key} {season} {kind}: no archive, and older than {newest} - refusing to "
                    "rebuild it from a save whose rows have changed hands since")
                continue
            # `force` is for repairing a season written wrong - the first capture archived 390
            # player seasons with every name blank, because the query was silently returning
            # nothing. Rewriting history is otherwise refused, so this is a flag and not a default.
            target = save(key, season, rows, source=str(mdb), overwrite=current or force, kind=kind)
            if target:
                if season not in written:
                    written.append(season)
                log(f"  {key} {season} {kind}: {len(rows)} player seasons -> {target.name}")
            else:
                log(f"  {key} {season} {kind}: already archived, left alone")
    return sorted(written)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    from .universe import config as cfg
    from .driver.fbpb3 import DOCS
    force = "--force" in argv
    if "--capture" in argv:
        try:
            from . import store
            current = int(store.get_settings().get("current_season", 0)) or None
        except Exception:
            current = None
        for spec in cfg.LEAGUES:
            capture(spec.key, DOCS / "leaguedata" / spec.save_name / "LeagueOutput.mdb",
                    overwrite_current=current, force=force)
        return 0
    if "--leaders" in argv:
        key = argv[argv.index("--leaders") + 1]
        rows = careers(key)
        print(f"{key}: {len(rows)} careers archived")
        for stat in ("Points", "Rebounds", "Assists", "Blocks", "Steals", "efficiency"):
            print(f"\n  all-time {stat}")
            for i, r in enumerate(leaders(rows, stat, 5), 1):
                print(f"    {i}. {r['name']:<24} {r[stat]:>6}  ({r['Games']} games, "
                      f"{r['first_season']}-{r['last_season']})")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
