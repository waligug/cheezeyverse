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
# The two award components stay OFF, per "make it so only player of the month award rewards
# points". Zeroed rather than deleted: a zero-point row is dropped before anything is paid, so an
# off component costs one dictionary lookup, keeps its parser under test, and comes back from the
# settings table without a deploy.
DEFAULTS = {
    "bonus_playoffs": 2,        # 3 -> off -> 2
    "bonus_title": 2,           # was 3; a team RESULT, not an award, so it never came off
    "bonus_potw": 0,            # was 1
    "bonus_potm": 2,            # the only award that pays
    "bonus_season_award": 0,    # was 3 (MVP, All-League)
    "bonus_catchup": 3,
    "catchup_share": 0.25,      # played in fewer than this share of his team's games
    "stat_bonus_top_n": 25,
    "stat_bonus_top_max": 2,
    "stat_bonus_elite_rank": 5,
    "stat_bonus_elite_max": 2,
    "bonus_cap": 10,            # the most the bonus can add on top of the flat offseason lump
}


def setting(settings, key):
    """A tuned number, or the default. Never raises on a junk value - it falls back."""
    try:
        value = (settings or {})[key]
        return type(DEFAULTS[key])(value)
    except (KeyError, TypeError, ValueError):
        return DEFAULTS[key]


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


def standings_teams(html_dir):
    """{nickname: games played} from standings.htm, and the set of real league teams.

    Doubles as the draft-pool filter. The export gives the draft pool its own player pages
    carrying season lines from wherever those players actually played, and they are not this
    league's games: an early version of this ranking pulled in 57 of them and produced a
    247-assist "leader" who had never played a minute in the league. Anybody whose team is not
    in the standings is not in the league.
    """
    text = _text(Path(html_dir) / "standings.htm")
    teams = {}
    # "&nbsp; Berries 13 10 .565" - the asterisk before a clinched team is optional.
    # The percentage is `\d*\.\d+`, NOT `\.\d+`: an undefeated team reads 1.000, and requiring
    # the leading dot made the one team in the league that had won everything invisible. It cost
    # more than a missing row - the Tulips were 24-0, so leaving them out dragged every
    # league-relative threshold down and silently robbed their players of a team bonus.
    for name, won, lost in re.findall(r"&nbsp;\s*\*?\s*([A-Za-z][A-Za-z'. -]*?)\s+"
                                      r"(\d+)\s+(\d+)\s+\d*\.\d+", text):
        teams[name.strip()] = int(won) + int(lost)
    return teams


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
        name = re.search(r"([A-Z][A-Za-z'.\-]+(?: [A-Z][A-Za-z'.\-]+)+)&nbsp;", raw)
        team = re.search(r"\d+lbs\s*\|+\s*([^|]+?)\s*\|", flat)
        if not name or not team:
            continue
        team = team.group(1).strip()
        if team not in teams:
            continue
        line = dict(zip(TOTAL_COLUMNS, values))
        line["team"] = team
        out[re.sub(r"<[^>]+>", "", name.group(1)).strip()] = line
    return out


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


def season_award_winners(html_dir):
    """Every name on seasonawards.htm. Empty until the season has actually ended."""
    text = _text(Path(html_dir) / "seasonawards.htm")
    head = text.find("Award")
    if head == -1:
        return set()
    return set(_award_row_players(text[head:]))


def playoff_teams(html_dir):
    """Nicknames marked with a * on playoffstandings.htm, i.e. the ones that got in."""
    text = _text(Path(html_dir) / "playoffstandings.htm")
    return {n.strip() for n in
            re.findall(r"&nbsp;\s*\*\s*([A-Za-z][A-Za-z'. -]*?)\s+\d+\s+\d+\s+\d*\.\d",
                       text)}


def champion(html_dir, teams=None):
    """The nickname on champs.htm, or None while nobody has won anything yet."""
    text = _text(Path(html_dir) / "champs.htm")
    for name in (teams or {}):
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


# ---- the bonus ---------------------------------------------------------------------------
def for_character(name, html_dir, settings=None, cache=None):
    """[(reason, points)] for one character, already capped. Empty list means nothing earned.

    `cache` is a dict the caller may reuse across characters in the same league: parsing 425
    player pages once per character would make the offseason unusably slow.
    """
    s = settings or {}
    c = cache if cache is not None else {}
    if "teams" not in c:
        c["teams"] = standings_teams(html_dir)
        c["totals"] = season_totals(html_dir, c["teams"])
        c["elite"] = elite_lines(c["totals"], setting(s, "stat_bonus_elite_rank"))
        c["awards"] = award_counts(html_dir)
        c["season_awards"] = season_award_winners(html_dir)
        c["playoffs"] = playoff_teams(html_dir)
        c["champion"] = champion(html_dir, c["teams"])

    line = c["totals"].get(name)
    if not line:
        return []                      # never played in this league; nothing to reward
    rows = []

    # -- the team's season
    team = line["team"]
    if team in c["playoffs"]:
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

    return _capped(rows, setting(s, "bonus_cap"))


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
