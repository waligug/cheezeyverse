"""The end-of-season bonus, against a hand-built export that contains every trap.

The fixture is written by this file rather than copied from a real export, deliberately. The
only other save-based test in the tree cannot run on SERVERPC at all, because `fixtures/saves/`
is gitignored and the server is a clone - and SERVERPC is the box where this code decides what
real people get paid. A test that writes its own pages runs everywhere.

Every row below is a defect that was actually in the parser, found by running it against the
live prep export:

  * THE UNDEFEATED TEAM. A 24-0 team's percentage reads `1.000`, not `.652`. The regex wanted a
    leading dot, so the best team in the league vanished from the standings - which is not just
    a missing row: it dropped its players out of the ranking pool, pulled every league-relative
    threshold down, and quietly denied a character his playoff bonus.
  * THE REPEATED COLUMN. FBPB3 prints "... AST STL TO STL BLK ...", so BLK is the seventeenth
    number after the season year, not the sixteenth. Off by one and everyone ranks on fouls.
  * THE HEADER THAT CONTAINS DIGITS. `3PM` and `3PA` are in the header row, so "find the
    numbers" finds those first and reads every column one place out.
  * THE DRAFT POOL. Those players get pages with season lines earned somewhere else. Left in,
    they outrank everybody: one had 247 assists without playing a minute in the league.
  * THE AWARD ROW. "date POS Player Team ppg rpg apg spg bpg" has nothing between the player and
    his team but a space, so a name matched as "capitalised words" swallows the team and yields
    winners called "Sid McFate Tulips" - who match no character, so awards silently pay nothing.

    python tests/test_season_bonus.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import seasonbonus as sb  # noqa: E402

# G GS MIN FGM FGA FTM FTA 3PM 3PA PTS OREB REB AST STL TO STL BLK PF +/-
PLAYERS = [
    # name,            team,      G,  PTS, REB, AST, STL, BLK
    ("Ace Elite",      "Tulips",  24, 500, 300,  90,  50,  40),
    ("Bo Second",      "Tulips",  24, 400, 250,  80,  40,  30),
    ("Cy Third",       "Clams",   24, 300, 200,  70,  30,  20),
    ("Dee Fourth",     "Clams",   24, 200, 150,  60,  20,  10),
    ("Ed Fifth",       "Clams",   24, 100, 100,  50,  10,   5),   # <- the elite line
    ("Fay Sixth",      "Clams",   24,  50,  50,  25,   5,   2),
    ("Our Guy",        "Tulips",  24,  90,  90,  45,   9,   4),
    ("Benched Kid",    "Clams",    4,  10,  10,   5,   1,   1),
    ("Drafted Ringer", "Draft",   34, 999, 999, 247, 999, 999),   # must never rank
    # An accented name, because an ASCII-only class silently dropped eleven real players across
    # the three leagues - and a silently SHORTER pool is not a cosmetic bug: everybody left in
    # ranks better than they should, and the top-25 rule pays somebody whose true rank is 26th.
    # Deliberately last in every category, so adding him cannot move the elite lines.
    ("Bartolomé Drexler", "Clams", 24, 5, 5, 2, 0, 0),
]


def _player_page(name, team, g, pts, reb, ast, stl, blk, honours=()):
    """`honours` are the award lines FBPB3 prints one per &nbsp;-separated item, exactly as a
    real page does: "2027 CVP All-Star", and separately "2027 CVP All-Star Game MVP"."""
    row = [g, g, 300, 1, 2, 1, 2, 1, 2, pts, 1, reb, ast, stl, 5, stl, blk, 3, 0]
    cells = "".join(f"<td>{v}</td>" for v in row)
    honour_html = "".join(f"<td>&nbsp;{h}</td>" for h in honours)
    return f"""<html><body>
      <b>{name}&nbsp;</b><td>#7 SF | 5-10, 138lbs | {team} | Experience: 1 year</td>
      {honour_html}
      <table><tr><td>&nbsp;Season Totals</td></tr>
      <tr><td>&nbsp;Season</td><td>G</td><td>GS</td><td>MIN</td><td>FGM</td><td>FGA</td>
          <td>FTM</td><td>FTA</td><td>3PM</td><td>3PA</td><td>PTS</td><td>OREB</td><td>REB</td>
          <td>AST</td><td>STL</td><td>TO</td><td>STL</td><td>BLK</td><td>PF</td><td>+/-</td></tr>
      <tr><td>&nbsp;2026</td>{cells}</tr></table>
      <table><tr><td>&nbsp;Efficiency</td></tr></table>
      </body></html>"""


def _build(tmp, *, champion=None, season_awards=False, postseason=True, honours=None):
    """`honours` is {player name: [award line, ...]} written onto that player's page."""
    d = Path(tmp)
    (d / "players").mkdir(parents=True, exist_ok=True)
    honours = honours or {}
    for i, p in enumerate(PLAYERS):
        (d / "players" / f"player{i}.htm").write_text(
            _player_page(*p, honours=honours.get(p[0], ())), encoding="latin-1")

    # Tulips are undefeated, so their percentage has a digit before the dot.
    (d / "standings.htm").write_text(
        "<table><tr><td>W</td><td>L</td><td>Pct</td></tr>"
        "<tr><td>&nbsp; Tulips</td><td>24</td><td>0</td><td>1.000</td></tr>"
        "<tr><td>&nbsp; Clams</td><td>17</td><td>7</td><td>.708</td></tr></table>",
        encoding="latin-1")
    # The asterisk marks DIVISION WINNERS, not qualifiers - reading it is what found four of
    # prep's eight playoff teams. Only Tulips is starred here, and both teams qualified.
    (d / "playoffstandings.htm").write_text(
        "<table><tr><td>&nbsp;* Tulips</td><td>24</td><td>0</td><td>1.000</td></tr>"
        "<tr><td>&nbsp; Clams</td><td>17</td><td>7</td><td>.708</td></tr></table>",
        encoding="latin-1")
    # champs.htm names the champion AND the beaten opponent on one row, which is what made
    # "the first team named on the page" return the loser.
    (d / "champs.htm").write_text(
        "<html><body><table><tr><td>Season</td><td>Champion</td><td>Wins</td>"
        "<td>Opponent</td><td>Wins</td><td>MVP</td></tr>"
        + ("<tr><td>&nbsp;2026</td><td>Clams</td><td>2</td><td>Tulips</td><td>0</td>"
           "<td>PF Somebody</td></tr>" if champion else "")
        + "</table></body></html>", encoding="latin-1")
    # playoffs.htm is the only page that states the bracket: every qualifier, including the
    # first-round losers, and who won each series. Written only once there IS a postseason.
    if postseason:
        # &#160; BETWEEN EVERY TOKEN, because that is what the game writes - 49 of them on the
        # real page and not one &nbsp;. _text strips tags and collapses whitespace but leaves
        # entities alone, so a fixture spaced with plain spaces is a DIFFERENT page from the
        # one in production. That exact difference shipped a bracket reader that returned
        # (None, None) on every real export while this test passed, because the page text it
        # was written from had been flattened for legibility first.
        (d / "playoffs.htm").write_text(
            "<html><body><table><tr><td>2026 Playoff Brackets</td></tr>"
            "<tr><td>#1 &#160; Clams 2</td><td>#2 &#160; Tulips 0</td></tr></table>"
            "Fast Break Pro Basketball 3</body></html>",
            encoding="latin-1")
    (d / "awards.htm").write_text(
        "<div>Player of the Week</div><table>"
        "<tr><td>&nbsp;03/21/2027</td><td>SF</td><td>Our Guy</td><td>Tulips</td>"
        "<td>9.0</td><td>10.0</td><td>1.0</td><td>1.5</td><td>0.5</td></tr>"
        "<tr><td>&nbsp;03/14/2027</td><td>SF</td><td>Ace Elite</td><td>Tulips</td>"
        "<td>9.0</td><td>5.0</td><td>1.0</td><td>0.0</td><td>0.0</td></tr></table>"
        "<div>Player of the Month</div><table>"
        "<tr><td>&nbsp;February</td><td>SF</td><td>Our Guy</td><td>Tulips</td>"
        "<td>18.1</td><td>7.6</td><td>2.4</td><td>0.7</td><td>0.1</td></tr></table>",
        encoding="latin-1")
    awards = ("<table><tr><td>&nbsp;Award</td><td>Pos</td><td>Player</td></tr>"
              "<tr><td>&nbsp;Most Valuable Player</td><td>SF</td><td>Our Guy</td>"
              "<td>Tulips</td><td>1.0</td><td>1.0</td><td>1.0</td><td>1.0</td><td>1.0</td></tr>"
              "</table>") if season_awards else "<table><tr><td>&nbsp;Award</td></tr></table>"
    (d / "seasonawards.htm").write_text(awards, encoding="latin-1")
    return d


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bonus-"))
    try:
        d = _build(tmp)

        teams = sb.standings_teams(d)
        assert teams == {"Tulips": 24, "Clams": 24}, f"undefeated team lost: {teams}"

        totals = sb.season_totals(d, teams)
        assert "Drafted Ringer" not in totals, "the draft pool got into the ranking pool"
        assert "Bartolomé Drexler" in totals, (
            "an accented name was dropped; the pool is short and every rank is too good")
        assert len(totals) == 9, totals.keys()
        ace = totals["Ace Elite"]
        assert (ace["PTS"], ace["REB"], ace["AST"], ace["STL"], ace["BLK"]) == (500, 300, 90, 50, 40), ace
        assert ace["BLK"] != ace["PF"], "BLK read from the wrong column"

        assert sb.elite_lines(totals, 5) == {"PTS": 100, "REB": 100, "AST": 50,
                                             "STL": 10, "BLK": 5}, sb.elite_lines(totals, 5)
        assert sb.rank_in(totals, "PTS", 90) == 6, sb.rank_in(totals, "PTS", 90)

        awards = sb.award_counts(d)
        assert awards.get("Our Guy") == {"potw": 1, "potm": 1}, awards
        assert not any(" Tulips" in n for n in awards), f"team swallowed into a name: {awards}"

        # The bracket names every qualifier, including the team that lost the series.
        assert sb.playoff_teams(d) == {"Clams", "Tulips"}, sb.playoff_teams(d)

        # Before the playoffs are played there is NO playoffs.htm - and that is not the same as
        # "nobody qualified". None stops the caller paying the bonus to an empty set, which is
        # what an empty set would silently do at the one moment the bonus is owed.
        nopost = _build(Path(tempfile.mkdtemp(prefix="nopost-")), postseason=False)
        assert sb.playoff_teams(nopost) is None, sb.playoff_teams(nopost)
        assert sb.champion(nopost) is None
        assert not any("playoffs" in r for r, _ in sb.for_character("Our Guy", nopost, {}, {})),             "a league with no bracket paid a playoff bonus"
        shutil.rmtree(nopost, ignore_errors=True)

        # ---- the All-Star game, and the season it belongs to -----------------------------
        # Every honour a player ever won stays printed on his page, so the season filter is the
        # whole correctness of this bonus: without it a man picked once is paid every year for
        # the rest of his career, and the ledger line would look perfectly reasonable.
        stars = _build(Path(tempfile.mkdtemp(prefix="stars-")), honours={
            "Our Guy": ["2026 CVP All-Star", "2027 CVP All-Star Game MVP"],
            "Ace Elite": ["2027 CVP All-Star"],
        })
        found = sb.all_star_seasons(stars)
        assert found.get("Our Guy") == {2026}, f"the Game MVP line was read as a selection: {found}"
        assert found.get("Ace Elite") == {2027}, found

        paid = dict(sb.for_character("Ace Elite", stars, {"current_season": 2027}, {}))
        assert paid.get("season bonus: 2027 All-Star") == 2, paid
        old_star = dict(sb.for_character("Our Guy", stars, {"current_season": 2027}, {}))
        assert not any("All-Star" in r for r in old_star),             f"a 2026 All-Star was paid again in 2027: {old_star}"
        back_then = dict(sb.for_character("Our Guy", stars, {"current_season": 2026}, {}))
        assert back_then.get("season bonus: 2026 All-Star") == 2, back_then
        # and with no season to check against, it pays nothing rather than guessing
        unknown = dict(sb.for_character("Ace Elite", stars, {}, {}))
        assert not any("All-Star" in r for r in unknown),             f"paid an All-Star bonus without knowing the season: {unknown}"
        assert sb.DEFAULTS["bonus_allstar"] == 2, sb.DEFAULTS["bonus_allstar"]
        shutil.rmtree(stars, ignore_errors=True)

        # The two AWARD components are off by default; the two TEAM ones pay 2 apiece. Assert
        # both explicitly: a component silently switching itself back on would pay real points
        # to real people, and "everyone got more than expected" is not a loud failure.
        for key in ("bonus_potw", "bonus_season_award"):
            assert sb.DEFAULTS[key] == 0, f"{key} came back on"
        assert sb.DEFAULTS["bonus_playoffs"] == 2 and sb.DEFAULTS["bonus_title"] == 2, sb.DEFAULTS

        # Our Guy, on the defaults: playoffs 2, POTM 2, top-25 in all five (capped to 2), and the
        # elite line just out of reach everywhere (90/100, 90/100, 45/50, 9/10, 4/5 floor to 0).
        rows = sb.for_character("Our Guy", d, {}, {})
        got = dict((r.split(":")[1].strip(), p) for r, p in rows)
        assert sum(p for _, p in rows) == 6, rows
        assert got["made the playoffs"] == 2 and got["player of the month x1"] == 2, rows
        assert got[f"top {sb.DEFAULTS['stat_bonus_top_n']} in PTS, REB, AST, STL, BLK"] == 2, rows
        assert not any("week" in r for r, _ in rows), rows

        # ...and the off components are off by VALUE, not deleted, so turning them back on in
        # the settings table works without a deploy. That is the whole reason they were zeroed,
        # and the playoff component coming back at 2 is the case that proved it was worth it.
        rows = sb.for_character("Our Guy", d, {"bonus_potw": 1}, {})
        got = dict((r.split(":")[1].strip(), p) for r, p in rows)
        assert got["player of the week x1"] == 1, rows

        # Ace Elite clears every elite line several times over, and the elite half still pays
        # only its maximum of 2 - that ceiling is the whole reason the rule is league-relative
        # rather than a rate per rebound, which paid the best pro 115.
        rows = sb.for_character("Ace Elite", d, {}, {})
        elite = [p for r, p in rows if "elite-line" in r]
        assert elite == [sb.DEFAULTS["stat_bonus_elite_max"]], rows
        assert sum(p for _, p in rows) == 6, rows

        # The overall cap, exercised by lowering it rather than by inventing a season nobody
        # could have: it must trim to exactly the cap AND say so, not pay less in silence.
        rows = sb.for_character("Ace Elite", d, {"bonus_cap": 3}, {})
        assert sum(p for _, p in rows) == 3, rows
        assert any("capped at 3" in r for r, _ in rows), "the cap was applied silently"

        # Benched Kid: 4 of 24 games is under a quarter, so he is a development case.
        rows = sb.for_character("Benched Kid", d, {}, {})
        assert any("development" in r for r, _ in rows), rows

        assert sb.for_character("Nobody At All", d, {}, {}) == [], "paid a player who never played"

        # A finished season fills in the pages that were empty.
        d2 = _build(Path(tempfile.mkdtemp(prefix="bonus2-")), champion="Tulips",
                    season_awards=True)
        # The bracket says Clams beat Tulips. Our Guy is a Tulip, so he made the playoffs and
        # did NOT win the league - and champs.htm names both teams on its one row, so a parser
        # that takes "the first name on the page" hands him the title he lost.
        q, champ = sb.playoff_bracket(d2)
        assert q == {"Clams", "Tulips"}, f"the bracket lost a qualifier: {q}"
        assert champ == "Clams", f"the champion came out as {champ}; Clams won 2-0"
        rows = sb.for_character("Our Guy", d2, {}, {})
        assert not any("won the league" in r for r, _ in rows),             f"the beaten finalist was paid the title: {rows}"
        assert any("made the playoffs" in r for r, _ in rows), rows
        # The season award is off by default, but the page must still be READ correctly, or
        # turning it back on would quietly pay nobody.
        assert "Our Guy" in sb.season_award_winners(d2), sb.season_award_winners(d2)
        rows = sb.for_character("Our Guy", d2, {"bonus_season_award": 3}, {})
        assert any("a season award" in r for r, _ in rows), rows
        shutil.rmtree(d2, ignore_errors=True)

        # Every number is tunable, and junk falls back rather than raising.
        rows = sb.for_character("Our Guy", d, {"bonus_potm": 99, "bonus_cap": 500}, {})
        assert any(p == 99 for _, p in rows), rows
        assert sb.setting({"bonus_cap": "not a number"}, "bonus_cap") == 10

        # ---- what the site is handed -------------------------------------------------------
        # Same parse, same traps already proven above, written down once at publish time so the
        # browser never has to fetch and parse four hundred player pages to say where somebody
        # placed - which would be a second implementation of every trap in this file.
        stats = sb.league_stats(d, elite_rank=5)
        assert stats["count"] == 9 and len(stats["players"]) == 9, stats["count"]
        assert all(p["team"] != "Draft" for p in stats["players"]), "draft pool reached the site"
        assert [p["name"] for p in stats["players"]][0] == "Ace Elite", "not sorted by scoring"
        ours = next(p for p in stats["players"] if p["name"] == "Our Guy")
        assert ours["rank"]["PTS"] == sb.rank_in(totals, "PTS", ours["PTS"]), ours
        # the page stem is the game's own player id, and it is what a link to the league site needs
        assert ours["page"].startswith("player"), ours
        assert stats["elite"] == sb.elite_lines(totals, 5), stats["elite"]
        assert stats["teams"] == teams and stats["export_date"] == sb.export_date(d)

        # seeds are by WIN PERCENTAGE, not wins: teams in a league are not all the same number
        # of games in (prep spans 22 to 27 today), so raw wins would rank a team that has simply
        # played more above a better one. Tulips 24-0 must outrank Clams 17-7.
        assert [t["name"] for t in stats["seeds"]] == ["Tulips", "Clams"], stats["seeds"]
        assert stats["seeds"][0]["seed"] == 1 and stats["seeds"][0]["pct"] == 1.0, stats["seeds"]

        # An exact tie at the cut line was being decided by the ALPHABET - it settled 8th place
        # in the pros between two 20-23 teams, which is a coin toss dressed up as a standing.
        # FBPB3's own playoff page has an opinion, so borrow it and fall back to the name only
        # when it does not.
        tied = [{"name": "Waxheads", "w": 20, "l": 23, "games": 43, "pct": 0.465},
                {"name": "Threshers", "w": 20, "l": 23, "games": 43, "pct": 0.465}]
        assert [t["name"] for t in sb.seeded(tied, top=2)] == ["Threshers", "Waxheads"]
        # SAME conference: take the game's own order, which is what it is there for.
        assert [t["name"] for t in sb.seeded(tied, top=2, order={"Waxheads": 4, "Threshers": 5})]             == ["Waxheads", "Threshers"], "the game's own order was ignored"

        # CROSS conference is the case that made this a dict of positions rather than a flat list
        # down the page. Read flat, a tie went to whichever conference is PRINTED first, which is
        # layout and not basketball - the game never ranks two conferences against each other.
        # Position within your own conference does: 2nd in the South beats 5th in the North.
        cross = [{"name": "Prospectors", "w": 15, "l": 10, "games": 25, "pct": 0.600},
                 {"name": "Potatoes", "w": 15, "l": 10, "games": 25, "pct": 0.600}]
        assert [t["name"] for t in sb.seeded(cross, top=2,
                                             order={"Prospectors": 5, "Potatoes": 2})]             == ["Potatoes", "Prospectors"], "a cross-conference tie went the wrong way"

        # a team the order has never heard of sorts after the ones it knows, not before
        assert [t["name"] for t in sb.seeded(tied, top=2, order={"Threshers": 1})]             == ["Threshers", "Waxheads"]
        # record still beats the order: a better team is not demoted by where he is seeded
        better = tied + [{"name": "Zebras", "w": 30, "l": 13, "games": 43, "pct": 0.698}]
        assert sb.seeded(better, top=1,
                         order={"Waxheads": 1, "Threshers": 2, "Zebras": 8})[0]["name"] == "Zebras"

        # true shooting is computed, not read off the game's own Efficiency row - see the
        # docstring. 5 points on 3 attempts and no free throws is 5 / (2 * 3).
        assert sb.true_shooting(5, 3, 0) == 0.833, sb.true_shooting(5, 3, 0)
        assert sb.true_shooting(10, 5, 5) == round(10 / (2 * (5 + 0.44 * 5)), 3)
        # None, not zero: a bench player with no attempts has no percentage, and 0.0 would sort
        # him below somebody who genuinely missed everything.
        assert sb.true_shooting(0, 0, 0) is None
        assert sb.true_shooting(2, 0, 0) is None
        # it has to survive json.dumps, since that is the only thing ever done with it
        assert json.loads(json.dumps(stats))["count"] == 9
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK  season bonus: undefeated team, repeated column, draft pool, award names, cap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
