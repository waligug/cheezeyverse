"""Second mapping pass: test every known field against three anchors per player.

Anchors: S = start of name triple, E = end of bio string block, R = start of current-ratings block
(located by the unique 18-rating signature from the in-game player-file export).
A field is reported at an anchor+offset when it matches for every player (or nearly).
"""
import csv
import struct
from collections import defaultdict

DAT = "fixtures/saves/chung-baseline/league.dat"
data = open(DAT, "rb").read()
exp_rows = {(r["FirstName"], r["LastName"]): r for r in csv.DictReader(open("fixtures/exports/chung_export.csv", encoding="latin-1"))}
mdb_rows = {(r["FirstName"], r["LastName"]): r for r in csv.DictReader(open("fixtures/exports/chung_mdb_player.csv", encoding="utf-8-sig"))}

u16 = lambda p: struct.unpack_from("<H", data, p)[0]
i16 = lambda p: struct.unpack_from("<h", data, p)[0]
pstr = lambda s: struct.pack("<H", len(s.encode("latin-1"))) + s.encode("latin-1")
RATING_ORDER = ["InsideScoring", "JumpShot", "FtShot", "3pUsage", "3pShot", "Handling", "Passing", "Quickness",
                "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "OReb", "DReb", "Jumping", "Strength",
                "Stamina", "Fouling"]

players = []
for key, e in exp_rows.items():
    needle = pstr(f"{key[0]} {key[1]}") + pstr(key[0]) + pstr(key[1])
    s = data.find(needle)
    if s == -1 or data.find(needle, s + 1) != -1 or key not in mdb_rows:
        continue
    p = s
    for _ in range(4):
        p += 2 + u16(p)
    p += 12
    for _ in range(4):
        p += 2 + u16(p)
    sig = struct.pack("<18h", *[int(e[f]) for f in RATING_ORDER])
    r = data.find(sig, p)
    fields = {}
    for src in (e, mdb_rows[key]):
        for k, v in src.items():
            try:
                fields[k] = int(float(v))
            except ValueError:
                if v in ("True", "False"):
                    fields[k] = -1 if v == "True" else 0
    players.append(({"S": s, "E": p, "R": r}, fields))
print(len(players), "players")

RANGES = {"S": (-200, 60), "E": (0, 520), "R": (-70, 1400)}
SKIP_R = set(RATING_ORDER)
for field in sorted(players[0][1]):
    vals = [f.get(field) for _, f in players]
    if len(set(vals)) < 3:  # too little variety to be conclusive
        continue
    found = []
    for anc, (lo, hi) in RANGES.items():
        for off in range(lo, hi, 2):
            if anc == "R" and 0 <= off < 36:
                continue
            n = sum(1 for a, f in players if field in f and -32768 <= f[field] <= 32767 and i16(a[anc] + off) == f[field])
            if n >= 0.97 * len(players):
                found.append(f"{anc}{off:+d}({n})")
    print(f"{field:22s}", " ".join(found) if found else "-")
