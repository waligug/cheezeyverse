"""A publish must not delete the JSON files the site reads.

`restyle(clean=True)` does an rmtree of `site/leagues/<key>` and rebuilds it from the game's
export. Anything written into that folder BEFOREHAND is destroyed - and that is not theoretical:
run_sim wrote games.json before calling publish(), a publish then removed it from the live site,
and the comment on that call said the opposite of what happened. stats.json survived only by
accident of being written after restyle rather than before.

So the rule is a property of the folder, not of one caller: anything the site needs must be
written INSIDE publish_league, after restyle. This test holds that shut by doing what restyle
does - deleting the folder - and requiring both files to come back.

It deliberately does not need FBPB3, a save or an MDB: the writers are stubbed, and what is
under test is the ORDER, which is the thing that was wrong.

    python tests/test_publish_keeps_json.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.publish import publish as pub  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp(prefix="publish-"))
    real = {"SITE": pub.SITE, "restyle": pub.restyle, "stats": pub._write_stats,
            "games": pub._write_games, "careers": pub._write_careers,
            "ours": pub._our_players, "docs": pub.DOCS}
    try:
        src = tmp / "export"
        (src / "players").mkdir(parents=True)
        (src / "index.htm").write_text("<html></html>", encoding="latin-1")
        pub.SITE = tmp / "site"
        pub.DOCS = tmp / "docs"
        (pub.DOCS / "leaguedata" / "CV_Prep").mkdir(parents=True)
        shutil.copytree(src, pub.DOCS / "leaguedata" / "CV_Prep" / "html")
        pub._our_players = lambda key: set()

        order = []

        def fake_restyle(s, d, **kw):
            # exactly what the real one does to the folder, which is the whole point
            if Path(d).exists():
                shutil.rmtree(d)
            Path(d).mkdir(parents=True)
            (Path(d) / "index.htm").write_text("skinned", encoding="utf-8")
            order.append("restyle")
            return 1

        def fake_stats(s, d, key):
            (Path(d) / "stats.json").write_text('{"count": 1}', encoding="utf-8")
            order.append("stats")
            return {"players": 1}

        def fake_games(s, d, key):
            (Path(d) / "games.json").write_text('{"characters": []}', encoding="utf-8")
            order.append("games")
            return {"characters": 1, "games": 2}

        def fake_careers(s, d, key):
            (Path(d) / "careers.json").write_text('{"careers": []}', encoding="utf-8")
            order.append("careers")
            return {"careers": 1}

        pub.restyle, pub._write_stats = fake_restyle, fake_stats
        pub._write_games, pub._write_careers = fake_games, fake_careers

        row = pub.publish_league("prep")
        dst = pub.SITE / "leagues" / "prep"

        assert (dst / "stats.json").exists(), "restyle's rmtree ate stats.json"
        assert (dst / "games.json").exists(), \
            "restyle's rmtree ate games.json - it must be written AFTER restyle, not before"
        assert order == ["restyle", "stats", "games", "careers"], f"wrong order: {order}"
        assert row["games"] == {"characters": 1, "games": 2}, row

        # and the failure that started this: written before, it does not survive
        (dst / "written-first.json").write_text("{}", encoding="utf-8")
        pub.publish_league("prep")
        assert not (dst / "written-first.json").exists(), \
            "the test's own premise is wrong - restyle did not clean the folder"

        # a league with no MDB simply has no file, and that must not fail the publish
        pub._write_games = lambda *_a, **_k: None
        row = pub.publish_league("prep")
        assert row["games"] is None and (dst / "stats.json").exists(), row

        # A deliberately deferred MDB keeps the last known game/career payloads through the
        # clean rebuild and never invokes either stale-MDB writer.
        (dst / "games.json").write_text('{"old":"games"}', encoding="utf-8")
        (dst / "careers.json").write_text('{"old":"careers"}', encoding="utf-8")
        pub._write_games = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("stale games"))
        pub._write_careers = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("stale careers"))
        row = pub.publish_league("prep", mdb_fresh=False)
        assert (dst / "games.json").read_text(encoding="utf-8") == '{"old":"games"}'
        assert (dst / "careers.json").read_text(encoding="utf-8") == '{"old":"careers"}'
        assert row["games"]["deferred"] is True, row
    finally:
        pub.SITE, pub.DOCS = real["SITE"], real["docs"]
        pub.restyle, pub._write_stats = real["restyle"], real["stats"]
        pub._write_games, pub._write_careers = real["games"], real["careers"]
        pub._our_players = real["ours"]
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK  publish: stats.json and games.json both survive restyle's rmtree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
