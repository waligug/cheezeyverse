"""Codec regression tests against real saves.

Run: python tests/test_codec.py
Fixtures (gitignored, ~7MB each):
  fixtures/saves/chung-baseline/league.dat  - fresh save, ratings block preceded by zeros
  fixtures/saves/chung-aged/league.dat      - after two simmed seasons: per-season archive rows,
                                              non-contiguous player ids, retirements and rookies
"""
import csv
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from commissioner.codec.league_dat import LeagueDat, POSITIONS, RATINGS, POTENTIALS  # noqa: E402

BASELINE = ROOT / "fixtures/saves/chung-baseline/league.dat"
AGED = ROOT / "fixtures/saves/chung-aged/league.dat"
LIVE = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\leaguedata\CV_Prep\league.dat")
failures = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
    if not cond:
        failures.append(name)


def test_baseline_matches_game_exports():
    # Guarded the same way the aged fixture is. `fixtures/saves/` is gitignored, so on EVERY
    # clone - SERVERPC included, which is the only machine that runs a sim - this opened a file
    # that is not there and tracebacked with FileNotFoundError. A suite that is permanently red
    # for a reason nobody can fix teaches people to stop reading it, and the next real failure
    # goes past unnoticed. test_raise_stamina's comment claimed it skipped "the way test_codec
    # does"; test_codec did not, and now it does.
    if not BASELINE.exists():
        print("SKIP  baseline save fixture missing")
        return
    L = LeagueDat(BASELINE)
    check("baseline: 390 players", len(L.players) == 390, f"got {len(L.players)}")
    check("baseline: 18 teams", len(L.teams()) == 18)
    exp = list(csv.DictReader(open(ROOT / "fixtures/exports/chung_export.csv", encoding="latin-1")))
    bad = []
    for e in exp:
        m, d, y = e["DOB"].split("/")
        hits = [p for p in L.players if p.name == f'{e["FirstName"]} {e["LastName"]}' and p.dob == f"{int(m)}/{int(d)}/{y}"]
        if len(hits) != 1:
            bad.append((e["FirstName"], "not found"))
            continue
        v = hits[0].values
        for f in RATINGS + POTENTIALS + ["Height", "Weight", "Exp"]:
            if v[f] != int(e[f]):
                bad.append((e["LastName"], f))
        if POSITIONS.get(v["Position"]) != e["Position"]:
            bad.append((e["LastName"], "Position"))
    check("baseline: every field matches the in-game player export", not bad, str(bad[:3]))
    mdb = {(r["Name"], f'{r["BirthMonth"]}/{r["BirthDay"]}/{r["BirthYear"]}'): r
           for r in csv.DictReader(open(ROOT / "fixtures/exports/chung_mdb_player.csv", encoding="utf-8-sig"))}
    wrong_id = [p.name for p in L.players if int(mdb[(p.name, p.dob)]["ID"]) != p.id]
    check("baseline: player ids match the MDB", not wrong_id, str(wrong_id[:3]))
    wrong_team = [p.name for p in L.players if int(mdb[(p.name, p.dob)]["CurrentTeamID"]) != p.values["Team"]]
    check("baseline: teams match the MDB", not wrong_team, str(wrong_team[:3]))


def test_aged_save_matches_game_exports():
    """The aged fixture is the harder case: per-season archive rows, ratings above 100, retirements and
    rookies (so ids have gaps), and 139 unrostered free-agent / draft-pool players whose ids the codec infers."""
    if not AGED.exists():
        print("SKIP  aged save fixture missing")
        return
    L = LeagueDat(AGED)
    rows = list(csv.DictReader(open(ROOT / "fixtures/exports/chung_aged_mdb_player.csv", encoding="utf-8-sig")))
    mdb = {(r["Name"], f'{r["BirthMonth"]}/{r["BirthDay"]}/{r["BirthYear"]}'): r for r in rows}
    check("aged: every player in the MDB was parsed", len(L.players) == len(rows), f"{len(L.players)} of {len(rows)}")
    check("aged: 18 consistent teams", len(L.teams()) == 18)
    missing = [p.name for p in L.players if (p.name, p.dob) not in mdb]
    check("aged: every parsed player is in the MDB", not missing, str(missing[:3]))
    wrong_id = [p.name for p in L.players if (p.name, p.dob) in mdb and int(mdb[(p.name, p.dob)]["ID"]) != p.id]
    check("aged: ids match the MDB (incl. free agents and draft pool)", not wrong_id, str(wrong_id[:3]))
    wrong_team = [p.name for p in L.players if (p.name, p.dob) in mdb and int(mdb[(p.name, p.dob)]["CurrentTeamID"]) != p.values["Team"]]
    check("aged: teams match the MDB", not wrong_team, str(wrong_team[:3]))
    exp = list(csv.DictReader(open(ROOT / "fixtures/exports/chung_aged_export.csv", encoding="latin-1")))
    bad = []
    for e in exp:
        m, d, y = e["DOB"].split("/")
        hits = [p for p in L.players if p.name == f'{e["FirstName"]} {e["LastName"]}' and p.dob == f"{int(m)}/{int(d)}/{y}"]
        if len(hits) != 1:
            bad.append((e["LastName"], "not found"))
            continue
        for f in RATINGS + POTENTIALS + ["Height", "Weight"]:
            if hits[0].values[f] != int(e[f]):
                bad.append((e["LastName"], f))
    check("aged: every field matches the in-game player export", not bad, str(bad[:3]))
    unrostered = sum(1 for p in L.players if p.values["Team"] < 1)
    check("aged: fixture actually covers unrostered players", unrostered > 50, f"{unrostered} FA/draft")


def test_edits_round_trip():
    # The two historical fixtures are optional and intentionally gitignored. SERVERPC always has
    # the live save, so exercise structural edits against a disposable copy instead of reporting
    # a green suite in which the codec never opened a file.
    for src in (BASELINE, AGED, LIVE):
        if not src.exists():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "league.dat"
            shutil.copy(src, path)
            L = LeagueDat(path)
            before = len(L.data)
            team_count = len(L.teams())
            a = next(p for p in L.players if p.values["Team"] >= 1)
            b = next(p for p in L.players if p.values["Team"] >= 1 and p.values["Team"] != a.values["Team"])
            ta, tb = a.values["Team"], b.values["Team"]
            L.set(a, "InsideScoring", 77)
            L.swap_teams(a, b)
            L.save()
            M = LeagueDat(path)
            ok = (M.by_id[a.id].values["InsideScoring"] == 77 and M.by_id[a.id].values["Team"] == tb
                  and M.by_id[b.id].values["Team"] == ta and len(M.data) == before
                  and len(M.teams()) == team_count)
            label = src.parent.name if src != LIVE else "live-save-copy"
            check(f"{label}: rating edit + team swap round-trips", ok)
            # release then sign the same player back: net file size unchanged
            L2 = LeagueDat(path)
            p = next(q for q in L2.players if q.values["Team"] >= 1)
            t = p.values["Team"]
            L2.release(p)
            L2.sign(L2.by_id[p.id], t)
            L2.save()
            N = LeagueDat(path)
            check(f"{label}: release + sign round-trips",
                  N.by_id[p.id].values["Team"] == t and len(N.data) == before
                  and len(N.teams()) == team_count)
            # renaming re-encodes three strings, so the record changes length
            L3 = LeagueDat(path)
            target = L3.players[5]
            L3.rename(target, "Bartholomew", "Vandersteenhoven")
            L3.save()
            R1 = LeagueDat(path)
            long_ok = (R1.by_id[target.id].name == "Bartholomew Vandersteenhoven"
                       and len(R1.teams()) == team_count)
            R1.rename(R1.by_id[target.id], "Al", "Ng")
            R1.save()
            R2 = LeagueDat(path)
            check(f"{label}: rename (longer then shorter) round-trips",
                  long_ok and R2.by_id[target.id].name == "Al Ng" and len(R2.teams()) == team_count
                  and len(R2.players) == len(L3.players))


def main():
    """Run every check once; 0 when all passed. A plain script, and ALSO a unittest
    below - it used to call sys.exit at import, which the unittest loader reported as an
    error on every full run even when every check passed."""
    failures.clear()
    test_baseline_matches_game_exports()
    test_aged_save_matches_game_exports()
    test_edits_round_trip()
    # The summary has to distinguish "everything passed" from "nothing ran". Both fixtures live in
    # the gitignored fixtures/saves/, so on a clone all three tests skip - and a last line reading
    # "all codec tests passed" told a reader scanning for green that the codec had been checked
    # when it had not been opened. Whoever reads this output usually reads only this line.
    if failures:
        print("FAILED: " + ", ".join(failures))
    elif not BASELINE.exists() and not AGED.exists() and not LIVE.exists():
        print("\nSKIPPED: no historical fixtures or live save present; codec edits were not exercised")
    elif not BASELINE.exists() and not AGED.exists():
        print("\ncodec structural edits passed against a disposable live-save copy; "
              "historical export-comparison fixtures are unavailable")
    elif not (BASELINE.exists() and AGED.exists()):
        print("\ncodec tests passed, but one fixture is missing - see SKIP above")
    else:
        print("\nall codec tests passed")
    return 1 if failures else 0


import unittest  # noqa: E402


class RunsClean(unittest.TestCase):
    def test_every_check_passes(self):
        self.assertEqual(main(), 0, failures)


if __name__ == "__main__":
    sys.exit(main())
