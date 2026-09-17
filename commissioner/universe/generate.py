"""Generate the FBPB3 league files and roster files for the three Hoops Universe saves.

Outputs, per league:
  <FBPB3 docs>/LeagueFiles/<LeagueFile>.csv   league settings + team table (used at New Game)
  <FBPB3 docs>/PlayerFiles/<RosterFile>.csv   initial rosters: fillers + dormant reserve slots
  universe/manifest.json (in the project)     every generated player, with role/team/dob/uniform

The manifest is how the commissioner app finds a reserve slot later: the codec looks a player
up by name + DOB, then renames and re-rates him in place. Reserve rows are ordinary-looking
players on purpose, so the public site never shows placeholder junk.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from . import config as cfg

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
GAME = Path(r"C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3")
PROJECT = Path(__file__).resolve().parents[2]

LEAGUE_HEADER = ["LeagueName", "Abbreviation", "HostNation", "Region", "Team Locations", "Prestige", "Conf#",
                 "C1", "C2", "Div#", "D1", "D2", "D3", "D4", "D5", "D6", "ScheduleGames", "PlayoffTeams",
                 "Round1", "Round2", "Round3", "Round4", "3pShot", "MaxFouls", "ShotClock", "Quarters",
                 "QuarterLength", "OtLength", "SalaryCap", "LuxuryCap", "MLE", "LLE", "ResignLength",
                 "FaLength", "ResignRaise", "FaRaise", "WaiverLength", "RookieDraft", "DraftLottery",
                 "ExpansionDraft", "ExpansionLottery", "DispersalDraft"]

TEAM_HEADER = ["CityName", "NickName", "Abbreviation", "Logo", "Division", "TeamColor", "City", "State",
               "Marketsize", "ArenaName", "Capacity", "ClubSeats", "LuxurySuits", "Opened", "NewCity",
               "NewState", "NewMarketsize", "NewArena", "NewCapacity", "NewSuites", "NewClub", "Opens"]

PLAYER_HEADER = ["FirstName", "LastName", "Height", "Weight", "Position", "DOB", "Uniform", "City", "State",
                 "College", "Exp", "Injury", "TimeInjured", "InsideScoring", "PotInside", "JumpShot",
                 "PotJumpShot", "FtShot", "PotFtShot", "3pShot", "Pot3pShot", "3pUsage", "Handling",
                 "PotHandling", "Passing", "PotPassing", "PostDefense", "PotPostDefense", "PerimeterDefense",
                 "PotPerimeterDefense", "Stealing", "PotStealing", "Blocking", "PotBlocking", "OReb",
                 "PotOReb", "DReb", "PotDReb", "Fouling", "Strength", "Quickness", "Jumping", "Stamina",
                 "Team", "Contract1", "Contract2", "Contract3", "Contract4", "Contract5", "Contract6",
                 "Contract7", "BirdYears", "Option", "Picname", "InjuryAvoidance"]

# 12 fillers per team, then the dormant reserve slots
FILLER_POSITIONS = ["C", "C", "PF", "PF", "SF", "SF", "SF", "SG", "SG", "SG", "PG", "PG"]
RESERVE_POSITIONS = ["PG", "SF", "C"]

# inches, at the league's oldest age; younger players are scaled down in height_for()
HEIGHT_RANGE = {"C": (78, 86), "PF": (76, 83), "SF": (74, 81), "SG": (72, 79), "PG": (68, 76)}

# per-position multipliers applied to the league's rating band
SKILL_WEIGHTS = {
    "C":  {"InsideScoring": 1.3, "JumpShot": .6, "FtShot": .7, "3pShot": .2, "Handling": .4, "Passing": .5,
           "PostDefense": 1.3, "PerimeterDefense": .5, "Stealing": .5, "Blocking": 1.4, "OReb": 1.3,
           "DReb": 1.3, "Quickness": .6, "Jumping": 1.0, "Strength": 1.3},
    "PF": {"InsideScoring": 1.2, "JumpShot": .8, "FtShot": .8, "3pShot": .5, "Handling": .6, "Passing": .6,
           "PostDefense": 1.2, "PerimeterDefense": .7, "Stealing": .6, "Blocking": 1.1, "OReb": 1.2,
           "DReb": 1.2, "Quickness": .8, "Jumping": 1.1, "Strength": 1.2},
    "SF": {"InsideScoring": 1.0, "JumpShot": 1.0, "FtShot": 1.0, "3pShot": .9, "Handling": .9, "Passing": .9,
           "PostDefense": .9, "PerimeterDefense": 1.0, "Stealing": .9, "Blocking": .8, "OReb": .9,
           "DReb": 1.0, "Quickness": 1.0, "Jumping": 1.1, "Strength": 1.0},
    "SG": {"InsideScoring": .8, "JumpShot": 1.2, "FtShot": 1.1, "3pShot": 1.2, "Handling": 1.1, "Passing": 1.0,
           "PostDefense": .6, "PerimeterDefense": 1.1, "Stealing": 1.1, "Blocking": .4, "OReb": .6,
           "DReb": .7, "Quickness": 1.2, "Jumping": 1.1, "Strength": .8},
    "PG": {"InsideScoring": .7, "JumpShot": 1.1, "FtShot": 1.2, "3pShot": 1.1, "Handling": 1.4, "Passing": 1.4,
           "PostDefense": .4, "PerimeterDefense": 1.0, "Stealing": 1.2, "Blocking": .2, "OReb": .4,
           "DReb": .6, "Quickness": 1.3, "Jumping": 1.0, "Strength": .7},
}
USAGE_3P = {"C": (0, 8), "PF": (3, 25), "SF": (15, 45), "SG": (25, 60), "PG": (20, 55)}

RATED = ["InsideScoring", "JumpShot", "FtShot", "3pShot", "Handling", "Passing", "PostDefense",
         "PerimeterDefense", "Stealing", "Blocking", "OReb", "DReb"]  # these have potentials
UNRATED = ["Fouling", "Strength", "Quickness", "Jumping", "Stamina"]  # no potential column
POT_COLUMN = {"InsideScoring": "PotInside", "JumpShot": "PotJumpShot", "FtShot": "PotFtShot",
              "3pShot": "Pot3pShot", "Handling": "PotHandling", "Passing": "PotPassing",
              "PostDefense": "PotPostDefense", "PerimeterDefense": "PotPerimeterDefense",
              "Stealing": "PotStealing", "Blocking": "PotBlocking", "OReb": "PotOReb", "DReb": "PotDReb"}

# reserve slots are deliberately terrible so the AI coach gives them no minutes until claimed
RESERVE_RATINGS = (3, 12)
RESERVE_POTENTIALS = (10, 25)


def _lines(path):
    with open(path, encoding="latin-1") as fh:
        return [ln.strip() for ln in fh if ln.strip()]


def name_pools():
    """First/last name pools from the game's own Names folder, English-weighted."""
    first, last = [], []
    for origin, weight in (("English", 6), ("African", 3), ("Spanish", 2), ("French", 1), ("German", 1),
                           ("Italian", 1), ("Serbian", 1), ("Turkish", 1), ("Lithuanian", 1)):
        f = GAME / "Names" / "FirstNames" / f"{origin}.txt"
        ln = GAME / "Names" / "LastNames" / f"{origin}.txt"
        if f.exists():
            first += _lines(f) * weight
        if ln.exists():
            last += _lines(ln) * weight
    if not first or not last:
        raise FileNotFoundError(f"no name pools under {GAME / 'Names'}")
    return first, last


def hometowns():
    rows = []
    with open(GAME / "HomeTowns" / "USA.csv", encoding="latin-1") as fh:
        for row in csv.DictReader(fh):
            if row.get("Name"):
                rows.append((row["Name"], row["State"]))
    return rows


def height_for(rng, position, age, oldest):
    lo, hi = HEIGHT_RANGE[position]
    shrink = min(5, max(0, 18 - age))  # 14-year-olds are not full size yet; adults are unscaled
    return rng.randint(lo - shrink, hi - shrink)


def weight_for(rng, height, age):
    base = 100 + (height - 60) * 5.2
    base -= max(0, (18 - age)) * 6  # teenagers are light
    return int(max(105, rng.gauss(base, 9)))


def roll(rng, lo, hi, weight, stud=False):
    span = hi - lo
    value = rng.triangular(lo, hi, lo + span * 0.4)
    if stud:
        value += span * rng.uniform(0.5, 1.1)
    return int(max(1, min(99, value * weight)))


def make_player(rng, spec, team, position, age, first_pool, last_pool, towns, reserve=False):
    oldest = spec.age_range[1]
    height = height_for(rng, position, age, oldest)
    stud = (not reserve) and rng.random() < spec.stud_share
    r_lo, r_hi = RESERVE_RATINGS if reserve else spec.ratings
    p_lo, p_hi = RESERVE_POTENTIALS if reserve else spec.potentials
    weights = SKILL_WEIGHTS[position]
    town, state = rng.choice(towns)

    row = {c: 0 for c in PLAYER_HEADER}
    row.update({
        "FirstName": rng.choice(first_pool),
        "LastName": rng.choice(last_pool),
        "Height": height,
        "Weight": weight_for(rng, height, age),
        "Position": position,
        "DOB": f"{rng.randint(1, 12)}/{rng.randint(1, 28)}/{cfg.START_YEAR - age}",
        "Uniform": 0,  # assigned per team below
        "City": town,
        "State": state,
        "College": "None",
        "Exp": max(0, age - spec.age_range[0]),
        "Injury": "",
        "TimeInjured": 0,
        "Team": team.abbrev,
        "Option": "None",
        "Picname": "",
        "InjuryAvoidance": -1,
        "3pUsage": rng.randint(*USAGE_3P[position]),
    })
    for skill in RATED:
        cur = roll(rng, r_lo, r_hi, weights.get(skill, 1.0), stud)
        pot = max(cur, roll(rng, p_lo, p_hi, weights.get(skill, 1.0), stud))
        row[skill] = cur
        row[POT_COLUMN[skill]] = pot
    for skill in UNRATED:
        row[skill] = roll(rng, r_lo, r_hi, weights.get(skill, 1.0), stud) if skill != "Stamina" \
            else rng.randint(40, 90)
    row["Fouling"] = rng.randint(20, 75)
    return row


def build_league(spec, seed):
    rng = random.Random(seed)
    first_pool, last_pool = name_pools()
    towns = hometowns()
    players, manifest = [], []
    lo_age, hi_age = spec.age_range

    for team in spec.teams:
        used_numbers = set()
        plan = [(p, False) for p in FILLER_POSITIONS[:spec.filler_per_team]]
        plan += [(RESERVE_POSITIONS[i % len(RESERVE_POSITIONS)], True) for i in range(spec.reserve_per_team)]
        for position, reserve in plan:
            age = rng.randint(lo_age, hi_age) if not reserve else rng.randint(lo_age, min(lo_age + 1, hi_age))
            row = make_player(rng, spec, team, position, age, first_pool, last_pool, towns, reserve)
            number = rng.randint(0, 55)
            while number in used_numbers:
                number = rng.randint(0, 55)
            used_numbers.add(number)
            row["Uniform"] = number
            players.append(row)
            manifest.append({"league": spec.key, "team": team.abbrev, "role": "reserve" if reserve else "filler",
                             "name": f'{row["FirstName"]} {row["LastName"]}', "dob": row["DOB"],
                             "position": position, "uniform": number})
    return players, manifest


def write_league_file(spec, path):
    conf = list(spec.conferences) + [""] * (2 - len(spec.conferences))
    divs = list(spec.divisions) + [""] * (6 - len(spec.divisions))
    r1, r2, r3, r4 = spec.playoff_rounds
    league_row = [spec.name, spec.abbrev, "USA", 1, 1, spec.prestige, len(spec.conferences), *conf,
                  len(spec.divisions), *divs, spec.schedule_games, spec.playoff_teams, r1, r2, r3, r4,
                  1, 6, 24, 4, spec.quarter_length, 5,
                  58044000, 0, 5000000, 2180000, 5, 4, 0, 0, 0,
                  0, 0, 0, 0, 0]  # rookie/lottery/expansion/dispersal drafts off: the app runs the draft
    with open(path, "w", newline="", encoding="latin-1") as fh:
        w = csv.writer(fh)
        w.writerow(LEAGUE_HEADER)
        w.writerow(league_row)
        w.writerow(TEAM_HEADER)
        for t in spec.teams:
            w.writerow([t.city, t.nickname, t.abbrev, t.abbrev.lower(), t.division, t.color, t.hometown,
                        t.state, 3, t.arena, t.capacity, 0, 0, 2020, "", "", 0, "", 0, 0, 0, 0])


def write_player_file(players, path):
    with open(path, "w", newline="", encoding="latin-1") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAYER_HEADER)
        w.writeheader()
        for row in players:
            w.writerow(row)


def generate(seed=20300917, docs=DOCS, out_dir=None):
    cfg.validate()
    docs = Path(docs)
    league_dir, player_dir = docs / "LeagueFiles", docs / "PlayerFiles"
    league_dir.mkdir(parents=True, exist_ok=True)
    player_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(out_dir) if out_dir else PROJECT / "universe"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest, summary = [], []
    for i, spec in enumerate(cfg.LEAGUES):
        players, entries = build_league(spec, seed + i)
        lf = league_dir / f"HU-{spec.key.capitalize()}.csv"
        pf = player_dir / f"HU-{spec.key.capitalize()}-Rosters.csv"
        write_league_file(spec, lf)
        write_player_file(players, pf)
        manifest += entries
        summary.append({"league": spec.key, "name": spec.name, "save": spec.save_name,
                        "teams": len(spec.teams), "players": len(players),
                        "reserves": sum(1 for e in entries if e["role"] == "reserve"),
                        "league_file": str(lf), "player_file": str(pf)})
    (out_dir / "manifest.json").write_text(
        json.dumps({"start_year": cfg.START_YEAR, "seed": seed, "leagues": summary, "players": manifest},
                   indent=1), encoding="utf-8")
    return summary
