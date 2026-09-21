from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.stories import _disaster, _mvp_watch, _rivalries


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
        {"name": "Our Player", "G": 10, "PTS": 100, "REB": 20, "AST": 10,
         "STL": 0, "BLK": 0},
        {"name": "Other Player", "G": 10, "PTS": 120, "REB": 20, "AST": 10,
         "STL": 0, "BLK": 0},
    ]}
    chars = [{"id": "a", "first_name": "Our", "last_name": "Player"}]
    event = _mvp_watch(stats, chars, "prep")[0]
    assert event["rank"] == 2
    assert event["field"] == 2
    assert "Unofficial" in event["detail"]


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"ok - {len(tests)} story feed tests")
