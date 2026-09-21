"""Created players enter at fourteen and archived seasons keep their historical age."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commissioner import simweek, statsarchive  # noqa: E402


assert simweek._arrival_dob("1/15/2011", 2026) == "1/15/2012"
assert simweek._arrival_dob("2010-11-27", 2028) == "11/27/2014"

players = [{"ID": 7, "Name": "Test Player", "BirthMonth": 1, "BirthDay": 15,
            "BirthYear": 2012, "Age": 18, "CurrentTeam": "WET", "PositionNumber": 5}]
seasons = [
    {"ID": 7, "Season": 2026, "Team": "WET", "Games": 10},
    {"ID": 7, "Season": 2027, "Team": "WET", "Games": 11},
    {"ID": 7, "Season": 2028, "Team": "WET", "Games": 12},
]


def query(_mdb, sql):
    return players if "FROM Player" in sql else seasons


with patch("commissioner.headtohead.query", query):
    rows = statsarchive.read_mdb("unused")

assert [rows[year][0]["age"] for year in (2026, 2027, 2028)] == [14, 15, 16]
assert all(rows[year][0]["dob"] == "1/15/2012" for year in rows)

print("OK  character ages: arrivals start at 14 and archived ages stay with their season")
