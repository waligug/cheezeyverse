"""Take what FBPB3 generated and put it where GitHub Pages can serve it.

FBPB3 writes its HTML into `leaguedata/<save>/html`. That folder is re-skinned (see restyle.py)
into `site/leagues/<key>/`, so one Pages deploy serves the character site and all three league
sites together and `site/config.js` can point at plain relative paths.

    python -m commissioner.publish.publish            # all three leagues
    python -m commissioner.publish.publish prep
"""
from __future__ import annotations

import json
import re
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from ..universe import config as cfg
from .restyle import restyle

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
SITE = ROOT / "site"


def season_label(key):
    """The season the universe is actually in, for the page header.

    This used to scrape the first four digits out of `saveinfo.dat`, which holds a date, not a
    season - so every page in every league said "Season 2030" for ever, however many years had
    been played. The store knows the season; ask it.
    """
    try:
        from ..simweek import store
        season = store().get_settings().get("current_season")
        if season:
            return f"Season {int(season)}"
    except Exception:
        pass
    return f"Season {cfg.START_YEAR}"


def _our_players(league_key, characters=None):
    """{fbpb3 player id: owner-facing name} for the real people's characters in this league.

    Keyed by the game's own player id rather than by name, because two players can share a
    name - the stock rosters do - and badging by name would decorate the wrong man. Returns
    empty on any failure: a missing badge is cosmetic, a publish that will not run is not.
    """
    try:
        from ..simweek import store
        out = {}
        rows = characters
        if rows is None:
            rows = store().characters(league=league_key)
        for c in rows:
            if c.get("league") != league_key:
                continue
            if c.get("status") == "retired":
                continue
            pid = (c.get("league_player_ids") or {}).get(league_key)
            if pid is not None:
                # The live sheet travels with the name, because FBPB3's player pages print a
                # season-start snapshot rather than current ratings - and for a claimed reserve
                # slot that snapshot is the RESERVE's ratings, so every character's page showed
                # his predecessor's numbers until the season rolled over.
                out[int(pid)] = {
                    "name": f'{c["first_name"]} {c["last_name"]}',
                    "ratings": c.get("ratings") or {},
                    "potentials": c.get("potentials") or {},
                }
        return out
    except Exception as exc:
        print(f"   (could not mark our players in {league_key}: {exc})")
        return {}


def _rookie_ids(key, season=None):
    """Player ids in their FIRST season in this league. Never fatal.

    FROM THE ARCHIVE, NOT FROM `Exp`. The game keeps an experience counter and it was the obvious
    source, but it does not survive contact with this universe: Davis Plowden reads Exp 1 having
    played 2031 AND 2032, while Josiah Meehan reads 10 for seven archived seasons - the counter
    drifts when a player sits out a rollover, and the stock rosters start with experience already
    on them. Badging on it would have called a third of the pro league rookies.

    The archive cannot drift, because a finished season is written once and never rewritten. A
    rookie is simply somebody with no archived season in this league BEFORE the one being played.
    Identity is name plus birthday, the same as everywhere else in statsarchive - two players can
    share a name.
    """
    try:
        from ..codec.league_dat import LeagueDat
        from .. import characters as ch, statsarchive
        path = ch.save_path(key)
        if not path.exists():
            return set()
        if season is None:
            try:
                from ..simweek import store
                season = int(store().get_settings().get("current_season", 0)) or None
            except Exception:                                       # noqa: BLE001
                season = None
        seen = set()
        for year, payload in statsarchive.archived_seasons(key):
            if season is not None and int(year) >= int(season):
                continue                     # this season is not evidence of a previous one
            for row in payload.get("players") or []:
                seen.add((row.get("name"), row.get("dob")))
        if not seen:
            return set()                     # no history yet: nobody is a rookie rather than everybody
        return {p.id for p in LeagueDat(path).players
                if p.values.get("Team", 0) >= 1 and (p.name, p.dob) not in seen}
    except Exception as exc:                                        # noqa: BLE001
        print(f"  no rookie badges for {key} ({exc}); the pages themselves are fine")
        return set()


def publish_league(key, mdb_fresh=True, season=None, ours=None):
    spec = cfg.BY_KEY[key]
    src = DOCS / "leaguedata" / spec.save_name / "html"
    dst = SITE / "leagues" / key
    if not (src / "index.htm").exists():
        raise FileNotFoundError(f"{spec.save_name} has no HTML output yet - run the driver's html_output() first")
    # restyle rebuilds the folder. When this Sim Week deliberately deferred an MDB, preserve
    # the last valid game/career payload instead of regenerating it from a stale database or
    # deleting it from the public site.
    retained = {}
    if not mdb_fresh:
        for name in ("games.json", "careers.json", "goat.json"):
            path = dst / name
            if path.exists():
                retained[name] = path.read_bytes()
    try:
        current = int(str(season or season_label(key)).split()[-1])
    except (ValueError, IndexError):
        current = 0
    # BEFORE restyle, and against the GAME'S OWN EXPORT, not the site copy.
    #
    # A character is put into this league by stamping his name onto an existing player's record,
    # so the export writes his name above that man's whole career. Cleaning only `dst` left the
    # bleed everywhere else, because `dst` is not what the rest of publish reads: `_write_stats`
    # and `_write_careers` both parse `src`. Measured on 2031 pro, Dodger Manson's site page was
    # clean while stats.json credited him with 20 games and 11 points - Vicente Cowden's 2030
    # line, under Dodger's name, in a season where Dodger had never played a pro minute. Thirty
    # foreign rows sat in the export page behind it.
    #
    # `src` is the driver's regenerable html_output(), rewritten wholesale every sim, and the
    # save is the record of truth - so editing it here costs nothing and is idempotent: a second
    # pass finds no rows at or before `first` left to drop. Doing it first also means restyle
    # copies pages that are ALREADY clean, so the `dst` pass below becomes a no-op that only
    # earns its keep when an export arrives by some other route.
    repaired_src = fix_player_pages(key, src, current)
    pages = restyle(src, dst, league=spec.name, season=season or season_label(key), key=key,
                    ours=_our_players(key) if ours is None else ours,
                    cache_path=ROOT / "tmp" / "restyle-cache" / f"{key}.json",
                    rookies=_rookie_ids(key))
    # AFTER restyle, both of them. restyle(clean=True) does an rmtree of the league's folder and
    # rebuilds it from the game's export, so anything written there beforehand is deleted. That
    # is exactly what happened to games.json: run_sim wrote it before publish(), a publish then
    # removed it from the site, and the comment on that call claimed the opposite.
    stats = _write_stats(src, dst, key)
    cap = _write_cap(src, dst, key)
    # AFTER restyle, which rebuilds the folder from the game's export and would undo this.
    repaired = fix_player_pages(key, dst, current)
    if mdb_fresh:
        games = _write_games(src, dst, key)
        careers = _write_careers(src, dst, key)
        # AFTER careers, which is what archives this season's lines; the GOAT components are
        # built from that archive.
        from . import goat
        goat_out = goat.write(dst, key)
        if isinstance(careers, dict):
            careers["goat"] = goat_out
    else:
        for name, payload in retained.items():
            (dst / name).write_bytes(payload)
        games = careers = {"deferred": True, "retained": sorted(retained)}
    return {"league": key, "name": spec.name, "pages": pages, "path": str(dst),
            "stats": stats, "games": games, "careers": careers, "cap": cap,
            "repaired": repaired, "repaired_export": repaired_src}


_YEAR_ROW = re.compile(r"^(20\d\d)$")


def _row_cells(row):
    out = []
    for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I):
        text = re.sub(r"<[^>]+>", "", cell)
        text = re.sub(r"(?:&nbsp;|&#160;|&#xA0;)", " ", text)
        out.append(text.strip())
    return out


def strip_foreign_history(html, first_season):
    """Drop the seasons on a player page that belong to whoever held the row before him.

    FBPB3 keeps a player's history on his ROW, and a character is stamped onto a reserve row that
    already existed - `characters.stamp_character` renames it and rewrites its birthday, and the
    row's past follows the new name. Johnny Gartholomew's college page, one day into the league,
    listed 19 games in 2028, 5 in 2029, and TWO Cheezeyverse College Championships he did not win.

    The save cannot be corrected from here: that history lives in league.dat structures the codec
    does not model. But the published page is ours, and a page that credits somebody with another
    man's championships is worse than a page with a gap in it.

    Any row whose first cell is a year before `first_season` goes - stat lines and award lines
    alike, since both are year-led. AND SO DOES THE "Career" ROW OF ANY TABLE WE TOUCHED: it is a
    total of the rows that were there, so once some are gone it is simply wrong, and a missing
    total is honest where a wrong one is not.
    """
    rows = list(re.finditer(r"<tr[^>]*>.*?</tr>", html, re.S | re.I))
    if not rows:
        return html, 0
    drop = set()
    kept_years = dropped_years = 0
    kept_total = dropped_total = 0
    for m in rows:
        cells = _row_cells(m.group(0))
        first = cells[0] if cells else ""
        year = _YEAR_ROW.match(first)
        if year:
            if int(year.group(1)) < first_season:
                drop.add(m.start())
                dropped_years += 1
                dropped_total += 1
            else:
                kept_years += 1
                kept_total += 1
            continue
        if first.lower().startswith("career"):
            # Only a table we edited loses its total; one left alone keeps it.
            if dropped_years:
                drop.add(m.start())
            kept_years = dropped_years = 0
            continue
        kept_years = dropped_years = 0
    # A SUMMARY OF SEASONS THAT ARE NOT HIS IS NOT HIS EITHER. "Career Highs" and
    # "Total - Championships: 2" are not year-led, so the pass above leaves them: Gartholomew's
    # page still claimed two championships after every 2028 and 2029 line had gone. When NOTHING
    # of his own is left on the page, the whole career block belongs to the man who held the row
    # before him, so it all goes; the championship total goes whenever any season was dropped,
    # because it counted those seasons.
    nothing_is_his = kept_total == 0 and dropped_total > 0
    if dropped_total:
        for m in rows:
            if m.start() in drop:
                continue
            cells = _row_cells(m.group(0))
            first = (cells[0] if cells else "").lower()
            joined = " ".join(cells).lower()
            if "championship" in joined:
                drop.add(m.start())
            elif nothing_is_his and (first.startswith("career") or first.startswith("total")):
                drop.add(m.start())

    if not drop:
        return html, 0
    out, at = [], 0
    for m in rows:
        if m.start() in drop:
            out.append(html[at:m.start()])
            at = m.end()
    out.append(html[at:])
    return "".join(out), len(drop)


def _first_season_in(key, name, dob, fallback):
    """The earliest season this man really played in this league, from the archives.

    The archives are the trustworthy record: every season was written while the rows still had
    the right names, and statsarchive.save refuses to rewrite a finished one. `fallback` is the
    season now, for somebody who has arrived but not yet played one.
    """
    from .. import statsarchive
    seasons = []
    for season, payload in statsarchive.archived_seasons(key):
        for row in payload.get("players") or []:
            if row.get("name") == name and (not dob or row.get("dob") == dob):
                seasons.append(int(season))
                break
    return min(seasons) if seasons else fallback


def fix_player_pages(key, dst, season, characters=None):
    """Rewrite our own characters' pages so they carry only their own career. Never fatal.

    Reads the characters itself rather than taking `_our_players`, which is a {player id: name}
    map for badging and carries none of the fields this needs.
    """
    if characters is None:
        try:
            from ..simweek import store
            characters = store().characters(league=key)
        except Exception:                                               # noqa: BLE001
            return []
    fixed = []
    for c in characters or []:
        if not isinstance(c, dict) or c.get("league") != key:
            continue
        page_id = (c.get("league_player_ids") or {}).get(key)
        if page_id is None:
            continue
        page = Path(dst) / "players" / f"player{page_id}.htm"
        if not page.exists():
            continue
        name = f'{c.get("first_name")} {c.get("last_name")}'
        try:
            first = _first_season_in(key, name, c.get("game_dob"), int(season))
            html = page.read_text(encoding="latin-1")
            # Only his own page, and only if it is really his - a stale id must not edit a
            # stranger's page.
            if name not in html:
                continue
            new, dropped = strip_foreign_history(html, first)
            if dropped:
                page.write_text(new, encoding="latin-1")
                fixed.append({"name": name, "page": f"player{page_id}", "from": first,
                              "rows_dropped": dropped})
        except Exception:                                               # noqa: BLE001
            continue   # a page we could not clean is published as the game wrote it
    return fixed

def _write_games(src, dst, key):
    """Emit `games.json`: each character's own game lines, for the head-to-head page.

    Read from the LeagueOutput.mdb the sim writes while the save is loaded. Done here rather
    than before publish() so that EVERY publish carries it - a sim, a manual re-skin, a
    republish after a fix - instead of only the one code path that remembered to write it first.

    Never fatal, like stats.json. A league with no MDB yet simply has no file, and the page
    treats the 404 as "not published yet" rather than as an error.
    """
    try:
        from .. import headtohead
        from ..simweek import store
        # src is <save>/html, so the MDB sits beside it. Derived from the SAME path the pages
        # came from rather than re-deriving it from the league key: two routes to one file is
        # two chances to publish a league's data from another league's export.
        mdb = Path(src).parent / "LeagueOutput.mdb"
        if not mdb.exists():
            return None
        st = store()
        # 'declared' as well as 'active'. declare_for_draft sets it mid-season and he keeps
        # playing, keeps being paid and keeps meeting people - so filtering on 'active' alone
        # made a character disappear from head-to-head the moment he declared, taking every
        # comparison involving him with it, until the offseason moved him.
        people = [c for c in st.characters(league=key)
                  if c.get("status") in ("active", "declared")]
        if not people:
            # A LEAGUE CAN EMPTY OUT, and prep just did: a whole cohort was promoted at once, so
            # nobody is left there. Returning here wrote no games.json, and the deploy guard then
            # read the absence as published work being removed and refused the push - correctly,
            # because that file is the entire prep game history of the seven who moved up, and
            # nothing else on the live site holds it.
            #
            # There is no export to read for a league with no characters in it, but the archive
            # is the whole answer anyway: merge() is built to carry a man who is absent from the
            # export, which is what everybody here now is, permanently.
            from .. import gamesarchive
            history = gamesarchive.archived_seasons(key)
            if not history:
                return None
            merged = gamesarchive.merge(history, {"characters": []}, None)
            merged["generated"] = datetime.now().isoformat(timespec="seconds")
            (dst / "games.json").write_text(json.dumps(merged, separators=(",", ":")),
                                            encoding="utf-8")
            played = sum(len(c["games"]) for c in merged["characters"])
            print(f"  {key}: nobody plays here now; games.json rebuilt from the archive alone "
                  f"({len(merged['characters'])} character(s), {played} game line(s))")
            return {"characters": len(merged["characters"]), "games": played, "from_mdb": 0}
        settings = st.get_settings()
        data = headtohead.from_mdb(
            mdb, people,
            runs=(st.runs(limit=None) if hasattr(st, "runs") else []),
            league=key,
            season=int(settings.get("current_season", 0)) or None)
        # MERGED WITH THE ARCHIVE, never published straight from the MDB. The export holds one
        # season and only one: it has no season column to filter on, and FBPB3's rollover
        # replaces it with next season's empty schedule. Publishing the MDB's own answer the day
        # after a rollover would write a games.json with zero lines for everybody and leave every
        # game these seven have ever played existing only in the published branch's history.
        from .. import gamesarchive
        season = int(settings.get("current_season", 0)) or None
        history = gamesarchive.archived_seasons(key)
        # This season's file is rewritten; every finished season's is left exactly alone. Only
        # when the export actually HOLDS games, though: after a rollover PlayerGameStats is empty
        # while the characters are all still listed, so `data` has seven entries and no lines -
        # and saving that would overwrite a good season file with an empty one.
        if season is not None and any(c.get("games") for c in data.get("characters") or []):
            gamesarchive.save(key, season, data)
            history = gamesarchive.archived_seasons(key)
        merged = gamesarchive.merge(history, data, season)
        merged["generated"] = datetime.now().isoformat(timespec="seconds")
        (dst / "games.json").write_text(json.dumps(merged, separators=(",", ":")), encoding="utf-8")
        played = sum(len(c["games"]) for c in merged["characters"])
        fresh = sum(len(c["games"]) for c in data["characters"])
        if not played:
            print(f"  {key}: no game lines at all, in the MDB or the archive - "
                  "head-to-head will be empty")
        elif not fresh:
            # Normal straight after a rollover, and alarming at any other time.
            print(f"  {key}: the MDB gave no game lines; publishing {played} from the archive")
        return {"characters": len(merged["characters"]), "games": played, "from_mdb": fresh}
    except Exception as exc:
        print(f"  no games.json for {key} ({exc}); the pages themselves are fine")
        return None


def _write_careers(src, dst, key):
    """Archive this season's stat lines, then emit `careers.json`: every career, all-time.

    THE ARCHIVING IS THE POINT, and it happens here rather than in the sim because a publish is
    the one step that always runs and always has the MDB beside it. `SeasonStats` is rebuilt
    from the save every export, and the save retires people - four pro players aged 34-35 went
    at the 2026 rollover and are already gone from the file. Whatever is not written down before
    a man retires is gone for good, so it gets written down every publish.

    A finished season is archived once and never rewritten; only the season being played is
    refreshed. See statsarchive for why identity is the name and birthday rather than the id.

    Never fatal, like the other two. A missing MDB is a league that has not exported yet.
    """
    try:
        from .. import statsarchive
        from ..simweek import store
        mdb = Path(src).parent / "LeagueOutput.mdb"
        if not mdb.exists():
            return None
        try:
            season = int(store().get_settings().get("current_season", 0)) or None
        except Exception:                                        # noqa: BLE001
            season = None
        statsarchive.capture(key, mdb, log=lambda m: None, overwrite_current=season)
        rows = statsarchive.careers(key)
        if not rows:
            return None
        seasons = sorted({s for s, _ in statsarchive.archived_seasons(key)})
        # The leaderboards the site shows, worked out here so the browser does not have to sort
        # a few hundred careers six ways on every page load.
        board_stats = ("Points", "Rebounds", "Assists", "Steals", "Blocks",
                       "efficiency", "Minutes", "3PM")
        boards = {stat: [_career_line(r, stat) for r in statsarchive.leaders(rows, stat, 10)]
                  for stat in board_stats}
        # THE POSTSEASON IS A SEPARATE ARCHIVE, not a slice of the one above: the game keeps it
        # in its own table and `SeasonStats` is the regular season alone. Published as its own
        # block so a page can offer it as a choice, and so a league that has never reached a
        # playoff simply has no block rather than an empty board.
        playoff_rows = statsarchive.careers(key, kind="playoffs")
        playoffs = None
        if playoff_rows:
            playoffs = {
                "seasons": sorted({s for s, _ in statsarchive.archived_seasons(key, "playoffs")}),
                "leaders": {stat: [_career_line(r, stat)
                                   for r in statsarchive.leaders(playoff_rows, stat, 10)]
                            for stat in board_stats},
                "careers": [{k: v for k, v in r.items() if k != "seasons"}
                            for r in sorted(playoff_rows, key=lambda r: -(r.get("Points") or 0))],
            }
        # A season that happened but cannot be compared with any other travels WITH the data, so
        # the page never has to know which ones they are. Named, with the reason, never filtered.
        anomalies = {str(s): statsarchive.anomaly(key, s) for s in seasons
                     if statsarchive.anomaly(key, s)}
        payload = {
            "league": key, "seasons": seasons, "anomalies": anomalies,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "leaders": boards,
            # TOTALS ONLY, not the year-by-year lines. Published whole this was 297 KB for pro,
            # and the index page already fetches one file per league - so a browser that wanted
            # three leaderboards was pulling most of a megabyte to render thirty rows. The
            # per-season detail is not lost: it stays in universe/history, which is in git and
            # is the record that matters. A career page can be served its own file when there
            # is a career page to serve.
            "careers": [{k: v for k, v in r.items() if k != "seasons"}
                        for r in sorted(rows, key=lambda r: -(r.get("Points") or 0))],
        }
        if playoffs:
            payload["playoffs"] = playoffs
        (dst / "careers.json").write_text(json.dumps(payload, separators=(",", ":")),
                                          encoding="utf-8")
        return {"careers": len(rows), "seasons": seasons,
                "playoff_careers": len(playoff_rows)}
    except Exception as exc:                                     # noqa: BLE001
        print(f"  no careers.json for {key} ({exc}); the pages themselves are fine")
        return None


def _career_line(row, stat):
    """One leaderboard entry: who, how much, and enough context to be worth reading."""
    return {"name": row.get("name", ""), "value": row.get(stat, 0),
            "games": row.get("Games", 0), "team": row.get("team", ""),
            "from": row.get("first_season"), "to": row.get("last_season"),
            "ppg": row.get("ppg"), "rpg": row.get("rpg"), "apg": row.get("apg")}


_MONEY = re.compile(r"^\(?\$?([\d,]+)\)?$")


def _money(cell):
    """A dollar cell from the game's own report, or None. Parenthesised means negative."""
    text = (cell or "").strip().replace("&nbsp;", " ").strip()
    m = _MONEY.match(text)
    if not m:
        return None
    value = int(m.group(1).replace(",", ""))
    return -value if text.startswith("(") else value


def _cap_report(src):
    """(cap, mle, lle) off `capreport.htm`, the game's own Salary Cap Report.

    The cap is not printed as such - every row shows Salary and Cap Room, and their SUM is the
    cap, identical on every row. The exceptions are printed parenthesised, which is this report's
    way of saying "room you have not used", so their magnitude is what we want.

    Parsed in PYTHON, deliberately. `page-index.js` states the rule the other way round: the
    browser must never parse the game's HTML, because a second implementation in JavaScript is
    how the two start disagreeing.
    """
    text = (Path(src) / "capreport.htm").read_text(encoding="latin-1", errors="replace")
    flat = re.sub(r"&nbsp;?", " ", re.sub(r"<[^>]+>", "|", text))
    cells = [c.strip() for c in flat.split("|") if c.strip()]
    head = cells.index("Low Exception")
    row = cells[head + 1:head + 8]              # #, Team, Salary, Cap Room, Budget, Mid, Low
    salary, room = _money(row[2]), _money(row[3])
    if salary is None or room is None:
        raise ValueError("capreport.htm has no readable Salary / Cap Room")
    mid, low = _money(row[5]), _money(row[6])
    return salary + room, abs(mid or 0), abs(low or 0)


def _luxury_tax(dat, cap, mle, lle):
    """The luxury tax, which the cap report does not print. None when it cannot be pinned down.

    It is not exposed anywhere readable, so it is found in the binary - but NOT at a fixed
    offset, because the finance block moves as the file grows (measured: 78632 in the live pro
    save, nowhere near it in a clone one season on). Instead the CAP, whose value the report just
    gave us, is located and confirmed by its neighbours - MLE at +28 and LLE at +32 - and the tax
    is the int32 immediately BEFORE it. Two independent sources have to agree before a number is
    believed, and if they do not this returns None and the chart simply draws no tax line.
    """
    needle = struct.pack("<i", cap)
    data, at, hits = dat, dat.find(needle), []
    while at != -1:
        try:
            if (struct.unpack_from("<i", data, at + 28)[0] == mle
                    and struct.unpack_from("<i", data, at + 32)[0] == lle):
                hits.append(at)
        except struct.error:
            pass
        at = data.find(needle, at + 1)
    if len(hits) != 1 or hits[0] < 4:
        return None                            # ambiguous or absent: say nothing rather than guess
    tax = struct.unpack_from("<i", data, hits[0] - 4)[0]
    return tax if 0 < tax < 2_000_000_000 else None


def _write_cap(src, dst, key):
    """Emit `cap.json`: what every team is paying, and the lines that money is read against.

    The site has never shown team money. The two places money appears are both per-character and
    both read `level_history.finances` out of Supabase; nothing anywhere adds a roster up.

    Salaries come from the SAVE rather than from the export, because the export does not carry
    them - `capreport.htm` totals a team but never breaks it down by player.

    Never fatal, like every other writer here. A publish that produced every page is a good
    publish even if this file is missing.
    """
    try:
        from ..codec.league_dat import LeagueDat, POSITIONS
        from .. import characters as ch
        spec = cfg.BY_KEY[key]
        league = LeagueDat(ch.save_path(key))
        ours = _our_players(key)

        ids = sorted(league.teams())
        abbrev_of = {tid: spec.teams[i].abbrev for i, tid in enumerate(ids) if i < len(spec.teams)}
        rosters = {}
        for pl in league.players:
            abbrev = abbrev_of.get(pl.values["Team"])
            if not abbrev:
                continue                       # free agents and the draft pool are not a payroll
            rosters.setdefault(abbrev, []).append({
                "name": pl.name,
                "salary": pl.contract[0] if pl.contract else 0,
                "years": sum(1 for y in pl.contract if y > 0),
                "position": POSITIONS.get(pl.values["Position"], ""),
                "ours": pl.id in ours,
            })

        teams = []
        for team in spec.teams:
            men = sorted(rosters.get(team.abbrev, []), key=lambda m: -m["salary"])
            teams.append({
                "abbrev": team.abbrev, "city": team.city, "nickname": team.nickname,
                "color": team.color, "division": team.division,
                "payroll": sum(m["salary"] for m in men), "players": men,
            })

        cap, mle, lle = _cap_report(src)
        data = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "league": key, "season": season_label(key),
            "cap": cap, "mle": mle, "lle": lle,
            "luxury_tax": _luxury_tax(bytes(league.data), cap, mle, lle),
            # THE SAME FLAG simweek.py uses for a character's Money tab. False means every
            # contract is still the league's setup token, so the page can say so rather than
            # drawing a distribution that does not exist yet.
            "scale": len({m["salary"] for t in teams for m in t["players"]}) > 1,
            "teams": teams,
        }
        (dst / "cap.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return {"teams": len(teams), "payroll": sum(t["payroll"] for t in teams),
                "tax": data["luxury_tax"]}
    except Exception as exc:                                            # noqa: BLE001
        print(f"  no cap.json for {key} ({exc}); the pages themselves are fine")
        return None


def _write_stats(src, dst, key):
    """Emit `stats.json` beside the skinned pages: counting stats and league placings.

    The site needs this to show anybody where they stand, and the alternative is the browser
    fetching and parsing four hundred player pages - which would be a second implementation of
    the export's four traps (the undefeated team reading 1.000, the repeated STL column, the
    digits in the header, the draft pool's borrowed season lines). All four are already handled
    and tested in seasonbonus; this just writes down what it works out.

    Never fatal. A publish that produced every page is a good publish even if this file is
    missing, and the pages are what people came for.
    """
    try:
        from ..seasonbonus import league_stats
        data = league_stats(src)
        data["generated"] = datetime.now().isoformat(timespec="seconds")
        data["league"] = key
        data["season"] = season_label(key)
        # WHERE EACH TEAM'S PAGE IS, so the site's standings can link a team to its roster. The
        # game's own standings page is the one place that pairs a team name with its rosterN id.
        try:
            html = (Path(src) / "standings.htm").read_text(encoding="latin-1", errors="replace")
            roster = {name.strip(): rid for rid, name in
                      re.findall(r'href="\.?/?rosters/(roster\d+)\.htm[^"]*"[^>]*>([^<]+)<', html)}
            for row in data.get("table") or []:
                if row.get("name") in roster:
                    row["roster"] = roster[row["name"]]
        except OSError:
            pass
        (dst / "stats.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return {"players": data["count"], "elite": data["elite"]}
    except Exception as exc:
        print(f"  no stats.json for {key} ({exc}); the pages themselves are fine")
        return None


def publish(keys=None, fresh_mdb=None):
    keys = list(keys or [s.key for s in cfg.LEAGUES])
    freshness = set(keys) if fresh_mdb is None else set(fresh_mdb)
    # The old per-league calls made six serial network requests inside three workers: settings
    # once and characters once for every league. They describe the same publish transaction, so
    # take one consistent snapshot and share it with each independent transform.
    shared_season, shared_characters = None, None
    try:
        from ..simweek import store
        st = store()
        settings = st.get_settings()
        current = settings.get("current_season")
        if current:
            shared_season = f"Season {int(current)}"
        shared_characters = st.characters()
    except Exception:
        pass

    def build(k):
        ours = _our_players(k, shared_characters) if shared_characters is not None else None
        return publish_league(k, k in freshness, season=shared_season, ours=ours)

    def _year(label):
        found = re.search(r"\b(20\d\d)\b", str(label or ""))
        return int(found.group()) if found else None

    # Each league writes its own destination and history files. Running their transforms
    # together overlaps thousands of small file reads/writes and store requests; ordering the
    # returned rows through executor.map keeps the public manifest deterministic.
    with ThreadPoolExecutor(max_workers=min(3, len(keys))) as pool:
        out = list(pool.map(build, keys))
    # One universe-wide feed, rebuilt after every league has finished writing its facts. A
    # partial publish can still use the last data for leagues it did not rebuild.
    if shared_characters is not None:
        try:
            from ..stories import write_story_feed
            write_story_feed(SITE, DOCS, shared_characters, _year(shared_season))
        except Exception as exc:
            print(f"  no stories.json ({exc}); the league pages themselves are fine")
    (SITE / "leagues" / "published.json").write_text(
        json.dumps({"published_at": datetime.now().isoformat(timespec="seconds"), "leagues": out}, indent=1),
        encoding="utf-8")
    return out


PAGES_BRANCH = "gh-pages"


def deploy_losses(live_root, staged_root):
    """What deploying `staged_root` would take away from the live site. [] when nothing.

    THE DEPLOY IS A FORCE PUSH OF ONE MACHINE'S site/ FOLDER, and `site/leagues/` is gitignored -
    58 MB rewritten every publish. So what reaches the public site is whatever that folder holds
    on whichever machine ran git_push, and nothing in git protects it: a machine that has never
    published a league, or still holds an abandoned universe's pages, would replace the live
    site with them and delete games.json outright. Nothing would error. The page would simply go
    blank, and the only copy of those league pages is the machine that made them.

    Two questions, both answered from the live branch itself, which git_push already has checked
    out in the worktree before it empties it:

      * DOES THIS DEPLOY DELETE ANYTHING PUBLISHED? Any league folder or data file that is live
        and not staged. This is what catches the desktop, which has no games.json at all.
      * IS IT EVEN THE SAME UNIVERSE? stats.json carries the season each league was published
        for. A machine holding a different season's pages is not a newer publish of this site,
        it is a different site - and it would look like a successful deploy.
    """
    live_leagues, staged_leagues = Path(live_root) / "leagues", Path(staged_root) / "leagues"
    if not live_leagues.exists():
        return []                       # nothing published yet: nothing can be lost
    losses = []
    for league in sorted(p for p in live_leagues.iterdir() if p.is_dir()):
        staged = staged_leagues / league.name
        if not (staged / "index.htm").exists():
            losses.append(f"leagues/{league.name}: live, and this machine has no pages for it")
            continue
        for data in ("games.json", "stats.json"):
            if (league / data).exists() and not (staged / data).exists():
                losses.append(f"leagues/{league.name}/{data}: live, and missing here")
        here, there = staged / "stats.json", league / "stats.json"
        if here.exists() and there.exists():
            try:
                mine = json.loads(here.read_text(encoding="utf-8")).get("season")
                live = json.loads(there.read_text(encoding="utf-8")).get("season")
            except ValueError:
                continue
            if mine and live and str(mine) != str(live):
                # A NEWER season is the rollover working, not a different universe. Only a
                # season going BACKWARDS means this machine is behind the site - which is the
                # case worth stopping, and the one the desktop would hit. The labels read
                # "Season 2026", so compare the numbers in them and fall back to refusing when
                # neither can be read as a year.
                def _year(label):
                    digits = re.findall(r"\d{4}", str(label))
                    return int(digits[0]) if digits else None

                # A rollover moves the site on by exactly one year, and that is the only
                # difference worth allowing. Behind means this machine is stale; more than one
                # year ahead means it is a different universe - the desktop's abandoned pages
                # are four seasons ahead of the live site, and "newer" would have waved them
                # through into a deploy that replaced everything.
                here, there = _year(mine), _year(live)
                if here is None or there is None or not 0 <= here - there <= 1:
                    losses.append(f"leagues/{league.name}: the live site is {live!r} and this "
                                  f"machine's pages are {mine!r} - a different universe")
    for page in sorted(Path(live_root).glob("*.html")):
        if not (Path(staged_root) / page.name).exists():
            losses.append(f"{page.name}: live, and missing here")
    return losses


def _live_shadow(git, branch, into):
    """A stand-in for the live site holding only what `deploy_losses` actually reads.

    The guard asks the published branch three things: which league folders are live, which of
    their data files exist, and what season each stats.json claims. That is a directory listing
    and three small files - but the only way to hand it a *folder* used to be checking the whole
    branch out, and the branch is 112 MB across 3,258 files. Measured on 2026-09-20, the deploy
    spent about 40 s writing that checkout, deleting it again and copying site/ over the top,
    purely so a guard could read three JSON files and call iterdir().

    So the branch is read with `ls-tree` and `show` instead, and only the paths the guard looks
    at are materialised. Every league directory is created whatever it contains, so a league that
    somehow published without an index.htm still registers as live and is still protected.
    """
    listing = git("ls-tree", "-r", "--name-only", branch, check=False)
    into.mkdir(parents=True, exist_ok=True)
    if listing.returncode:
        return into
    wanted = ("index.htm", "games.json", "stats.json")
    for path in listing.stdout.splitlines():
        path = path.strip()
        if not path:
            continue
        parts = path.split("/")
        # len >= 3, because `leagues/` holds published.json as well as the league folders, and
        # a shadow that turned that file into a DIRECTORY made the guard read it as a league
        # with no pages and refuse every deploy: "leagues/published.json: live, and this machine
        # has no pages for it". Only a path with something after the league name proves the
        # league name is a folder.
        if parts[0] == "leagues" and len(parts) >= 3:
            (into / "leagues" / parts[1]).mkdir(parents=True, exist_ok=True)
        keep = (len(parts) == 3 and parts[0] == "leagues" and parts[2] in wanted) or (
            len(parts) == 1 and path.endswith(".html"))
        if not keep:
            continue
        target = into / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.name == "stats.json":
            # The one file whose CONTENT is read. Bytes, then utf-8: text mode would decode it
            # in the console codepage, and a season label is not guaranteed to be ASCII.
            blob = subprocess.run(["git", "show", f"{branch}:{path}"], cwd=ROOT,
                                  capture_output=True)
            target.write_bytes(blob.stdout)
        else:
            target.touch()
    return into


def git_push(message=None, branch=PAGES_BRANCH, remote="origin", allow_loss=False):
    """Publish `site/` to the Pages branch, league pages and all.

    `site/leagues/` is deliberately gitignored: it is 112 MB, it is rewritten in full on every
    single publish, and committing it to the main history would make the repository unusable
    within a month. But a plain `git add site` therefore pushed the character site with all three
    league sites MISSING - the hub would deploy with three dead links and nothing would say why.

    So the pages go to their own branch as a single orphan commit, built with plumbing against a
    throwaway index: the main history and the main index stay untouched, the deployed site is
    complete, and each publish replaces the branch's one commit rather than adding to it.
    `git add -f` is what gets past the ignore rule; it is deliberate here and nowhere else.

    NO WORKTREE, AND NOTHING IS COPIED. This used to add a worktree (a 112 MB checkout), delete
    every file in it, copy site/ over the top and commit that - three full passes over the tree
    to produce a commit `write-tree` can make from site/ where it already sits. Measured on
    2026-09-20: the copy alone was 11.7 s and the delete 2.4 s, with the checkout larger than
    either, against 3.6 s to hash all 112 MB straight out of site/. The deploy was the
    third-biggest line in a sim week and most of it was moving files around.

    The two files that belong to the DEPLOY rather than to the sources - .nojekyll, and a CNAME
    inherited from the branch - go into the index as blobs, so site/ is never modified to make a
    deploy work.
    """
    message = message or f"Publish the Cheezeyverse {datetime.now():%Y-%m-%d %H:%M}"
    if not (SITE / "leagues").exists():
        raise RuntimeError("site/leagues does not exist yet - publish the league sites first")

    def git(*args, cwd=ROOT, check=True, env=None):
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
        if check and r.returncode and "nothing to commit" not in (r.stdout + r.stderr):
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
        return r

    if not git("remote", "get-url", remote, check=False).stdout.strip():
        raise RuntimeError(
            f"no git remote called {remote!r}. Create the GitHub repository, add it as a remote, "
            "and turn on Pages for the " + branch + " branch.")

    exists = git("rev-parse", "--verify", branch, check=False).returncode == 0
    kept_cname = ""
    if exists and git("rev-parse", "--verify", f"{branch}:CNAME", check=False).returncode == 0:
        kept_cname = git("show", f"{branch}:CNAME", check=False).stdout.strip()

    # A worktree left behind by the old deploy, which nothing uses any more. Removing it is not
    # housekeeping: git still counts a registered worktree as a checkout of that branch, and
    # some operations on the branch refuse while one exists.
    stale = ROOT / "tmp" / "pages-worktree"
    if stale.exists():
        git("worktree", "remove", "--force", str(stale), check=False)
        shutil.rmtree(stale, ignore_errors=True)
    git("worktree", "prune", check=False)

    (ROOT / "tmp").mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="pages-", dir=ROOT / "tmp"))
    try:
        if exists:
            # BEFORE anything is pushed. See deploy_losses: this is the only moment the deploy
            # can compare itself against what it is about to replace, and a force push has no
            # undo.
            losses = deploy_losses(_live_shadow(git, branch, scratch / "live"), SITE)
            if losses and not allow_loss:
                raise RuntimeError(
                    "this deploy would REMOVE published work from the live site:\n  - "
                    + "\n  - ".join(losses)
                    + "\nPublish from the machine that owns the saves (site/leagues/ is "
                      "gitignored, so only that machine has the league pages), or pass "
                      "allow_loss=True if the removal is genuinely wanted.")

        # A throwaway index, so the repository's real index is never touched and a deploy that
        # dies halfway cannot leave 3,258 staged files behind in somebody's working copy.
        env = {**os.environ, "GIT_INDEX_FILE": str(scratch / "index"),
               "GIT_DIR": str(ROOT / ".git"), "GIT_WORK_TREE": str(SITE)}
        git("add", "-A", "-f", ".", cwd=SITE, env=env)

        extras = {".nojekyll": ""}              # or Pages skips folders like js/
        # A custom domain lives in a CNAME file IN the published branch, and this publish
        # replaces that branch wholesale - so without this the domain would break on every sim.
        # site/CNAME is the source of truth; keep whatever the branch already had otherwise.
        if not (SITE / "CNAME").exists() and kept_cname:
            extras["CNAME"] = kept_cname + "\n"
        for name, text in extras.items():
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=ROOT,
                                  input=text.encode("utf-8"), capture_output=True)
            if blob.returncode:
                raise RuntimeError(
                    f"could not stage {name}: {blob.stderr.decode(errors='replace').strip()}")
            git("update-index", "--add", "--cacheinfo",
                f"100644,{blob.stdout.decode().strip()},{name}", env=env)

        tree = git("write-tree", env=env).stdout.strip()
        # check=True, deliberately. A commit that fails - most commonly because the machine has
        # no git user.name/user.email - used to be swallowed here, leaving the branch with no
        # commit at all; the push then failed with "src refspec HEAD does not match any", which
        # describes a symptom three steps downstream of the cause.
        commit = git("commit-tree", tree, "-m", message, env=env).stdout.strip()
        if not commit:
            raise RuntimeError("git commit-tree produced no commit; is user.name/user.email set?")
        git("update-ref", f"refs/heads/{branch}", commit)
        # The commit id, not HEAD: HEAD is the working checkout and has nothing to do with this.
        git("push", "--force", remote, f"{commit}:refs/heads/{branch}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return message



if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    for row in publish(keys):
        print(f'{row["league"]:8} {row["pages"]:4} pages -> {row["path"]}')
    if "--push" in sys.argv:
        print("pushed:", git_push())
