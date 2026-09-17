"""Read-only scan of league.dat for player name triples: len16 "Full" len16 "First" len16 "Last".

Prints offsets and deltas between consecutive matches to test for a fixed-stride player table.
"""
import re
import struct
import sys
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else "fixtures/saves/chung-baseline/league.dat"
data = open(PATH, "rb").read()

def read_str(pos):
    if pos + 2 > len(data):
        return None, pos
    n = struct.unpack_from("<H", data, pos)[0]
    if n == 0 or n > 40:
        return None, pos
    s = data[pos + 2:pos + 2 + n]
    if not re.fullmatch(rb"[A-Za-z .'\-]+", s):
        return None, pos
    return s.decode("ascii"), pos + 2 + n

hits = []
for m in re.finditer(rb"[\x03-\x28]\x00[A-Z][A-Za-z.'\-]* [A-Za-z .'\-]+", data):
    pos = m.start()
    full, p = read_str(pos)
    if not full:
        continue
    first, p2 = read_str(p)
    last, p3 = read_str(p2)
    if first and last and full == f"{first} {last}":
        hits.append((pos, full))

print(f"file size {len(data)}, name triples {len(hits)}")
deltas = [b[0] - a[0] for a, b in zip(hits, hits[1:])]
print("most common deltas:", Counter(deltas).most_common(10))
for pos, full in hits[:15]:
    print(pos, full)
print("...")
for pos, full in hits[-5:]:
    print(pos, full)
