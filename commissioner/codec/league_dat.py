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
    id: int = 0
    T1: int = 0

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

    _ARR2 = bytes([1, 0, 2, 0, 0, 0, 0, 0, 0, 0])  # VB6 array header: dims=1, count=2, lbound=0

    def _find_team_fields(self, E, R):
        """Two more team ids live between E and R: T1 sits between two count-2 array headers
        (header at T1-20, header at T1+2); T2 = T1+166. T1 always equals the current team;
        T2 equals it for rostered players (FA/draft players keep their former team there)."""
        d = self.data
        hits = [q for q in range(E + 84, R) if d[q - 20:q - 10] == self._ARR2 and d[q + 2:q + 12] == self._ARR2]
        if len(hits) != 1:
            raise CodecError(f"record at {E}: {len(hits)} team-field anchors")
        return hits[0]

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
            pl.T1 = self._find_team_fields(p, r)
            self._read_values(pl)
            players.append(pl)
        self._assign_ids(players)
        return players

    def _assign_ids(self, players):
        """Records are stored in contiguous id order; the base id is the (id, id) pair shortly before the name."""
        votes = {}
        for i, pl in enumerate(players):
            for o in range(pl.S - 40, pl.S - 2, 2):
                a, b = struct.unpack_from("<2h", self.data, o)
                if a == b and a - i > 0:
                    votes[a - i] = votes.get(a - i, 0) + 1
        if not votes:
            raise CodecError("could not determine player id base")
        base = max(votes, key=votes.get)
        if votes[base] < 0.6 * len(players):
            raise CodecError(f"player id base {base} only supported by {votes[base]}/{len(players)} records")
        for i, pl in enumerate(players):
            pl.id = base + i
        self.by_id = {pl.id: pl for pl in players}

    # ---- team structures -------------------------------------------------------------------
    # Membership lives in three places per team (see CONVENTIONS.md): the roster dynamic array in the team
    # record, a 14-slot lineup array right after it, and 5 depth-chart blocks (81 int16) near the end of file.
    # E+40 on each player is a save-time copy; it is only used to identify which array belongs to which team.
    LINEUP_GAP, LINEUP_SLOTS = 22, 14
    DEPTH_BLOCK, DEPTH_PER_TEAM = 162, 5

    def teams(self):
        members = {}
        for pl in self.players:
            if pl.values["Team"] >= 1:
                members.setdefault(pl.values["Team"], set()).add(pl.id)
        d, limit = self.data, self.players[0].S
        arrays = []
        q = d.find(b"\x01\x00", 0, limit)
        while q != -1:
            if d[q + 4:q + 10] == b"\0" * 6:
                n = self._u16(q + 2)
                if 2 <= n <= 40:
                    vals = struct.unpack_from(f"<{n}h", d, q + 10)
                    if vals[0] == 0:
                        arrays.append((q, n, frozenset(vals[1:])))
            q = d.find(b"\x01\x00", q + 1, limit)
        teams = {}
        for t, ids in members.items():
            hits = [(q, n) for q, n, s in arrays if s == ids and n - 1 == len(ids)]
            if len(hits) != 1:
                raise CodecError(f"team {t}: {len(hits)} roster arrays match its {len(ids)} players")
            q, n = hits[0]
            lineup_at = q + 10 + 2 * n + self.LINEUP_GAP
            lineup = struct.unpack_from(f"<{self.LINEUP_SLOTS}h", d, lineup_at)
            if not {v for v in lineup if v} <= ids:
                raise CodecError(f"team {t}: lineup at {lineup_at} holds non-roster ids {lineup}")
            teams[t] = {"roster_at": q + 12, "size": n - 1, "lineup_at": lineup_at, "ids": set(ids), "depth_at": []}
        owner = {pid: t for t, ids in members.items() for pid in ids}
        order = sorted(teams)
        n_blocks = self.DEPTH_PER_TEAM * len(order)
        starts = []
        span = self.DEPTH_BLOCK * n_blocks
        for p0 in range(max(self.players[-1].R, len(d) - span - 65536), len(d) - span + 1):
            for k in range(n_blocks):
                q = p0 + self.DEPTH_BLOCK * k
                vals = struct.unpack_from("<80h", d, q + 2)
                if d[q:q + 2] != b"\0\0" or not vals[0] or {owner.get(v) for v in vals if v} != {order[k // self.DEPTH_PER_TEAM]}:
                    break
            else:
                starts.append(p0)
        if len(starts) != 1:
            raise CodecError(f"depth-chart region: {len(starts)} candidate starts")
        for k in range(n_blocks):
            teams[order[k // self.DEPTH_PER_TEAM]]["depth_at"].append(starts[0] + self.DEPTH_BLOCK * k)
        return teams

    def _replace_ids(self, start, count, old, new):
        for k in range(count):
            off = start + 2 * k
            if self._i16(off) == old:
                struct.pack_into("<h", self.data, off, new)

    def swap_teams(self, a, b):
        """Exchange team membership of players a and b (different teams). File length is unchanged."""
        ta, tb = a.values["Team"], b.values["Team"]
        if ta < 1 or tb < 1 or ta == tb:
            raise CodecError(f"swap needs two rostered players on different teams (got {ta}, {tb})")
        teams = self.teams()
        for t, old, new in ((ta, a.id, b.id), (tb, b.id, a.id)):
            info = teams[t]
            self._replace_ids(info["roster_at"], info["size"], old, new)
            self._replace_ids(info["lineup_at"], self.LINEUP_SLOTS, old, new)
            for blk in info["depth_at"]:
                self._replace_ids(blk + 2, 80, old, new)
        for field_name in ("Team", "Team1", "Team2"):
            self.set(a, field_name, tb)
            self.set(b, field_name, ta)

    def _slots(self, pl):
        """Map every editable field name to its absolute int16 offset for this player."""
        slots = {name: pl.bio_at + 2 * i for i, name in enumerate(BIO_INTS) if not name.startswith("_")}
        slots.update({name: pl.E + off for name, off in E_FIELDS.items()})
        slots.update({"Team1": pl.T1, "Team2": pl.T1 + 166})
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
            if (a.S, a.R, a.id) != (b.S, b.R, b.id) or a.values != b.values:
                tmp.unlink()
                raise CodecError(f"verification failed for {a.name}")
        try:
            check.teams()  # rosters, lineups and depth charts must agree with every player's team field
        except CodecError:
            tmp.unlink()
            raise
        tmp.replace(self.path)
