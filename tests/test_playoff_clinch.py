"""A team that merely LEADS a series has not won it, and the champion is not crowned until it has.

`playoff_bracket()` decided a series by "whose number is bigger", which is only the same question
once the series is over. Pro's 2028 League Finals sat at `#1 Leghorns 1  #1 Swiss 0` while it was
still being played. That is not a tie, so the undecided guard let it through, Leghorns was
credited with a third series win against Swiss's two, and `champion()` named a team that had won
one game of a best-of-seven. The bonus pays a league title from that name. It cost nothing in
2028 only because pro had no characters in it; all seven are in prep, where the same bracket
shape would have paid the wrong roster.

THE BRACKET NEVER STATES HOW LONG A SERIES IS, and it differs by round and by league: prep plays
best-of-one until a best-of-three final, college best-of-one throughout, pro best-of-five then
best-of-seven. What the bracket does support is that A ROUND IS NEVER SHORTER THAN THE ONE BEFORE
IT, so each round's clinch is the highest total seen in it carried forward monotonically.

The obvious cheaper rule - one clinch for the whole bracket, taken as its maximum - is WRONG, and
`test_short_first_round_still_counts` is here to keep anyone from reaching for it. It marks prep's
best-of-one first round undecided because the final needs two, which strips the champion of the
series he actually won and returns None for a season that finished properly.

All six brackets below are the real pages, read out of the finished-season exports under
`backups/<stamp>-offseason-CV_<league>/finished-season/html/playoffs.htm`. They are embedded
rather than read because `backups/` is gitignored. Tokens are separated with the NUMERIC entity
`&#160;`, exactly as FBPB3 writes them - a fixture with tidy spaces passes against a parser that
cannot read the real page.

    python tests/test_playoff_clinch.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commissioner import seasonbonus  # noqa: E402
from commissioner.universe import config as cfg  # noqa: E402

# The exact answer, and the reason this file passes `rounds` everywhere: series LENGTHS per
# round, the final last. prep (0,1,1,3) -> needs 1, 1, 2; college (0,1,1,1) -> 1, 1, 1;
# pro (0,5,7,7) -> 3, 4, 4. simweek.round_one_days() reads the same field.
ROUNDS = {key: cfg.BY_KEY[key].playoff_rounds for key in ("prep", "college", "pro")}

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


# (seed, team, wins) in the page's own rowspan order. Pair i is round v2(i+1)+1, so the final is
# the fourth of seven pairs: [R1, CF, R1, FINAL, R1, CF, R1].
BRACKETS = {
    ("2027", "prep"): [
        ("1", "Tulips", 1), ("4", "Clams", 0), ("1", "Tulips", 1), ("3", "Spirits", 0),
        ("2", "Derricks", 0), ("3", "Spirits", 1), ("1", "Tulips", 2), ("3", "Dealers", 0),
        ("2", "Sluggers", 0), ("3", "Dealers", 1), ("3", "Dealers", 1), ("1", "Generals", 0),
        ("1", "Generals", 1), ("4", "Rails", 0),
    ],
    ("2027", "college"): [
        ("1", "Potatoes", 0), ("4", "Wahoos", 1), ("2", "Onions", 1), ("4", "Wahoos", 0),
        ("2", "Onions", 1), ("3", "Towers", 0), ("2", "Mandibles", 1), ("2", "Onions", 0),
        ("2", "Mandibles", 1), ("3", "Mermaids", 0), ("4", "Prospectors", 0), ("2", "Mandibles", 1),
        ("1", "Gators", 0), ("4", "Prospectors", 1),
    ],
    ("2027", "pro"): [
        ("1", "Leghorns", 3), ("4", "Alpines", 2), ("1", "Leghorns", 4), ("2", "Crush", 1),
        ("2", "Crush", 3), ("3", "Monks", 0), ("1", "Leghorns", 2), ("2", "Swiss", 4),
        ("2", "Swiss", 3), ("3", "Curds", 0), ("4", "Threshers", 2), ("2", "Swiss", 4),
        ("1", "Wheels", 0), ("4", "Threshers", 3),
    ],
    ("2028", "prep"): [
        ("1", "Tulips", 1), ("4", "Royals", 0), ("1", "Tulips", 1), ("2", "Derricks", 0),
        ("2", "Derricks", 1), ("3", "Spirits", 0), ("1", "Generals", 2), ("1", "Tulips", 1),
        ("2", "Berries", 1), ("3", "Dealers", 0), ("2", "Berries", 0), ("1", "Generals", 1),
        ("1", "Generals", 1), ("4", "Sluggers", 0),
    ],
    ("2028", "college"): [
        ("1", "Onions", 0), ("4", "Towers", 1), ("4", "Towers", 0), ("2", "Hounds", 1),
        ("2", "Hounds", 1), ("3", "Wahoos", 0), ("1", "Mandibles", 1), ("2", "Hounds", 0),
        ("2", "Mermaids", 1), ("3", "Catfish", 0), ("2", "Mermaids", 0), ("1", "Mandibles", 1),
        ("1", "Mandibles", 1), ("4", "Gators", 0),
    ],
    # The bug. League Finals at Leghorns 1, Swiss 0 - one game of a best-of-seven, while the
    # rounds before it clinched at three and four.
    ("2028", "pro"): [
        ("1", "Leghorns", 3), ("4", "Veins", 1), ("1", "Leghorns", 4), ("2", "Crush", 2),
        ("2", "Crush", 3), ("3", "Monks", 0), ("1", "Leghorns", 1), ("1", "Swiss", 0),
        ("2", "Threshers", 0), ("3", "Wheels", 3), ("3", "Wheels", 0), ("1", "Swiss", 4),
        ("1", "Swiss", 3), ("4", "Waxheads", 2),
    ],
}

CHAMPION = {
    ("2027", "prep"): "Tulips",
    ("2027", "college"): "Mandibles",
    ("2027", "pro"): "Swiss",
    ("2028", "prep"): "Generals",
    ("2028", "college"): "Mandibles",
    ("2028", "pro"): None,          # still being played - nobody has won it
}


def write_bracket(root, season, entries):
    """The page as FBPB3 writes it: every token separated by the numeric entity &#160;."""
    cells = "".join(
        f"<td class='main'>&#160;#{s}&#160;{t}&#160;{w}&#160;</td>" for s, t, w in entries)
    html = (f"<html><head><title>Playoffs</title><style>td.main{{font-size:10pt;}}</style></head>"
            f"<body><table><tr><td class='plainheader'>{season} Playoff Brackets</td></tr>"
            f"<tr><td>1st Round</td><td>Conference Finals</td><td>League Finals</td></tr>"
            f"<tr>{cells}</tr></table>"
            f"<p>Fast Break Pro Basketball 3 | Grey Dog Software</p></body></html>")
    d = Path(root) / f"{season}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "playoffs.htm").write_text(html, encoding="latin-1")
    return d


def test_every_real_bracket(root):
    """The six finished-season pages we actually have, and who really won each."""
    print("the six real brackets")
    for (season, league), entries in BRACKETS.items():
        d = write_bracket(Path(root) / league, season, entries)
        check(f"{season} {league} champion",
              seasonbonus.champion(str(d), rounds=ROUNDS[league]), CHAMPION[(season, league)])


def test_qualifiers_survive_an_undecided_final(root):
    """An unfinished final must not cost us the qualifier list - the playoff bonus reads it."""
    print("qualifiers are still readable while the final is being played")
    d = write_bracket(Path(root) / "qual", "2028", BRACKETS[("2028", "pro")])
    teams = seasonbonus.playoff_teams(str(d))
    check("2028 pro qualifiers", len(teams or []), 8)
    check("Swiss is a qualifier", "Swiss" in (teams or set()), True)


def test_short_first_round_still_counts(root):
    """One clinch for the whole bracket is the wrong rule. This is the case that proves it.

    Prep's first round is best-of-one and its final best-of-three. A single bracket-wide clinch of
    two marks every 1-0 first-round series undecided, Generals loses the two series he really won,
    and a properly finished season returns None.
    """
    print("a best-of-one round is not undecided just because the final is longer")
    d = write_bracket(Path(root) / "short", "2028", BRACKETS[("2028", "prep")])
    check("2028 prep champion", seasonbonus.champion(str(d)), "Generals")
    check("2028 prep qualifiers", len(seasonbonus.playoff_teams(str(d)) or []), 8)


def test_nothing_played_yet(root):
    """Before a ball is bounced every pair is 0-0: no champion, but the qualifiers are known."""
    print("a bracket that has not started")
    zeroed = [(s, t, 0) for s, t, _w in BRACKETS[("2028", "pro")]]
    d = write_bracket(Path(root) / "zero", "2028", zeroed)
    check("champion before any game", seasonbonus.champion(str(d)), None)
    check("qualifiers before any game", len(seasonbonus.playoff_teams(str(d)) or []), 8)


def test_a_final_still_being_played(root):
    """The hole the inferred clinch could not see, and the reason `rounds` is not optional.

    Rewind each league's real final to 1-0 and ask again. Whether that is a win depends ENTIRELY
    on the league: college plays a best-of-one final, so 1-0 IS the title; prep needs two and pro
    needs four, so 1-0 is a team ahead in a series nobody has won yet.

    No amount of reading the bracket page can tell those apart - at prep's 1-0 final all seven
    series look decided and the leader holds the structurally correct three series wins. Inferring
    the clinch from the page happens to catch pro, whose earlier rounds clinch at 3 and 4, and
    MISSES PREP, where every earlier round tops out at 1. Prep is the league our characters are
    in, so that miss is the one that would have paid a title bonus to the wrong roster.
    """
    print("a final that is still being played")
    want = {"prep": None, "pro": None, "college": "Mandibles"}
    for league, expected in want.items():
        entries = list(BRACKETS[("2028", league)])
        (sa, ta, _wa), (sb, tb, _wb) = entries[6], entries[7]
        entries[6], entries[7] = (sa, ta, 1), (sb, tb, 0)
        d = write_bracket(Path(root) / f"inplay-{league}", "2028", entries)
        check(f"{league} final at 1-0", seasonbonus.champion(str(d), rounds=ROUNDS[league]), expected)


def main():
    with tempfile.TemporaryDirectory() as root:
        test_every_real_bracket(root)
        test_a_final_still_being_played(root)
        test_qualifiers_survive_an_undecided_final(root)
        test_short_first_round_still_counts(root)
        test_nothing_played_yet(root)
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  playoff clinch: a series counts only once its leader reaches the wins its round "
          "actually needs, taken from LeagueSpec.playoff_rounds; a 1-0 final crowns nobody in "
          "prep or pro and crowns the winner in best-of-one college, the six real brackets are "
          "unchanged, and the qualifier list survives an undecided series")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
