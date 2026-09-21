from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import tempfile

from commissioner.stories import (
    _award_and_trade_events, _disaster, _mvp_watch, _rivalries, build_story_feed,
)


def player(pid, name, team, games):
    return {"id": pid, "name": name, "team": team, "games": games}


def test_disaster_rejects_productive_game_even_with_turnovers():
    p = player("a", "Good Night", "A", [{
        "season": 2028, "day": 4, "pts": 17, "fgm": 7, "fga": 14,
        "to": 4, "pm": 5, "opp": "B", "home": False, "won": False,
    }])
    assert _disaster(p, "prep") is None


def test_disaster_accepts_comically_bad_shooting_line():
    p = player("a", "Brick Layer", "A", [{
        "season": 2028, "day": 4, "pts": 2, "fgm": 1, "fga": 11,
        "to": 2, "pm": -12, "opp": "B", "home": False, "won": False,
    }])
    event = _disaster(p, "prep")
    assert event["type"] == "disaster"
    assert "1-for-11" in event["detail"]


def test_rivalry_requires_same_season_day_and_opposing_teams():
    one = player("a", "One Player", "A", [
        {"season": 2028, "day": 9, "team": "A", "opp": "B", "won": True},
        {"season": 2027, "day": 9, "team": "A", "opp": "B", "won": True},
    ])
    two = player("b", "Two Player", "B", [
        {"season": 2028, "day": 9, "team": "B", "opp": "A", "won": False},
        {"season": 2027, "day": 10, "team": "B", "opp": "A", "won": False},
    ])
    events = _rivalries([one, two], "prep")
    assert len(events) == 1
    assert "1 time; One Player leads 1-0" in events[0]["detail"]


def test_mvp_watch_is_labeled_unofficial_and_ranks_whole_field():
    stats = {"season": "Season 2028", "players": [
        {"name": "Our Player", "page": "player7", "G": 10, "PTS": 100, "REB": 20, "AST": 10,
         "STL": 0, "BLK": 0},
        {"name": "Other Player", "page": "player8", "G": 10, "PTS": 120, "REB": 20, "AST": 10,
         "STL": 0, "BLK": 0},
    ]}
    chars = [{"id": "a", "first_name": "Our", "last_name": "Player",
              "league_player_ids": {"prep": 7}}]
    event = _mvp_watch(stats, chars, "prep")[0]
    assert event["rank"] == 2
    assert event["field"] == 2
    assert "Unofficial" in event["detail"]


def test_mvp_watch_uses_player_id_when_names_collide():
    stats = {"season": "Season 2028", "players": [
        {"name": "Same Name", "page": "player7", "G": 10, "PTS": 50},
        {"name": "Same Name", "page": "player8", "G": 10, "PTS": 150},
    ]}
    chars = [{"id": "ours", "first_name": "Same", "last_name": "Name",
              "league_player_ids": {"prep": 7}}]
    assert _mvp_watch(stats, chars, "prep")[0]["rank"] == 2


def _player_page(name, pid, honour=""):
    return f"<body>{name}&nbsp;#{pid} SG | 6-0, 170lbs | Team | active&nbsp;{honour}</body>"


def test_awards_keep_their_real_season_instead_of_following_current_season():
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp)
        (html / "players").mkdir()
        (html / "players" / "player7.htm").write_text(
            _player_page("Our Player", 7, "2027 CVP All-Star"), encoding="latin-1")
        (html / "standings.htm").write_text("Page created: March 30, 2027", encoding="latin-1")
        (html / "awards.htm").write_text(
            "Player of the Week 03/21/2027 SG Our Player Team 9.0 10.0 1.0 1.5 0.5",
            encoding="latin-1")
        (html / "seasonawards.htm").write_text("", encoding="latin-1")
        chars = [{"id": "a", "first_name": "Our", "last_name": "Player",
                  "league_player_ids": {"prep": 7}}]
        events = _award_and_trade_events(html, "prep", chars, season=2028)
        star = next(e for e in events if "All-Star" in e["title"])
        weekly = next(e for e in events if "weekly" in e["title"])
        assert star["season"] == 2027
        assert weekly["season"] == 2028


def test_duplicate_character_names_both_remain_in_player_index():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        site, docs = root / "site", root / "docs"
        site.mkdir()
        chars = [
            {"id": "one", "first_name": "Same", "last_name": "Name"},
            {"id": "two", "first_name": "Same", "last_name": "Name"},
        ]
        feed = build_story_feed(site, docs, chars, 2028)
        assert set(feed["players"]) == {"one", "two"}


def test_finished_season_trade_is_read_from_rollover_archive():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        site, docs = root / "site", root / "docs"
        archived = root / "backups" / "stamp-offseason-CV_Prep" / "finished-season" / "html"
        (archived / "players").mkdir(parents=True)
        (archived / "players" / "player7.htm").write_text(
            _player_page("Our Player", 7), encoding="latin-1")
        (archived / "standings.htm").write_text("Page created: May 1, 2027", encoding="latin-1")
        (archived / "awards.htm").write_text("", encoding="latin-1")
        (archived / "seasonawards.htm").write_text("", encoding="latin-1")
        (archived / "transactions.htm").write_text(
            "<table><tr><td>11/8/2027</td><td>Team</td>"
            "<td>Traded Our Player to Rivals</td></tr></table>", encoding="latin-1")
        chars = [{"id": "a", "first_name": "Our", "last_name": "Player",
                  "league_player_ids": {"prep": 7}}]
        feed = build_story_feed(site, docs, chars, 2028)
        trades = [e for e in feed["events"] if e["type"] == "trade"]
        assert len(trades) == 1
        assert trades[0]["season"] == 2027


def test_prior_trade_survives_without_the_old_export_or_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        site, docs = root / "site", root / "docs"
        (site / "data").mkdir(parents=True)
        prior = {"events": [{
            "id": "old-trade", "type": "trade", "title": "Trade wire",
            "detail": "Traded Our Player", "character_ids": ["a"], "league": "prep",
            "season": 2027, "day": None, "tone": "neutral",
        }]}
        (site / "data" / "stories.json").write_text(json.dumps(prior), encoding="utf-8")
        chars = [{"id": "a", "first_name": "Our", "last_name": "Player",
                  "league_player_ids": {"prep": 7}}]
        feed = build_story_feed(site, docs, chars, 2028)
        assert any(e["id"] == "old-trade" for e in feed["events"])


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"ok - {len(tests)} story feed tests")
