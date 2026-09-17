"""Controlled DOB test: three players whose codec DOB matches the game export, set to ages 13/14/15."""
import sys, csv, json
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.codec.league_dat import LeagueDat

SAVE = r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\leaguedata\Chung_test\league.dat"
EXPORT = r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\PlayerFiles\age_test.csv"
rows = {(r['FirstName'], r['LastName']): r for r in csv.DictReader(open(EXPORT, encoding='latin1'))}
lg = LeagueDat(SAVE)
cands = [p for p in lg.players
         if p.values["Team"] >= 1 and (p.first, p.last) in rows and rows[(p.first, p.last)]['DOB'] == p.dob]
picks = sorted(cands, key=lambda p: -p.values["BirthYear"])[:3]
out = []
for pl, age in zip(picks, (13, 14, 15)):
    out.append({"name": pl.name, "id": pl.id, "team": pl.values["Team"], "old_dob": pl.dob, "target_age": age})
    lg.set(pl, "BirthYear", 2015 - age)
    print(f'{pl.name} id={pl.id} team={pl.values["Team"]} {out[-1]["old_dob"]} -> {pl.dob} (target age {age})')
lg.save(backup_dir=r"C:\claude\hoops-universe\backups")
json.dump(out, open(r"C:\claude\hoops-universe\tmp\age2.json", "w"), indent=1)
print("saved")
