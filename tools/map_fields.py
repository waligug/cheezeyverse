"""Map player-file CSV fields to int16 offsets inside league.dat player records.

For each player in the in-game export, locate the name triple, then parse the string block
(full, first, last, nickname, height, weight, ?, month, day, year, college, city, state, nation).
Every CSV field is tested at every offset relative to the end of that block; offsets that match
for (nearly) all players are the field's location.
"""
import csv
import struct
import sys
from collections import defaultdict

DAT = sys.argv[1] if len(sys.argv) > 1 else "fixtures/saves/chung-baseline/league.dat"
CSV = sys.argv[2] if len(sys.argv) > 2 else "fixtures/exports/chung_export.csv"
data = open(DAT, "rb").read()
rows = list(csv.DictReader(open(CSV, newline="", encoding="latin-1")))


def u16(p):
    return struct.unpack_from("<H", data, p)[0]


def i16(p):
    return struct.unpack_from("<h", data, p)[0]


def pstr(s):
    b = s.encode("latin-1")
    return struct.pack("<H", len(b)) + b


def skip_str(p):
    return p + 2 + u16(p)


def locate(first, last):
    needle = pstr(f"{first} {last}") + pstr(first) + pstr(last)
    hits, i = [], data.find(needle)
    while i != -1:
        hits.append(i)
        i = data.find(needle, i + 1)
    return hits


def block_end(p):
    """Walk the name/bio block; return (end offset, parsed header ints)."""
    for _ in range(4):  # full, first, last, nickname
        p = skip_str(p)
    ints = struct.unpack_from("<4h", data, p)  # height, weight, ?, month  (day, year follow)
    p += 2 * 6
    for _ in range(4):  # college, city, state, nation
        p = skip_str(p)
    return p, ints


players = []
for r in rows:
    hits = locate(r["FirstName"], r["LastName"])
    if len(hits) != 1:
        continue
    end, hdr = block_end(hits[0])
    players.append((r, hits[0], end))
print(f"{len(players)} of {len(rows)} players located uniquely")

# sanity: header ints vs CSV
bad = [r["FirstName"] + " " + r["LastName"] for r, start, end in players
       if struct.unpack_from("<2h", data, start + 2 * 4 + sum(2 + len(x) for x in
          (f'{r["FirstName"]} {r["LastName"]}', r["FirstName"], r["LastName"])) + 0) and False]

SKIP = {"FirstName", "LastName", "Position", "DOB", "City", "State", "College", "Injury", "Team", "Option", "Picname"}
LO, HI = -400, 3200
report = {}
for field in rows[0].keys():
    if field in SKIP:
        continue
    counts = defaultdict(int)
    usable = 0
    for r, start, end in players:
        try:
            v = int(float(r[field]))
        except ValueError:
            continue
        if not -32768 <= v <= 32767:
            continue
        usable += 1
        for off in range(LO, HI, 1):
            q = end + off
            if 0 <= q < len(data) - 1 and i16(q) == v:
                counts[off] += 1
    best = sorted(counts.items(), key=lambda kv: -kv[1])[:4]
    report[field] = (usable, best)
    print(f"{field:22s} n={usable:3d} " + "  ".join(f"@{o}:{c}" for o, c in best))
