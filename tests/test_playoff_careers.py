"""The postseason is its own archive, and an all-time page can ask for it instead.

FBPB3 keeps the playoffs in a SEPARATE TABLE. `SeasonStats` is the regular season on its own -
checked against the live prep MDB, where for 2028 id 41 reads 30 games in `SeasonStats` and 2 in
`PlayoffStats`, and the two never overlap. So a playoff career cannot be a filter over the
regular-season archive; it is a second archive, written from a second table, and that is what
these tests pin.

THE ASSERTION THIS FILE EXISTS FOR is `test_the_two_archives_never_mix`. Both archives live in
the same folder and differ only by filename prefix, so a careless glob is all it takes for
playoff lines to be summed into regular-season careers - which would not crash, would not look
wrong, and would quietly inflate every all-time total on the site.

`test_a_missing_playoff_table_costs_nothing` matters for a different reason: `capture()` runs
inside every publish. An MDB with no `PlayoffStats` - an older export, or a schema that moves -
must lose the postseason and nothing else, because the alternative is a publish that stops.

The MDB is faked here rather than read. The real one needs the game, a PowerShell bridge and an
Access driver, and none of that is available to a test that has to run on the desktop too.

    python tests/test_playoff_careers.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commissioner import statsarchive  # noqa: E402

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


PLAYERS = [
    {"ID": 1, "Name": "Ada Vance", "BirthMonth": 3, "BirthDay": 4, "BirthYear": 2008,
     "Age": 20, "CurrentTeam": "Tulips", "PositionNumber": 2},
    {"ID": 2, "Name": "Bo Reyes", "BirthMonth": 7, "BirthDay": 9, "BirthYear": 2009,
     "Age": 19, "CurrentTeam": "Generals", "PositionNumber": 4},
]
# Ada plays both seasons and both rounds; Bo never reaches a playoff, which is the ordinary case
# and the one that must not appear on a postseason board at all.
SEASON_ROWS = [
    {"ID": 1, "Season": 2027, "Team": "Tulips", "Games": 30, "GamesStarted": 30,
     "Minutes": 900, "Points": 600, "Rebounds": 300, "Assists": 150, "FGM": 240, "FGA": 500},
    {"ID": 2, "Season": 2027, "Team": "Generals", "Games": 28, "GamesStarted": 10,
     "Minutes": 500, "Points": 200, "Rebounds": 150, "Assists": 40, "FGM": 80, "FGA": 200},
    {"ID": 1, "Season": 2028, "Team": "Tulips", "Games": 30, "GamesStarted": 30,
     "Minutes": 950, "Points": 700, "Rebounds": 320, "Assists": 160, "FGM": 280, "FGA": 560},
]
PLAYOFF_ROWS = [
    # No GamesStarted column at all - PlayoffStats does not have one. It must read 0, not crash.
    {"ID": 1, "Season": 2027, "Team": "Tulips", "Games": 3,
     "Minutes": 100, "Points": 60, "Rebounds": 30, "Assists": 15, "FGM": 24, "FGA": 50},
    {"ID": 1, "Season": 2028, "Team": "Tulips", "Games": 2,
     "Minutes": 70, "Points": 40, "Rebounds": 20, "Assists": 10, "FGM": 16, "FGA": 34},
]


def fake_query(missing_playoffs=False):
    def query(_mdb, sql, script=None):
        if "FROM Player" in sql:
            return list(PLAYERS)
        if "SeasonStats" in sql:
            return list(SEASON_ROWS)
        if "PlayoffStats" in sql:
            if missing_playoffs:
                raise RuntimeError("the Microsoft Jet database engine cannot find 'PlayoffStats'")
            return list(PLAYOFF_ROWS)
        raise AssertionError(f"unexpected sql: {sql}")
    return query


def capture_into(root, missing_playoffs=False, **kw):
    Path(root).mkdir(parents=True, exist_ok=True)
    mdb = Path(root) / "LeagueOutput.mdb"
    mdb.write_bytes(b"not really a database")
    with patch.object(statsarchive, "ARCHIVE", Path(root) / "history"), \
         patch("commissioner.headtohead.query", fake_query(missing_playoffs)):
        statsarchive.capture("prep", mdb, log=lambda m: None, **kw)
        return {
            "stats": statsarchive.careers("prep"),
            "playoffs": statsarchive.careers("prep", kind="playoffs"),
            "stat_seasons": sorted(s for s, _ in statsarchive.archived_seasons("prep")),
            "playoff_seasons": sorted(s for s, _ in statsarchive.archived_seasons("prep", "playoffs")),
            "files": sorted(p.name for p in (Path(root) / "history").iterdir()),
        }


def test_the_two_archives_never_mix(root):
    """Same folder, different prefix. A glob that catches both inflates every all-time total."""
    print("the two archives are kept apart")
    out = capture_into(root)
    check("files written", out["files"], [
        "playoffs-prep-2027.json", "playoffs-prep-2028.json",
        "stats-prep-2027.json", "stats-prep-2028.json"])
    check("regular seasons", out["stat_seasons"], [2027, 2028])
    check("playoff seasons", out["playoff_seasons"], [2027, 2028])

    ada_reg = next(r for r in out["stats"] if r["name"] == "Ada Vance")
    ada_po = next(r for r in out["playoffs"] if r["name"] == "Ada Vance")
    # 30 + 30 regular, and NOT 30 + 30 + 3 + 2.
    check("Ada regular games", ada_reg["Games"], 60)
    check("Ada regular points", ada_reg["Points"], 1300)
    check("Ada playoff games", ada_po["Games"], 5)
    check("Ada playoff points", ada_po["Points"], 100)
    # Bo never played a playoff game, so he is absent from that board rather than present at zero.
    check("playoff careers", sorted(r["name"] for r in out["playoffs"]), ["Ada Vance"])
    check("regular careers", sorted(r["name"] for r in out["stats"]), ["Ada Vance", "Bo Reyes"])


def test_rates_are_computed_off_the_postseason_alone(root):
    """A playoff PPG divided by regular-season games is the kind of wrong that looks right."""
    print("playoff rates use playoff games")
    out = capture_into(root)
    ada = next(r for r in out["playoffs"] if r["name"] == "Ada Vance")
    check("playoff ppg", ada["ppg"], 20.0)            # 100 points / 5 games, not / 65
    check("playoff rpg", ada["rpg"], 10.0)            # 50 / 5
    check("GamesStarted absent reads 0", ada["GamesStarted"], 0)
    check("fg_pct off playoff shots", ada["fg_pct"], round(100 * 40 / 84, 1))


def test_a_missing_playoff_table_costs_nothing(root):
    """capture() runs inside every publish. No PlayoffStats must not stop the regular season."""
    print("an MDB with no PlayoffStats")
    out = capture_into(root, missing_playoffs=True)
    check("regular season still archived", out["stat_seasons"], [2027, 2028])
    check("no playoff files", [f for f in out["files"] if f.startswith("playoffs-")], [])
    check("regular careers intact", len(out["stats"]), 2)
    check("playoff careers empty", out["playoffs"], [])


def test_a_broken_regular_season_read_is_never_swallowed(root):
    """The asymmetry in capture()'s guard, which is easy to widen by accident.

    publish calls capture with `log=lambda m: None`. If a SeasonStats failure were skipped the
    way a missing PlayoffStats is, capture would return [], careers() would read the archive it
    already had, and careers.json would be rewritten with a fresh `generated` and stale
    contents - a broken Access driver looking like a clean publish, forever. It must raise into
    _write_careers' own handler instead, which prints and leaves the published file alone.
    """
    print("a broken regular-season read")
    from commissioner.publish import publish as pub
    Path(root).mkdir(parents=True, exist_ok=True)
    history = Path(root) / "history"
    mdb = Path(root) / "LeagueOutput.mdb"
    mdb.write_bytes(b"not really a database")

    def broken(_mdb, sql, script=None):
        if "FROM Player" in sql:
            return list(PLAYERS)
        raise RuntimeError("the Access driver is not installed")

    raised = None
    with patch.object(statsarchive, "ARCHIVE", history),          patch("commissioner.headtohead.query", broken):
        try:
            statsarchive.capture("prep", mdb, log=lambda m: None)
        except Exception as exc:                                    # noqa: BLE001
            raised = exc
    check("capture raises rather than returning quietly", raised is not None, True)

    # And the published file is left as it was, rather than restamped over a stale archive.
    dst = Path(root) / "out"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "careers.json").write_text('{"generated":"yesterday"}', encoding="utf-8")

    class Store:
        def get_settings(self):
            return {"current_season": 2028}

    with patch.object(statsarchive, "ARCHIVE", history),          patch("commissioner.headtohead.query", broken),          patch("commissioner.simweek.store", lambda: Store()):
        out = pub._write_careers(mdb, dst, "prep")
    check("_write_careers reports nothing written", out, None)
    check("the published careers.json is untouched",
          json.loads((dst / "careers.json").read_text(encoding="utf-8")), {"generated": "yesterday"})


def test_a_finished_postseason_is_never_rewritten(root):
    """Same rule the regular season runs under, and it matters more here.

    A playoff archive for the season being played goes from absent, to a first round, to a full
    bracket. Only the last is true, so the CURRENT season is refreshed and finished ones are not.
    """
    print("finished postseasons are left alone")
    history = Path(root) / "history"
    capture_into(root, overwrite_current=2028)
    before = (history / "playoffs-prep-2027.json").read_text(encoding="utf-8")
    mdb = Path(root) / "LeagueOutput.mdb"
    bumped = [dict(r, Points=int(r["Points"]) + 999) for r in PLAYOFF_ROWS]
    def query(_mdb, sql, script=None):
        if "FROM Player" in sql:
            return list(PLAYERS)
        if "SeasonStats" in sql:
            return list(SEASON_ROWS)
        return list(bumped)
    with patch.object(statsarchive, "ARCHIVE", history), \
         patch("commissioner.headtohead.query", query):
        statsarchive.capture("prep", mdb, log=lambda m: None, overwrite_current=2028)
        after_2027 = json.loads((history / "playoffs-prep-2027.json").read_text(encoding="utf-8"))
        after_2028 = json.loads((history / "playoffs-prep-2028.json").read_text(encoding="utf-8"))
    check("2027 untouched", (history / "playoffs-prep-2027.json").read_text(encoding="utf-8"), before)
    check("2027 points unchanged", after_2027["players"][0]["Points"], 60)
    check("2028 refreshed", after_2028["players"][0]["Points"], 1039)


def test_the_published_payload_offers_the_choice(root):
    """careers.json carries the postseason as its own block, or not at all."""
    print("the published payload")
    from commissioner.publish import publish as pub
    Path(root).mkdir(parents=True, exist_ok=True)
    history = Path(root) / "history"
    mdb = Path(root) / "LeagueOutput.mdb"
    mdb.write_bytes(b"not really a database")
    dst = Path(root) / "out"
    dst.mkdir(parents=True, exist_ok=True)

    class Store:
        def get_settings(self):
            return {"current_season": 2028}

    with patch.object(statsarchive, "ARCHIVE", history), \
         patch("commissioner.headtohead.query", fake_query()), \
         patch("commissioner.simweek.store", lambda: Store()):
        pub._write_careers(mdb, dst, "prep")
    payload = json.loads((dst / "careers.json").read_text(encoding="utf-8"))
    check("has a playoffs block", "playoffs" in payload, True)
    check("playoff seasons", payload["playoffs"]["seasons"], [2027, 2028])
    check("playoff boards match the regular ones", sorted(payload["playoffs"]["leaders"]),
          sorted(payload["leaders"]))
    check("playoff points leader", payload["playoffs"]["leaders"]["Points"][0]["name"], "Ada Vance")
    check("playoff leader value is postseason only",
          payload["playoffs"]["leaders"]["Points"][0]["value"], 100)
    check("regular block untouched by the addition",
          payload["leaders"]["Points"][0]["value"], 1300)

    # And a league that has never played a postseason gets no block, rather than an empty one a
    # page would have to special-case.
    dst2 = Path(root) / "out2"
    dst2.mkdir(parents=True, exist_ok=True)
    with patch.object(statsarchive, "ARCHIVE", Path(root) / "history2"), \
         patch("commissioner.headtohead.query", fake_query(missing_playoffs=True)), \
         patch("commissioner.simweek.store", lambda: Store()):
        pub._write_careers(mdb, dst2, "prep")
    bare = json.loads((dst2 / "careers.json").read_text(encoding="utf-8"))
    check("no block when no postseason", "playoffs" in bare, False)
    check("careers still published", len(bare["careers"]), 2)


def test_the_player_join_is_read_once(root):
    """Every query() spawns a PowerShell + ADODB process, and this runs per league per publish."""
    print("the Player join")
    Path(root).mkdir(parents=True, exist_ok=True)
    mdb = Path(root) / "LeagueOutput.mdb"
    mdb.write_bytes(b"not really a database")
    calls = []
    base = fake_query()

    def counting(mdbp, sql, script=None):
        calls.append(sql)
        return base(mdbp, sql, script)

    with patch.object(statsarchive, "ARCHIVE", Path(root) / "history"),          patch("commissioner.headtohead.query", counting):
        statsarchive.capture("prep", mdb, log=lambda m: None)
    check("Player read once for both tables",
          sum("FROM Player" in sql for sql in calls), 1)
    check("both stat tables still read",
          sorted(t for t in ("SeasonStats", "PlayoffStats")
                 if any(t in sql for sql in calls)), ["PlayoffStats", "SeasonStats"])


def test_the_rollover_freezes_the_postseason_too(root):
    """A playoff file is only written while its season is current; after that save() refuses.

    So a Sim Week that sims and saves the finals but fails to publish would leave the postseason
    archive frozen pre-finals, or absent, permanently - the all-time board quietly losing a
    finals. archive_finished is the last moment that can be repaired, so it freezes both.
    """
    print("the rollover freezes both archives")
    from commissioner import seasonflow
    Path(root).mkdir(parents=True, exist_ok=True)
    saved = []
    src = Path(root) / "save"
    (src / "html").mkdir(parents=True, exist_ok=True)
    (src / "html" / "schedule.htm").write_text("<html></html>", encoding="latin-1")
    (src / "LeagueOutput.mdb").write_bytes(b"not really a database")
    backups = {}
    for spec_key in ("prep", "college", "pro"):
        d = Path(root) / "backups" / spec_key
        d.mkdir(parents=True, exist_ok=True)
        backups[spec_key] = d / "league.dat"

    class Store:
        def characters(self, league=None):
            return []

        def runs(self, limit=None):
            return []

    def read(mdbp, kind="stats", people=None):
        return {2028: [{"id": "1", "name": "Ada Vance", "Games": 30 if kind == "stats" else 3}]}

    with patch.object(statsarchive, "read_mdb", read),          patch.object(statsarchive, "save",
                      lambda key, season, rows, source="", overwrite=False, kind="stats":
                      saved.append((key, season, kind)) or Path("x")),          patch("commissioner.takeaways.archive_overview", lambda *a, **k: None),          patch("commissioner.headtohead.from_mdb", lambda *a, **k: {"characters": []}),          patch("commissioner.gamesarchive.archived_seasons", lambda key: []),          patch("commissioner.gamesarchive.save", lambda *a, **k: None),          patch.object(seasonflow.ch, "save_path", lambda key: src / "league.dat"):
        seasonflow.archive_finished(Store(), 2028, backups, log=lambda m: None)
    kinds = sorted({k for _key, _season, k in saved})
    check("both archives frozen", kinds, ["playoffs", "stats"])
    check("one of each per league", len(saved), 6)


def test_the_page_cannot_strand_itself_on_an_empty_view():
    """Source check, because the alternative needs a DOM, Supabase and the whole module graph.

    Two properties, both of which produced a blank page in the writing of this: every reader goes
    through view() rather than touching data.playoffs, and tabbing to a league with no postseason
    falls back instead of leaving the selector pointed at nothing.
    """
    print("the page's fallback")
    src = (Path(__file__).resolve().parents[1] / "site" / "js" / "page-goats.js").read_text(
        encoding="utf-8")
    body = src.split("function view(", 1)[1]
    after_view = body.split("\n}", 1)[1]           # everything past view()'s own body
    check("no reader touches data.playoffs directly",
          "data.playoffs.careers" in after_view or "data.playoffs.leaders" in after_view, False)
    check("falls back off an absent postseason",
          "MODE = 'regular'" in src and "!(data && data.playoffs)" in src, True)
    check("the selector exists", "'Playoffs'" in src and "'Regular season'" in src, True)


def main():
    with tempfile.TemporaryDirectory() as root:
        test_the_two_archives_never_mix(Path(root) / "a")
        test_rates_are_computed_off_the_postseason_alone(Path(root) / "b")
        test_a_missing_playoff_table_costs_nothing(Path(root) / "c")
        test_a_broken_regular_season_read_is_never_swallowed(Path(root) / "f")
        test_a_finished_postseason_is_never_rewritten(Path(root) / "d")
        test_the_published_payload_offers_the_choice(Path(root) / "e")
        test_the_player_join_is_read_once(Path(root) / "g")
        test_the_rollover_freezes_the_postseason_too(Path(root) / "h")
        test_the_page_cannot_strand_itself_on_an_empty_view()
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  playoff careers: the postseason is archived from its own table, kept apart from "
          "the regular season in the same folder, never rewritten once finished, survives an MDB "
          "that has no PlayoffStats, and reaches the page as its own block or not at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
