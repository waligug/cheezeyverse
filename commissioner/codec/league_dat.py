"""Read/write player records in an FBPB3 league.dat (in-place int16 edits only; file layout never changes).

Layout notes and how each offset was confirmed live in CONVENTIONS.md. Anchors per player record:
  S  start of the name triple:  str Full, str First, str Last, str Nickname
     then int16 Height, Weight, 0, BirthMonth, BirthDay, BirthYear
     then str College, str City, str State, str Nation
  E  end of that bio block (E+18 Position, E+40 Team id, E+82 Experience)
  R  start of the current-ratings block (18 int16), potentials at R+168 (12 int16)
Strings are uint16-length-prefixed latin-1. Records are sequential; R is the first offset after E
that matches the ratings-block signature (see _find_ratings).
"""
from __future__ import annotations

import re
import shutil
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

RATINGS = ["InsideScoring", "JumpShot", "FtShot", "3pUsage", "3pShot", "Handling", "Passing", "Quickness",
           "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "OReb", "DReb", "Jumping", "Strength",
           "Stamina", "Fouling"]
POTENTIALS = ["PotInside", "PotJumpShot", "PotFtShot", "Pot3pShot", "PotHandling", "PotPassing", "PotOReb",
              "PotDReb", "PotPostDefense", "PotPerimeterDefense", "PotStealing", "PotBlocking"]
POT_OFFSET = 168
BIO_INTS = ["Height", "Weight", "_zero", "BirthMonth", "BirthDay", "BirthYear"]
E_FIELDS = {"Position": 18, "Team": 40, "Exp": 82}
POSITIONS = {1: "C", 2: "PF", 3: "SF", 4: "SG", 5: "PG"}

_NAME_RE = re.compile(rb"[\x03-\x30]\x00[^\x00-\x1f\x7f]{3,48}")


class CodecError(Exception):
    pass


@dataclass
class Player:
    S: int
    E: int
    R: int
    bio_at: int
    name: str
    first: str
    last: str
    values: dict = field(default_factory=dict)

    @property
    def dob(self):
        return f'{self.values["BirthMonth"]}/{self.values["BirthDay"]}/{self.values["BirthYear"]}'


class LeagueDat:
    def __init__(self, path):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        self.players = self._parse()

    # ---- low level -------------------------------------------------------------------------
    def _u16(self, p):
        return struct.unpack_from("<H", self.data, p)[0]

    def _i16(self, p):
        return struct.unpack_from("<h", self.data, p)[0]

    def _str(self, p):
        n = self._u16(p)
        return self.data[p + 2:p + 2 + n].decode("latin-1"), p + 2 + n

    def _find_ratings(self, start, stop):
        d = self.data
        for q in range(start + 52, stop):
            if d[q - 52:q - 48] != b"\x01\x00\x01\x00" or any(d[q - 48:q]):
                continue
            cur = struct.unpack_from("<18h", d, q)
            pot = struct.unpack_from("<12h", d, q + POT_OFFSET)
            if any(cur) and all(0 <= v <= 100 for v in cur + pot):
                return q
        return None

    def _parse(self):
        triples = []
        for m in _NAME_RE.finditer(self.data):
            p = m.start()
            try:
                full, p1 = self._str(p)
                first, p2 = self._str(p1)
                last, p3 = self._str(p2)
            except (struct.error, UnicodeDecodeError):
                continue
            if first and last and full == f"{first} {last}":
                triples.append((p, full, first, last, p3))
        players = []
        for i, (s, full, first, last, p) in enumerate(triples):
            _, p = self._str(p)  # nickname
            bio_at = p
            p += 12
            for _ in range(4):
                _, p = self._str(p)
            stop = triples[i + 1][0] if i + 1 < len(triples) else len(self.data)
            r = self._find_ratings(p, stop)
            if r is None:
                continue
            pl = Player(S=s, E=p, R=r, bio_at=bio_at, name=full, first=first, last=last)
            self._read_values(pl)
            players.append(pl)
        return players

    def _slots(self, pl):
        """Map every editable field name to its absolute int16 offset for this player."""
        slots = {name: pl.bio_at + 2 * i for i, name in enumerate(BIO_INTS) if not name.startswith("_")}
        slots.update({name: pl.E + off for name, off in E_FIELDS.items()})
        slots.update({name: pl.R + 2 * i for i, name in enumerate(RATINGS)})
        slots.update({name: pl.R + POT_OFFSET + 2 * i for i, name in enumerate(POTENTIALS)})
        return slots

    def _read_values(self, pl):
        pl.values = {k: self._i16(off) for k, off in self._slots(pl).items()}

    # ---- public API ------------------------------------------------------------------------
    def find(self, name, dob=None):
        hits = [p for p in self.players if p.name == name and (dob is None or p.dob == dob)]
        if len(hits) != 1:
            raise CodecError(f"{len(hits)} players match {name!r} dob={dob!r}")
        return hits[0]

    def set(self, pl, field_name, value):
        slots = self._slots(pl)
        if field_name not in slots:
            raise CodecError(f"unknown field {field_name}")
        if field_name in RATINGS or field_name in POTENTIALS:
            if not 0 <= value <= 100:
                raise CodecError(f"{field_name}={value} out of range 0..100")
        struct.pack_into("<h", self.data, slots[field_name], int(value))
        pl.values[field_name] = int(value)

    def save(self, backup_dir=None):
        """Write in place after verifying a fresh parse sees exactly the intended values."""
        if backup_dir:
            dest = Path(backup_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, dest / self.path.name)
        tmp = self.path.with_suffix(".dat.tmp")
        tmp.write_bytes(self.data)
        check = LeagueDat(tmp)
        if len(check.players) != len(self.players):
            tmp.unlink()
            raise CodecError("record count changed after write")
        for a, b in zip(self.players, check.players):
            if (a.S, a.R) != (b.S, b.R) or a.values != b.values:
                tmp.unlink()
                raise CodecError(f"verification failed for {a.name}")
        tmp.replace(self.path)
