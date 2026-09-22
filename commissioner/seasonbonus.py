"""The end-of-season bonus: what a character earned on top of the flat offseason lump.

Read from FBPB3's own exported HTML for the season just finished, because that is the only
place the game writes down what actually happened - league.dat carries ratings, not box scores.

EVERY NUMBER IS A SETTING. They were tuned twice before they were right and will be tuned
again; nothing here should need a code change to rebalance.

WHY THE STAT PART IS LEAGUE-RELATIVE
The first draft paid a fixed rate per counting stat - a point per 50 points scored, per 25
rebounds and so on. Measured against real exports that inflates badly with level, because
totals grow with BOTH talent and season length: a full season paid the best prep player 22 and
the best pro 115, for the same achievement of leading your league. Both parts below are
measured against the league's own season instead, so leading the pros and leading prep are
worth the same, and a rebalance of one league cannot quietly rebalance the others.

WHAT DEGRADES TO NOTHING
Playoffs, the title and the season awards come from pages FBPB3 only fills in once a season
ends. Before then they are present but empty, and every reader here returns nothing and says
so rather than raising - a bonus that fails closed costs somebody three points, and a bonus
that raises takes down the offseason for everybody.
"""
from __future__ import annotations

import re
from pathlib import Path

# Regular-season totals, in the order FBPB3 prints them after the season year. The header
# repeats STL - "... AST STL TO STL BLK ..." - which is the game's own quirk and not a parse
# error, so BLK is the seventeenth number and not the sixteenth. Getting this wrong is silent:
# every character simply ranks on the wrong statistic.
TOTAL_COLUMNS = ["G", "GS", "MIN", "FGM", "FGA", "FTM", "FTA", "3PM", "3PA", "PTS", "OREB",
                 "REB", "AST", "STL", "TO", "STL_repeat", "BLK", "PF", "PM"]
CATEGORIES = ["PTS", "REB", "AST", "STL", "BLK"]

# The team components are back on at 2 apiece (2026-09-19), after being removed on the premise
# that everyone makes the playoffs. They don't: the brackets take 8 of 16 in prep and college
# and 8 of 20 in the pros. At 3 + 3 a title was worth six points, which crowded out everything a
# player does himself; at 2 + 2 it is worth four, and a good individual season still competes.
#
# Weekly and generic season awards stay off. All-Star selections and the three All-League
# teams have their own season-specific rewards; the generic award switch does not enable them.
# The pages are decoded latin-1, so a name can carry any accented letter in that range, and
# eleven of them do across the three leagues. An ASCII-only class silently DROPPED those
# players - six in prep, five in college, five in pro - which is not merely a shorter list:
# league_stats and the season bonus both rank against this pool, so every rank came out better
# than it was, and the top-25 rule could pay somebody whose true rank was 26th. Caught by the
# server comparing row counts (184 against 190), not by anything failing.
#
# The dash sits last inside each class so it needs no escape, and the classes are built here
# once rather than repeated at three call sites that could drift apart.
NAME_START = "[A-ZÀ-ÖØ-Þ]"
NAME_REST = "[A-Za-zÀ-ÖØ-öø-ÿ'.-]"
TEAM_CHARS = "[A-Za-zÀ-ÖØ-öø-ÿ'. -]"

DEFAULTS = {
    "bonus_playoffs": 2,        # 3 -> off -> 2
    "bonus_title": 2,           # was 3; a team RESULT, not an award, so it never came off
    "bonus_potw": 0,            # was 1
    "bonus_potm": 2,
    # The All-Star game, which the engine selects itself. Nate's call, 2026-09-20: worth 2, the
    # same as Player of the Month, because it is the league saying he was one of its best that
    # year rather than one good month.
    "bonus_allstar": 2,
    "bonus_allleague_1": 3,
    "bonus_allleague_2": 2,
    "bonus_allleague_3": 1,
    "bonus_season_award": 0,    # was 3 (MVP, All-League)
    "bonus_catchup": 3,
    "catchup_share": 0.25,      # played in fewer than this share of his team's games
    "stat_bonus_top_n": 25,
    "stat_bonus_top_max": 2,
    "stat_bonus_elite_rank": 5,
    "stat_bonus_elite_max": 2,
    "bonus_cap": 10,            # the most the bonus can add on top of the flat offseason lump
}

# THE CAP IS PER LEVEL, and the reason is not "college is worth more". Both statistical
# components are ranked WITHIN a league, so a character who moves up arrives at the bottom of a
# stronger field and his EARNED bonus falls - at exactly the moment the price bands start to
# bite, because his ratings are finally crossing 50 and 70 where a step costs 2 and 3 instead
# of 1. A single cap therefore tightens as he climbs, which is backwards.
#
# These live here and NOT in the settings table, deliberately. A stale bonus_* row silently
# overrides a changed default, and this is a number that gets tuned by editing and diffing. An
# explicit settings row still wins, for deliberate live tuning, and cap_for says so.
BONUS_CAP_BY_LEVEL = {"prep": 10, "college": 15, "pro": 20}

# THE PROMOTION GRANT, paid once when a character moves up a level.
#
# It replaces the conversion haircut rather than repaying it. Moving to college used to cost 3%
# of every rating - 22 to 27 points for the first seven, about 41 points to buy back, which is
# two thirds of a college season's entire income spent standing still. That is gone. This is
# paid instead, and it is scaled to the season he is LEAVING, because a flat sum pays the same
# to the man who won prep and the man who never dressed.
#
# NOT subject to bonus_cap. The cap exists so one enormous season cannot dwarf an ordinary one
# in a career of them; a promotion happens once, and capping it would flatten the thing this is
# deliberately trying to make uneven.
GRANT = {
    "grant_base": 20,
    "grant_playoffs": 5,
    "grant_title": 5,
    "grant_top": 3,             # per category placed inside stat_bonus_top_n
    "grant_top_max": 9,
    "grant_elite": 5,
    "grant_catchup": 5,         # played under catchup_share of his team's games
}


def setting(settings, key):
    """A tuned number, or the default. Never raises on a junk value - it falls back."""
    table = DEFAULTS if key in DEFAULTS else GRANT
    try:
        value = (settings or {})[key]
        return type(table[key])(value)
    except (KeyError, TypeError, ValueError):
        return table[key]


def cap_for(level, settings=None):
    """The most the season bonus may add, which depends on the level he played at.

    An explicit `bonus_cap` setting still wins, so live tuning is possible; otherwise the
    per-level table decides. See BONUS_CAP_BY_LEVEL for why one number does not work.
    """
    if settings and "bonus_cap" in settings:
        return setting(settings, "bonus_cap")
    return BONUS_CAP_BY_LEVEL.get(level, DEFAULTS["bonus_cap"])


def _text(path):
    """A page as one flat string of visible text, or "" if it is not there."""
    try:
        raw = Path(path).read_text(encoding="latin-1", errors="replace")
    except OSError:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))


# ---- what the export says happened -------------------------------------------------------
def export_date(html_dir):
    """When FBPB3 wrote these pages, as its own footer states it, or "" if it does not.

    Everything here is read off whatever export happens to be on disk, and nothing in the file
    says which season it belongs to. On the normal path that is fine - the offseason runs right
    after the last sim of the year. It is re-running an offseason with force=True, after a new
    season has already started, that would quietly pay last week's numbers for a season nobody
    played: that path bypasses the last_offseason guard, which is the one thing that would
    otherwise notice. Naming the date in the preview turns a silent wrong answer into an
    obvious one.
    """
    m = re.search(r"Page created:\s*([A-Za-z]+ \d+, \d{4})", _text(Path(html_dir) / "standings.htm"))
    return m.group(1) if m else ""


def standings_rows(html_dir):
    """[{name, w, l, games, pct}] from standings.htm, in the order the page lists them."""
    text = _text(Path(html_dir) / "standings.htm")
    pattern = ("&nbsp;" + r"\s*\*?\s*" + f"({TEAM_CHARS}*?)"
               + r"\s+(\d+)\s+(\d+)\s+\d*\.\d+")
    rows = []
    for name, won, lost in re.findall(pattern, text):
        w, l = int(won), int(lost)
        rows.append({"name": name.strip(), "w": w, "l": l, "games": w + l,
                     "pct": round(w / (w + l), 3) if (w + l) else 0.0})
    return rows


def seeded(rows, top=8, order=None):
    """The best `top` records in the league, by WIN PERCENTAGE, numbered from 1.

    Percentage rather than wins, because the teams in a league are not all the same number of
    games in: today prep spans 22 to 27 played, so ranking by raw wins would put a team three
    games ahead on the calendar above a better team that has played fewer.

    This is OUR ordering, not the game's official bracket. FBPB3 seeds by conference and puts
    division winners first, so a division winner with a losing record can outrank a better team
    - college has exactly that today. The panel says "by record" rather than implying otherwise.

    `order` is {team: its position WITHIN ITS OWN CONFERENCE} from playoffstandings.htm, used as
    the LAST tiebreaker. Two teams on an identical record were first separated by the alphabet,
    which decided 8th in the pros between Threshers and Waxheads at 20-23 apiece. Borrowing the
    game's order fixed that - and, read as one flat list down the page, immediately broke
    something else: a cross-conference tie then went to whichever conference the page happens to
    print first, which is layout, not basketball. The game never ranks two conferences against
    each other, so its page order means nothing across that line.

    Position within the conference is the honest version of the same idea. A same-conference tie
    still gets the game's own tiebreakers; a cross-conference tie goes to whoever is doing better
    where he actually plays. The name stays as the final fallback.
    """
    where = dict(order or {})
    unknown = max(where.values(), default=0) + 1
    best = sorted(rows, key=lambda r: (-r["pct"], -r["w"],
                                       where.get(r["name"], unknown), r["name"]))[:top]
    return [{**r, "seed": i + 1} for i, r in enumerate(best)]


def playoff_order(html_dir):
    """{team: its 1-based position within its own conference} from playoffstandings.htm.

    The page is one table per conference, each starting with a "W L Pct" header row, so the
    counter resets at each header. Deliberately NOT the position down the whole page: that
    ranks a conference against another conference, which the game never does and which would
    hand a tie to whichever section happens to be printed first.
    """
    text = _text(Path(html_dir) / "playoffstandings.htm")
    row = ("&nbsp;" + r"\s*\*?\s*" + f"({TEAM_CHARS}*?)"
           + r"\s+\d+\s+\d+\s+\d*\.\d")
    heads = [m.start() for m in re.finditer(r"W\s+L\s+Pct", text)]
    out = {}
    for n, start in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(text)
        seen = 0
        for name in re.findall(row, text[start:end]):
            name = name.strip()
            if name and name not in out:
                seen += 1
                out[name] = seen
    return out


def standings_teams(html_dir):
    """{nickname: games played} from standings.htm, and the set of real league teams.

    Doubles as the draft-pool filter. The export gives the draft pool its own player pages
    carrying season lines from wherever those players actually played, and they are not this
    league's games: an early version of this ranking pulled in 57 of them and produced a
    247-assist "leader" who had never played a minute in the league. Anybody whose team is not
    in the standings is not in the league.
    """
    return {r["name"]: r["games"] for r in standings_rows(html_dir)}


def season_totals(html_dir, teams=None):
    """{player name: {team, G, PTS, REB, ...}} for everyone on a league team.

    `teams` is the standings map; anybody whose team is not in it (the draft pool, free agents)
    is left out, because their season line was not earned here.
    """
    teams = standings_teams(html_dir) if teams is None else teams
    out = {}
    for page in sorted((Path(html_dir) / "players").glob("*.htm")):
        raw = page.read_text(encoding="latin-1", errors="replace")
        flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", raw))
        block = re.search(r"Season Totals(.*?)Efficiency", flat, re.S)
        if not block:
            continue
        # The data row starts with the season year; the header above it also contains digits
        # (3PM, 3PA), which is what made an earlier version read every column one place out.
        row = re.search(r"&nbsp;(20\d\d)\|((?:\s*\|*\s*-?\d+\s*\|*)+)", block.group(1))
        if not row:
            continue
        values = [int(n) for n in re.findall(r"-?\d+", row.group(2))]
        if len(values) < len(TOTAL_COLUMNS):
            continue
        name = re.search(f"({NAME_START}{NAME_REST}+(?: {NAME_START}{NAME_REST}+)+)&nbsp;", raw)
        team = re.search(r"\d+lbs\s*\|+\s*([^|]+?)\s*\|", flat)
        if not name or not team:
            continue
        team = team.group(1).strip()
        if team not in teams:
            continue
        line = dict(zip(TOTAL_COLUMNS, values))
        line["team"] = team
        # The page's own filename is the game's player id ("player53"), and it is the only
        # stable handle on a player that survives a rename. Names are what the bonus joins on
        # because that is what the store holds, but anything linking to the league site wants
        # this instead.
        line["page"] = page.stem
        out[re.sub(r"<[^>]+>", "", name.group(1)).strip()] = line
    return out


FT_WEIGHT = 0.44     # the standard true-shooting coefficient, and the one FBPB3 itself uses


def true_shooting(pts, fga, fta):
    """PTS / (2 * (FGA + 0.44 * FTA)), or None when nobody has attempted anything.

    Computed rather than read off the page's own Efficiency row, after checking the game's
    number against the formula for all 1385 players in the three leagues. Solving each page for
    the coefficient it implies gives a median of 0.440 to 0.442 everywhere, so the game does use
    0.44 - but on inputs that are not quite the ones it prints in Season Totals, and no single
    coefficient fits every player. At tiny samples its number cannot be reproduced from its own
    page at all: one college player shows .667 off 1-for-3 with no free throws, where the
    arithmetic says .500.

    So this agrees with the PTS, FGA and FTA shown beside it, which the game's own number
    sometimes does not, and it lands within a few thousandths for anybody with real minutes -
    our seven characters differ by .001 to .002, except a six-game player at .015.

    None, not zero: a bench player with no attempts has no true-shooting percentage, and 0.0
    would sort him below somebody genuinely missing everything.
    """
    attempts = float(fga or 0) + FT_WEIGHT * float(fta or 0)
    if attempts <= 0:
        return None
    return round(float(pts or 0) / (2 * attempts), 3)


def league_stats(html_dir, elite_rank=5):
    """Everything the SITE wants from a season, as plain data, computed once at publish time.

    The career page wants a character's counting stats and where they place; the front page
    wants the same thing for everybody. Both could fetch and parse the four hundred player
    pages in the browser, and both would then be a second implementation of the four traps
    documented at the top of this module - the undefeated team, the repeated STL column, the
    digits in the header, the draft pool. One emitter, already under test, instead.
    """
    table = standings_rows(html_dir)
    teams = {r["name"]: r["games"] for r in table}
    totals = season_totals(html_dir, teams)
    lines = elite_lines(totals, elite_rank)
    players = []
    for name, row in totals.items():
        players.append({
            "name": name, "team": row["team"], "page": row.get("page"),
            **{k: row.get(k, 0) for k in ("G", "GS", "MIN", "FGM", "FGA", "FTM", "FTA",
                                          *CATEGORIES)},
            "ts": true_shooting(row.get("PTS"), row.get("FGA"), row.get("FTA")),
            "rank": {cat: rank_in(totals, cat, row.get(cat, 0)) for cat in CATEGORIES},
        })
    players.sort(key=lambda p: -p["PTS"])
    return {
        "generated": None,          # publish fills this in; kept here so the shape is complete
        "export_date": export_date(html_dir),
        "categories": list(CATEGORIES),
        "elite_rank": elite_rank,
        "elite": lines,
        "teams": teams,
        "table": table,
        "seeds": seeded(table, order=playoff_order(html_dir)),
        "count": len(players),
        "players": players,
    }


def elite_lines(totals, rank):
    """{category: the rank-th best total in the league}. The yardstick for the elite part."""
    lines = {}
    for cat in CATEGORIES:
        ordered = sorted((row.get(cat, 0) for row in totals.values()), reverse=True)
        lines[cat] = ordered[rank - 1] if len(ordered) >= rank else 0
    return lines


def rank_in(totals, cat, value):
    """1 + however many players beat this total outright. Ties share the better rank."""
    return 1 + sum(1 for row in totals.values() if row.get(cat, 0) > value)


def _award_row_players(text):
    """Every player named in an award table. One parser for both award pages.

    awards.htm rows read "03/21/2027 SF Our Guy Tulips 9.0 10.0 1.0 1.5 0.5" and
    seasonawards.htm rows read "Most Valuable Player SF Our Guy Tulips 1.0 ...", so the only
    stable landmarks are the position code and the five averages at the end. Anchor on those
    and take everything between the position and the last word, which is the team.

    Matching a name as "capitalised words" instead is what produced winners called
    "Sid McFate Tulips": nothing separates a player from his team but a space, and a name with
    the team stuck on the end matches no character, so every award paid nothing at all.
    """
    return [m.group(1).strip() for m in
            re.finditer(r"\s[A-Z]{1,2}\s+(.+?)\s+\S+(?:\s+-?[\d.]+){5}", text)]


def award_counts(html_dir):
    """{player name: {"potw": n, "potm": n}} from awards.htm.

    Counted by how many times a name appears under each heading, which is what the page is:
    one row per award handed out. Empty before any have been.
    """
    text = _text(Path(html_dir) / "awards.htm")
    out = {}
    marks = [("potw", text.find("Player of the Week")),
             ("potm", text.find("Player of the Month"))]
    marks = [(k, i) for k, i in marks if i != -1]
    for n, (key, start) in enumerate(marks):
        end = marks[n + 1][1] if n + 1 < len(marks) else len(text)
        for name in _award_row_players(text[start:end]):
            out.setdefault(name.strip(), {"potw": 0, "potm": 0})[key] += 1
    return out


# "2027 CVP All-Star" as the player page writes it: the season, the league's abbreviation, and
# the award. Anchored at both ends, which is the whole trick - "2027 CVP All-Star Game MVP" is a
# DIFFERENT award printed on the same page, and a prefix match pays the wrong people. The middle
# is loose because the abbreviation is per league and some award lines spell it out in full
# ("2026 Cheezeyverse Prep Champion").
ALL_STAR_LINE = re.compile(r"(\d{4})\s+.+\bAll-Star", re.I)
ALL_LEAGUE_LINE = re.compile(
    r"(\d{4})\s+.+\bAll-League\s+(First|Second|Third|1st|2nd|3rd)\s+Team", re.I)
ALL_LEAGUE_TIERS = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}
ALL_LEAGUE_LABELS = {1: "1st", 2: "2nd", 3: "3rd"}


def player_honours(html_dir, player_ids=None):
    """Player identity, profile link and season-specific All-Star / All-League selections.

    READ FROM THE PLAYER PAGES, because that is the only place the export states it. Neither
    awards.htm nor seasonawards.htm mentions the All-Star game at all - checked on a real
    export, both come back with no match - so there is no league-wide page to read and the 425
    player pages are the source. Parsed once per league and kept in the caller's cache.

    The page is a run of `&nbsp;`-separated items: the player's name is the first, and his
    honours are one item each. Splitting on that separator is what keeps "All-Star" apart from
    "All-Star Game MVP"; searching the page for the words would conflate them.
    """
    out = []
    players = Path(html_dir) / "players"
    if not players.is_dir():
        return out
    pages = ([(players / f"player{int(pid)}.htm") for pid in player_ids]
             if player_ids is not None else players.glob("player*.htm"))
    for page in pages:
        if not page.is_file():
            continue
        items = [i.strip() for i in _text(page).split("&nbsp;")]
        # THE NAME IS THE ITEM BEFORE THE DESCRIPTOR, not the second item on the page. The
        # second item only works when the stylesheet happens to sit in front of it, which is
        # true of a real export and not of a hand-built one - and when it is wrong it is not
        # empty, it is "#7 SF | 5-10, 138lbs | Tulips | ...". Two players on the same team then
        # collapse into one key and inherit each other's honours, which a test caught.
        # "#1 SF | 6-8, 202lbs | Tulips | ..." is the landmark, and it is on every player page.
        name, position, team = "", "", ""
        for i, item in enumerate(items):
            descriptor = re.match(r"#\d+\s+(\S+)\s*\|", item)
            if descriptor:
                name = next((prev for prev in reversed(items[:i]) if prev), "")
                position = descriptor.group(1)
                parts = item.split("|")
                team = parts[2].strip() if len(parts) > 2 else ""
                break
        if not name:
            continue
        stars, league_teams = set(), {}
        for item in items:
            m = ALL_STAR_LINE.fullmatch(item)
            if m:
                stars.add(int(m.group(1)))
            m = ALL_LEAGUE_LINE.fullmatch(item)
            if m:
                year, tier = int(m.group(1)), ALL_LEAGUE_TIERS[m.group(2).lower()]
                # A repeated honour, or multiple team rows, must not pay twice.
                league_teams[year] = min(tier, league_teams.get(year, tier))
        out.append({"name": name, "position": position, "team": team,
                    "page": f"players/{page.name}", "all_stars": stars,
                    "all_league": league_teams})
    return out


def all_star_seasons(html_dir):
    """{player name: {season, ...}} for callers that only need All-Star selections."""
    out = {}
    for player in player_honours(html_dir):
        if player["all_stars"]:
            out.setdefault(player["name"], set()).update(player["all_stars"])
    return out


def season_award_winners(html_dir):
    """Every name on seasonawards.htm. Empty until the season has actually ended."""
    text = _text(Path(html_dir) / "seasonawards.htm")
    head = text.find("Award")
    if head == -1:
        return set()
    return set(_award_row_players(text[head:]))


def bracket_season(html_dir):
    """The season playoffs.htm describes, or None. The page heads itself "2026 Playoff Brackets"."""
    text = _text(Path(html_dir) / "playoffs.htm")
    m = re.search(r"(\d{4})\s+Playoff Bracket", text)
    return int(m.group(1)) if m else None


def playoff_bracket(html_dir, season=None, rounds=None):
    """(qualifiers, champion) read from playoffs.htm, the only page that states either.

    `season` REFUSES A BRACKET FROM ANOTHER YEAR, and that is not hypothetical. The file is
    replaced only when a new postseason is played, so all through the following season it still
    describes the last one - checked on the live export mid-2027 and it is the 2026 bracket,
    naming Tulips as champion. Reading it without asking which year it covers pays the playoff
    and title bonuses a second time for a title already paid for, and the ledger line looks
    exactly like a correct one. The sim guard already does this check; the bonus did not.

    None means "do not care", which is what the panel and the tests want when they are simply
    asking what the page says.

    Returns (None, None) when there is no bracket - no file, or nothing parseable. NOT an empty
    set: before the playoffs exist the file is absent, and an empty set would look exactly like
    "nobody qualified" and silently pay the playoff bonus to no one.

    WHY NOT THE PAGES THIS USED TO READ. Both earlier sources were wrong, and both were wrong
    silently, and a season-end rehearsal on a copy of CV_Prep is what proved it:

      * playoffstandings.htm marks DIVISION WINNERS with an asterisk, not qualifiers. Prep takes
        the top four of each conference, so the real bracket was eight teams and the asterisks
        found four. Half the qualifiers lost their bonus, every season, not merely at season end.
      * champs.htm prints the season, the champion, the OPPONENT and both win counts on one row,
        so "the first team whose name appears on the page" returned the LOSER. It also keeps one
        row per season, so from year two every past finalist matches too. And it is empty for
        some unknown window after the final - it was header-only at 5/1 and filled by 6/21.

    playoffs.htm has neither problem. It is complete from the moment the final ends, it lists
    every qualifier including the first-round losers, and it states who won each series.

    HOW IT PARSES. The page is a rowspan bracket, so neither document order nor column position
    tells you the round - the final is the fourth of seven pairs. What does hold is that the
    "#seed Team wins" entries pair up CONSECUTIVELY into series. The champion is then simply the
    team that won the most of them, which holds for any bracket shape: reaching the final means
    winning one more series than anybody who did not.
    """
    # ENTITIES ARE NOT WHITESPACE. _text strips tags and collapses spaces but leaves entities
    # alone, and this page separates every token with the NUMERIC entity &#160; - 49 of them and
    # not one &nbsp;. So the text this actually receives reads "#1 &#160; Tulips 0 &#160; #4",
    # and a pattern written against "#1 Tulips 0" matches nothing at all. Shipped once, because
    # the page text I was working from had been flattened for legibility before I saw it and the
    # fixture I built from it inherited the same tidy spacing.
    #
    # Fixed HERE and not in _text(): the standings and award readers anchor on a literal
    # "&nbsp;", so normalising entities globally silently empties standings_teams and every
    # league_stats player. Tried and measured on the live saves before it was ruled out.
    if season is not None:
        theirs = bracket_season(html_dir)
        if theirs is not None and int(theirs) != int(season):
            return None, None
    text = _text(Path(html_dir) / "playoffs.htm")
    text = re.sub("[ ]+", " ", re.sub("(?:&nbsp;|&#160;|&#xA0;|&#xa0;)", " ", text))
    if not text.strip():
        return None, None
    entry = re.compile("#([0-9]+) (" + NAME_REST + "+(?: " + NAME_REST + "+)*?) ([0-9]+)"
                       "(?= #| Fast Break|$)")
    found = entry.findall(text)
    if len(found) < 2:
        return None, None

    series = [(found[i], found[i + 1]) for i in range(0, len(found) - 1, 2)]

    # HOW LONG IS A SERIES? The bracket page NEVER SAYS, and it differs by round AND by league:
    # prep plays best-of-one until a best-of-three final, college best-of-one throughout, pro
    # best-of-five and then best-of-seven. So "one side is ahead" is not "one side has won".
    # Pro's 2028 League Finals sat at "#1 Leghorns 1  #1 Swiss 0" while it was still being
    # played; that is not a tie, so the guard below let it through, credited Leghorns with a
    # third series against Swiss's two, and champion() named a team that had won one game of
    # seven. The bonus pays a league title from that name.
    #
    # `rounds` is LeagueSpec.playoff_rounds - series LENGTHS, the final last, leading zeros for
    # rounds a bracket does not have - and it is the ONLY exact answer. Wins needed is
    # (length + 1) // 2, so prep is 1, 1, 2 and pro is 3, 4, 4. PASS IT WHEREVER THE LEAGUE IS
    # KNOWN; config.py validates it and simweek.round_one_days() already reads the same field.
    #
    # WITHOUT IT we fall back to inferring each round's clinch from the highest total seen in
    # that round, carried forward on the rule that a round is never shorter than the one before
    # it. THE FALLBACK IS A FLOOR, NOT A GUARANTEE: it catches pro, whose earlier rounds clinch
    # at 3 and 4 so a 1-0 final cannot reach them, but NOT prep, where every earlier round tops
    # out at 1 and so does a final sitting at 1-0 - and prep is the league our characters are in.
    # Inferring cannot tell "best-of-one, won" from "best-of-three, leading 1-0" from the page
    # alone. That gap is why `rounds` exists; the fallback only keeps an unwired caller sane.
    #
    # ONE CLINCH FOR THE WHOLE BRACKET, taken as its maximum, is the tempting cheaper rule and it
    # is WRONG either way: prep's final needs two, which would mark its best-of-one first round
    # undecided and strip the champion of the series he really won. Per round, or not at all.
    #
    # In this rowspan layout the round of pair i is v2(i + 1) + 1: prep, college and pro all lay
    # out as [R1, CF, R1, FINAL, R1, CF, R1].
    def _round_of(index):
        n, depth = index + 1, 1
        while n % 2 == 0:
            n //= 2
            depth += 1
        return depth

    present = sorted({_round_of(i) for i in range(len(series))})
    live = [int(n) for n in (rounds or ()) if int(n) > 0]
    clinch = {}
    if len(live) == len(present):
        for round_no, length in zip(present, live):
            clinch[round_no] = (int(length) + 1) // 2
    else:
        # No usable spec, or a bracket shape it does not describe - infer, and see the warning
        # above about what inferring cannot see.
        seen = {}
        for i, ((_s1, _a, w1), (_s2, _b, w2)) in enumerate(series):
            round_no = _round_of(i)
            seen[round_no] = max(seen.get(round_no, 0), int(w1), int(w2))
        longest = 0
        for round_no in present:
            longest = max(longest, seen.get(round_no, 0))
            clinch[round_no] = longest

    qualifiers, wins, undecided = set(), {}, False
    for i, ((_s1, a, w1), (_s2, b, w2)) in enumerate(series):
        qualifiers.update((a, b))
        if int(w1) == int(w2) or max(int(w1), int(w2)) < clinch[_round_of(i)]:
            # a series still being played, or one that has not started. The bracket still names
            # the qualifiers correctly; it just cannot yet say who won.
            undecided = True
            continue
        wins[a if int(w1) > int(w2) else b] = wins.get(a if int(w1) > int(w2) else b, 0) + 1

    if undecided or not wins:
        return qualifiers or None, None
    best = max(wins.values())
    leaders = [t for t, n in wins.items() if n == best]
    return qualifiers, (leaders[0] if len(leaders) == 1 else None)


def playoff_teams(html_dir, season=None, rounds=None):
    """Every team that reached the playoffs, or None when there is no bracket to read."""
    return playoff_bracket(html_dir, season, rounds)[0]


def champion(html_dir, teams=None, season=None, rounds=None):
    """Who won it, or None. `teams` is accepted and ignored; the bracket needs no help.

    Pass `rounds` (LeagueSpec.playoff_rounds) wherever the league is known - see playoff_bracket.
    """
    return playoff_bracket(html_dir, season, rounds)[1]


# ---- the bonus ---------------------------------------------------------------------------
def for_character(name, html_dir, settings=None, cache=None, rounds=None, league=None):
    """[(reason, points)] for one character, already capped. Empty list means nothing earned.

    `cache` is a dict the caller may reuse across characters in the same league: parsing 425
    player pages once per character would make the offseason unusably slow.
    """
    s = settings or {}
    c = cache if cache is not None else {}
    # The season being settled, read once. Everything that can be won in a PARTICULAR year -
    # the playoff bonus, the title, the All-Star selection - is checked against it, because the
    # export keeps last year's answer on disk until a new one replaces it.
    try:
        this_season = int(s.get("current_season"))
    except (TypeError, ValueError):
        this_season = None
    if "teams" not in c:
        c["teams"] = standings_teams(html_dir)
        c["totals"] = season_totals(html_dir, c["teams"])
        c["elite"] = elite_lines(c["totals"], setting(s, "stat_bonus_elite_rank"))
        c["awards"] = award_counts(html_dir)
        c["season_awards"] = season_award_winners(html_dir)
        c["honours"] = player_honours(html_dir)
        # `rounds` matters MORE here than anywhere else: this is the path that PAYS. It feeds
        # both the league-title bonus and "made the playoffs", so an unclinched final read as
        # a win hands real points to the wrong roster. Without it prep falls back to the
        # inferred clinch, which cannot see a best-of-three final sitting at 1-0.
        c["playoffs"], c["champion"] = playoff_bracket(html_dir, this_season, rounds)

    line = c["totals"].get(name)
    if not line:
        return []                      # never played in this league; nothing to reward
    rows = []

    # -- the team's season
    team = line["team"]
    # None is "there is no bracket to read", which is the normal state before the playoffs are
    # played - and is NOT the same as an empty set. Treating the two alike is how a missing page
    # would quietly pay the playoff bonus to nobody at the one moment it is owed.
    if c["playoffs"] and team in c["playoffs"]:
        rows.append(("season bonus: made the playoffs", setting(s, "bonus_playoffs")))
    if c["champion"] and team == c["champion"]:
        rows.append(("season bonus: won the league", setting(s, "bonus_title")))

    # -- individual awards
    got = c["awards"].get(name, {})
    if got.get("potw"):
        rows.append((f'season bonus: player of the week x{got["potw"]}',
                     got["potw"] * setting(s, "bonus_potw")))
    if got.get("potm"):
        rows.append((f'season bonus: player of the month x{got["potm"]}',
                     got["potm"] * setting(s, "bonus_potm")))
    if name in c["season_awards"]:
        rows.append(("season bonus: a season award", setting(s, "bonus_season_award")))

    # -- the All-Star game, ONLY for the season being settled.
    # The player page lists every honour of his career, so a man picked in 2026 still carries
    # that line in 2027 and would be paid again every single year. The season comes from the
    # settings the offseason is running against, and if it cannot be read the bonus is NOT paid
    # - silently paying for the wrong year is worse than not paying at all, and the ledger line
    # would name a season nobody could check.
    honours = [p for p in c["honours"] if p["name"] == name]
    if this_season and any(this_season in p["all_stars"] for p in honours):
        rows.append((f"season bonus: {this_season} All-Star", setting(s, "bonus_allstar")))
    tiers = [p["all_league"][this_season] for p in honours if this_season in p["all_league"]]
    if tiers:
        tier = min(tiers)
        rows.append((f"season bonus: {this_season} All-League {ALL_LEAGUE_LABELS[tier]} team",
                     setting(s, f"bonus_allleague_{tier}")))

    # -- statistics, both halves league-relative
    top_n, top_max = setting(s, "stat_bonus_top_n"), setting(s, "stat_bonus_top_max")
    placed = [cat for cat in CATEGORIES
              if rank_in(c["totals"], cat, line.get(cat, 0)) <= top_n]
    if placed:
        earned = min(len(placed), top_max)
        rows.append((f'season bonus: top {top_n} in {", ".join(placed)}', earned))

    elite_max = setting(s, "stat_bonus_elite_max")
    elite = sum(line.get(cat, 0) // c["elite"][cat]
                for cat in CATEGORIES if c["elite"].get(cat))
    if elite:
        rows.append((f"season bonus: elite-line production x{elite}", min(elite, elite_max)))

    # -- the one that pays for NOT playing
    # Deliberately not scaled by minutes: a character the coach froze out did not choose that,
    # and the universe's rule is that minutes are earned on the floor, not bought. Points are
    # what he gets; he still has to spend them well to earn the minutes back. A character who
    # joined mid-season also qualifies, which is intended - he is a development case too.
    played = c["teams"].get(team, 0)
    if played and line.get("G", 0) < played * setting(s, "catchup_share"):
        rows.append((f'season bonus: development ({line.get("G", 0)} of {played} games)',
                     setting(s, "bonus_catchup")))

    return _capped(rows, cap_for(league, s))


def promotion_grant(name, html_dir, settings=None, cache=None, rounds=None):
    """What a character is paid for the season he is leaving, when he moves up a level.

    Reads the SAME cache `for_character` builds, at the same moment and against the same export,
    so the grant and the season bonus can never disagree about who made the playoffs. Returns
    ledger rows, uncapped - see GRANT for why.

    Empty when he has no line in this league's totals, which is the same "never played here"
    guard the season bonus uses. That is not a character who earns nothing; it is a character
    this export cannot speak about, and paying a base for him would invent a season.
    """
    s = settings or {}
    c = cache if cache is not None else {}
    if "teams" not in c:
        for_character(name, html_dir, settings, c, rounds)     # fills the cache, result unused
    line = (c.get("totals") or {}).get(name)
    if not line:
        return []

    rows = [("promotion: a prep career", setting(s, "grant_base"))]
    team = line["team"]
    if c.get("playoffs") and team in c["playoffs"]:
        rows.append(("promotion: made the playoffs", setting(s, "grant_playoffs")))
    if c.get("champion") and team == c["champion"]:
        rows.append(("promotion: won the league", setting(s, "grant_title")))

    top_n = setting(s, "stat_bonus_top_n")
    placed = [cat for cat in CATEGORIES
              if rank_in(c["totals"], cat, line.get(cat, 0)) <= top_n]
    if placed:
        earned = min(len(placed) * setting(s, "grant_top"), setting(s, "grant_top_max"))
        rows.append((f'promotion: top {top_n} in {", ".join(placed)}', earned))

    if any(line.get(cat, 0) // c["elite"][cat] for cat in CATEGORIES if c["elite"].get(cat)):
        rows.append(("promotion: elite-line production", setting(s, "grant_elite")))

    # The same rule the season bonus uses, and for the same reason: a character the coach froze
    # out did not choose that, and he is the one who most needs points to earn the minutes back.
    played = c["teams"].get(team, 0)
    if played and line.get("G", 0) < played * setting(s, "catchup_share"):
        rows.append((f'promotion: development ({line.get("G", 0)} of {played} games)',
                     setting(s, "grant_catchup")))
    return rows


def _capped(rows, cap):
    """Trim to the cap, and say so in the ledger rather than silently paying less.

    Zero-point rows are dropped first: grant_points refuses an amount of 0 outright, so a
    component somebody had turned off by setting it to 0 would raise mid-offseason rather than
    simply not being paid.
    """
    rows = [(reason, points) for reason, points in rows if points]
    total = sum(points for _, points in rows)
    if total <= cap:
        return rows
    return rows + [(f"season bonus: capped at {cap} (earned {total})", cap - total)]
