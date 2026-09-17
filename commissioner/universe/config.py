"""Cheezeyverse definition: three leagues, three saves, one shared team table per league.

The league CSV and the player CSV are generated from the same `teams` list so a filler's
Team abbreviation always matches a real team. FBPB3 silently dumps a player into free
agency when it cannot match the abbreviation, team name or nickname.

Roster maths (FBPB3 hard limit is 15 dressed players per team):
    filler_per_team + reserve_per_team <= 15
`reserve_per_team` is the number of dormant slots parked on each roster. Creating a
character claims one: the codec renames it, re-rates it and re-dates it in place, so the
file layout never changes. reserve_per_team * len(teams) is the hard ceiling on the number
of concurrent characters in that league.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

START_YEAR = 2030  # season the universe starts in; DOBs are derived from it
ROSTER_LIMIT = 15


@dataclass(frozen=True)
class Team:
    city: str
    nickname: str
    abbrev: str
    division: int  # 1-based index into LeagueSpec.divisions
    color: str
    hometown: str  # arena city
    state: str
    arena: str
    capacity: int


@dataclass(frozen=True)
class LeagueSpec:
    key: str
    name: str
    abbrev: str
    save_name: str
    prestige: int  # 1 = highest (the stock NBA league file ships with 1)
    conferences: tuple
    divisions: tuple
    schedule_games: int
    playoff_teams: int
    playoff_rounds: tuple  # 4 entries, last = the final; leading 0s mean the bracket has fewer rounds
    quarter_length: int
    age_range: tuple  # inclusive ages of the initial filler population
    filler_per_team: int
    reserve_per_team: int
    ratings: tuple  # (lo, hi) band for filler current ratings
    potentials: tuple  # (lo, hi) band for filler potentials
    stud_share: float  # fraction of fillers rolled well above the band
    youth_shrink: int = 0  # max inches taken off the adult height range for the youngest players
    teams: tuple = field(default_factory=tuple)

    @property
    def roster_size(self):
        return self.filler_per_team + self.reserve_per_team

    @property
    def reserve_capacity(self):
        return self.reserve_per_team * len(self.teams)


PREP_TEAMS = (
    Team("Brooklyn", "Ironworks", "BKI", 1, "#1D3557", "New York", "NY", "Ironworks Fieldhouse", 3200),
    Team("Philadelphia", "Forge", "PHF", 1, "#7D2E2E", "Philadelphia", "PA", "The Forge", 3000),
    Team("Boston", "Landmark", "BOL", 1, "#0B6E4F", "Boston", "MA", "Landmark Court", 2800),
    Team("Newark", "Ironside", "NWI", 1, "#2B2D42", "Newark", "NJ", "Ironside Center", 2600),
    Team("Atlanta", "Peachtree", "ATP", 2, "#C1440E", "Atlanta", "GA", "Peachtree Hall", 3100),
    Team("Miami", "Coastal", "MIC", 2, "#00A6A6", "Miami", "FL", "Coastal Pavilion", 2900),
    Team("Houston", "Bayou", "HOB", 2, "#6A4C93", "Houston", "TX", "Bayou Arena", 3400),
    Team("Memphis", "Delta", "MED", 2, "#4F6D7A", "Memphis", "TN", "Delta Gym", 2500),
    Team("Chicago", "Southside", "CHS", 3, "#A4161A", "Chicago", "IL", "Southside Armory", 3600),
    Team("Detroit", "Motorworks", "DTM", 3, "#005F73", "Detroit", "MI", "Motorworks Hall", 2700),
    Team("Indianapolis", "Crossroads", "INC", 3, "#BB8B00", "Indianapolis", "IN", "Crossroads Fieldhouse", 3300),
    Team("St. Louis", "Gateway", "STG", 3, "#3A506B", "St. Louis", "MO", "Gateway Court", 2600),
    Team("Los Angeles", "Westgate", "LAW", 4, "#8338EC", "Los Angeles", "CA", "Westgate Pavilion", 3800),
    Team("Oakland", "Harbor", "OAH", 4, "#006D77", "Oakland", "CA", "Harbor Gym", 2800),
    Team("Phoenix", "Sunbelt", "PHS", 4, "#E76F51", "Phoenix", "AZ", "Sunbelt Center", 3000),
    Team("Seattle", "Rainier", "SER", 4, "#2A9D8F", "Seattle", "WA", "Rainier Hall", 2900),
)

COLLEGE_TEAMS = (
    Team("Durham", "Blueflame", "DUR", 1, "#1B3A6B", "Durham", "NC", "Blueflame Arena", 9200),
    Team("Chapel Hill", "Sandhills", "CHH", 1, "#4C9BD1", "Chapel Hill", "NC", "Sandhills Coliseum", 8800),
    Team("Syracuse", "Northmen", "SYR", 1, "#D35400", "Syracuse", "NY", "Northmen Dome", 12000),
    Team("Storrs", "Colonials", "STO", 1, "#152238", "Storrs", "CT", "Colonial Center", 7600),
    Team("Lexington", "Bluegrass", "LEX", 2, "#3A5BA0", "Lexington", "KY", "Bluegrass Hall", 11400),
    Team("Gainesville", "Marshland", "GNV", 2, "#1D7874", "Gainesville", "FL", "Marshland Arena", 8100),
    Team("Knoxville", "Ridgeline", "KNX", 2, "#E8871E", "Knoxville", "TN", "Ridgeline Court", 9400),
    Team("Baton Rouge", "Levee", "BTR", 2, "#5B2A86", "Baton Rouge", "LA", "Levee Center", 7900),
    Team("Bloomington", "Limestone", "BLM", 3, "#A03033", "Bloomington", "IN", "Limestone Fieldhouse", 10600),
    Team("East Lansing", "Greenbelt", "ELS", 3, "#0F5132", "East Lansing", "MI", "Greenbelt Arena", 9800),
    Team("Lawrence", "Prairie", "LWR", 3, "#1F4E79", "Lawrence", "KS", "Prairie Fieldhouse", 10200),
    Team("Iowa City", "Blackhawk", "IWC", 3, "#2D2D2D", "Iowa City", "IA", "Blackhawk Hall", 8400),
    Team("Tucson", "Saguaro", "TUC", 4, "#C1272D", "Tucson", "AZ", "Saguaro Center", 8900),
    Team("Eugene", "Timberline", "EUG", 4, "#0B7A3B", "Eugene", "OR", "Timberline Court", 7200),
    Team("Boulder", "Flatiron", "BLD", 4, "#8C5A2B", "Boulder", "CO", "Flatiron Arena", 7800),
    Team("Spokane", "Cascade", "SPK", 4, "#264653", "Spokane", "WA", "Cascade Pavilion", 6900),
)

PRO_TEAMS = (
    Team("Toronto", "Northmen", "TOR", 1, "#5B2C6F", "Toronto", "CAN", "Harbourfront Centre", 19800),
    Team("New York", "Skyline", "NYS", 1, "#1F2A44", "New York", "NY", "Skyline Garden", 19500),
    Team("Boston", "Mariners", "BOS", 1, "#0B6E4F", "Boston", "MA", "Harbor Garden", 18600),
    Team("Philadelphia", "Liberty", "PHI", 1, "#A4161A", "Philadelphia", "PA", "Liberty Center", 20300),
    Team("Brooklyn", "Ironworks", "BKN", 1, "#333333", "New York", "NY", "Ironworks Arena", 18100),
    Team("Miami", "Current", "MIA", 2, "#00A6A6", "Miami", "FL", "Current Arena", 19600),
    Team("Atlanta", "Peaches", "ATL", 2, "#C1440E", "Atlanta", "GA", "Peachtree Arena", 18400),
    Team("Charlotte", "Sabres", "CHA", 2, "#3D6085", "Charlotte", "NC", "Sabre Center", 18500),
    Team("Orlando", "Comets", "ORL", 2, "#0077B6", "Orlando", "FL", "Comet Center", 18800),
    Team("Nashville", "Sound", "NSH", 2, "#E8B94F", "Nashville", "TN", "Sound Arena", 17900),
    Team("Chicago", "Stockyards", "CHI", 3, "#BB003A", "Chicago", "IL", "Stockyard Center", 22800),
    Team("Detroit", "Motors", "DET", 3, "#005F73", "Detroit", "MI", "Motor City Arena", 20400),
    Team("Cleveland", "Forge", "CLE", 3, "#6A040F", "Cleveland", "OH", "Forge Fieldhouse", 20500),
    Team("Milwaukee", "Lakefront", "MIL", 3, "#0F5132", "Milwaukee", "WI", "Lakefront Arena", 18700),
    Team("Minneapolis", "Northstars", "MIN", 3, "#2B4162", "Minneapolis", "MN", "Northstar Center", 19400),
    Team("Denver", "Summit", "DEN", 4, "#8C5A2B", "Denver", "CO", "Summit Arena", 19100),
    Team("Phoenix", "Sunbelt", "PHX", 4, "#E76F51", "Phoenix", "AZ", "Sunbelt Arena", 18400),
    Team("Los Angeles", "Westgate", "LAW", 4, "#8338EC", "Los Angeles", "CA", "Westgate Forum", 20100),
    Team("San Francisco", "Bayside", "SFB", 4, "#006D77", "San Francisco", "CA", "Bayside Pavilion", 18600),
    Team("Seattle", "Rainier", "SEA", 4, "#2A9D8F", "Seattle", "WA", "Rainier Arena", 18300),
)

PREP = LeagueSpec(
    key="prep", name="Cheezeyverse Prep", abbrev="CVP", save_name="CV_Prep",
    prestige=5,
    conferences=("North", "South"),
    divisions=("Prairie", "Northeast", "River", "Far"),
    schedule_games=30, playoff_teams=8, playoff_rounds=(0, 1, 1, 3),
    quarter_length=8,
    age_range=(14, 17),
    filler_per_team=12, reserve_per_team=3,
    ratings=(8, 38), potentials=(25, 58), stud_share=0.05,
    youth_shrink=1,  # an elite prep league: these are already near-grown prospects
    teams=PREP_TEAMS,
)

COLLEGE = LeagueSpec(
    key="college", name="Cheezeyverse College", abbrev="CVC", save_name="CV_College",
    prestige=3,
    conferences=("North", "South"),
    divisions=("Tundra", "Lakes", "Backroads", "Frontier"),
    schedule_games=32, playoff_teams=8, playoff_rounds=(0, 1, 1, 1),
    quarter_length=10,
    age_range=(18, 21),
    filler_per_team=12, reserve_per_team=3,
    ratings=(18, 52), potentials=(35, 70), stud_share=0.07,
    teams=COLLEGE_TEAMS,
)

PRO = LeagueSpec(
    key="pro", name="Cheezeyverse", abbrev="CV", save_name="CV_Pro",
    prestige=1,
    conferences=("North", "South"),
    divisions=("Borealis", "Heartland", "Delta", "Outlands"),
    schedule_games=58, playoff_teams=8, playoff_rounds=(0, 5, 7, 7),
    quarter_length=12,
    age_range=(22, 34),
    filler_per_team=12, reserve_per_team=3,
    ratings=(28, 62), potentials=(40, 75), stud_share=0.08,
    teams=PRO_TEAMS,
)

TEAMS_CSV = Path(__file__).resolve().parents[2] / "universe" / "teams.csv"
TEAM_CSV_HEADER = ["league", "division", "city", "nickname", "abbrev", "color", "arena_city", "state",
                   "arena", "capacity"]


def _load_team_overrides(path=TEAMS_CSV):
    """Team tables live in universe/teams.csv so they can be edited without touching code.

    The file wins whenever it exists; the tables above are only the starting point it was
    written from. Missing or empty file means fall back to those tables.
    """
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            if not row.get("league") or row["league"].lstrip().startswith("#"):
                continue
            key = row["league"].strip().lower()
            try:
                out.setdefault(key, []).append(Team(
                    city=row["city"].strip(), nickname=row["nickname"].strip(),
                    abbrev=row["abbrev"].strip().upper(), division=int(row["division"]),
                    color=row["color"].strip() or "#444444", hometown=(row["arena_city"].strip()
                                                                       or row["city"].strip()),
                    state=row["state"].strip(), arena=row["arena"].strip() or f'{row["city"].strip()} Arena',
                    capacity=int(row["capacity"] or 5000)))
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path} line {i}: {exc}") from exc
    return {k: tuple(v) for k, v in out.items() if v}


def write_teams_csv(path=TEAMS_CSV, specs=None):
    """Dump the current team tables to the editable CSV (used to seed it the first time)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TEAM_CSV_HEADER)
        for spec in (specs or (PREP, COLLEGE, PRO)):
            for t in spec.teams:
                w.writerow([spec.key, t.division, t.city, t.nickname, t.abbrev, t.color, t.hometown,
                            t.state, t.arena, t.capacity])
    return path


_overrides = _load_team_overrides()
if _overrides:
    PREP = replace(PREP, teams=_overrides.get("prep", PREP.teams))
    COLLEGE = replace(COLLEGE, teams=_overrides.get("college", COLLEGE.teams))
    PRO = replace(PRO, teams=_overrides.get("pro", PRO.teams))

LEAGUES = (PREP, COLLEGE, PRO)
BY_KEY = {spec.key: spec for spec in LEAGUES}


def _check_bracket(spec):
    """Round1..Round4 always has 4 entries; Round4 is the final and unused rounds are leading zeros.

    Confirmed against every season in Historical Leagues/North America/Leagues/USA{1,2,3}.csv:
    4 playoff teams -> (0,0,x,x), 8 -> (0,x,x,x), 12 and 16 -> (x,x,x,x). Only the 6-team bracket
    breaks the pattern (early-NBA divisional format), and no league here uses it.
    """
    out = []
    if len(spec.playoff_rounds) != 4:
        return [f"{spec.key}: playoff_rounds must have 4 entries"]
    if spec.playoff_teams == 6:
        return out
    needed = math.ceil(math.log2(spec.playoff_teams))
    if not 1 <= needed <= 4:
        return [f"{spec.key}: {spec.playoff_teams} playoff teams needs {needed} rounds, only 4 are available"]
    byes, live = spec.playoff_rounds[:4 - needed], spec.playoff_rounds[4 - needed:]
    if any(byes):
        out.append(f"{spec.key}: {spec.playoff_teams} playoff teams is a {needed}-round bracket, "
                   f"so the first {4 - needed} round value(s) must be 0, got {spec.playoff_rounds}")
    if not all(live):
        out.append(f"{spec.key}: round length 0 inside the live bracket {spec.playoff_rounds}")
    if any(g % 2 == 0 for g in live):
        out.append(f"{spec.key}: series lengths must be odd, got {spec.playoff_rounds}")
    return out


def validate():
    """Raise on anything FBPB3 would silently mangle."""
    problems = []
    for spec in LEAGUES:
        if spec.roster_size > ROSTER_LIMIT:
            problems.append(f"{spec.key}: roster {spec.roster_size} exceeds the {ROSTER_LIMIT}-man limit")
        if len(spec.teams) % len(spec.divisions):
            problems.append(f"{spec.key}: {len(spec.teams)} teams do not divide into {len(spec.divisions)} divisions")
        if spec.playoff_teams > len(spec.teams):
            problems.append(f"{spec.key}: {spec.playoff_teams} playoff teams but only {len(spec.teams)} teams")
        problems += _check_bracket(spec)
        abbrevs = [t.abbrev for t in spec.teams]
        if len(set(abbrevs)) != len(abbrevs):
            problems.append(f"{spec.key}: duplicate team abbreviations")
        for t in spec.teams:
            if not 1 <= t.division <= len(spec.divisions):
                problems.append(f"{spec.key}/{t.abbrev}: division {t.division} out of range")
    if problems:
        raise ValueError("; ".join(problems))
    return True
