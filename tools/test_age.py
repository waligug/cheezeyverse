"""Phase 0 age-floor test: set three rostered players to ages 14/15/16 in Chung_test and report."""
import sys
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.codec.league_dat import LeagueDat

SAVE = r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\leaguedata\Chung_test\league.dat"
SEASON = 2015
lg = LeagueDat(SAVE)
rostered = [p for p in lg.players if p.values["Team"] >= 1]
# youngest rostered players, so the rest of their record stays plausible
rostered.sort(key=lambda p: -p.values["BirthYear"])
picks = rostered[:3]
for pl, age in zip(picks, (14, 15, 16)):
    before = pl.dob
    lg.set(pl, "BirthYear", SEASON - age)
    lg.set(pl, "BirthMonth", 12)
    lg.set(pl, "BirthDay", 1)
    print(f"{pl.name} id={pl.id} team={pl.values['Team']} dob {before} -> {pl.dob} (age {age})")
lg.save(backup_dir=r"C:\claude\hoops-universe\backups")
print("saved")
