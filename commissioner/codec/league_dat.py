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
RATING_MAX = 150  # the game stores ratings above 100 (a real player file reaches 139)
BIO_INTS = ["Height", "Weight", "_zero", "BirthMonth", "BirthDay", "BirthYear"]
E_FIELDS = {"Position": 18, "Team": 40, "Inactive": 46, "Exp": 82}  # Inactive: -1 = dressed out
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
    _id_override = None  # set during a splice so re-parsed records keep their ids

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
        """First offset in the record whose 18 current + 12 potential ratings are plausible and which is
        followed by the record's `<int> 0 0 <double>` tail. A run of zeros used to precede the block, but
        once a save has played a season the game writes per-season archive rows there
        ([year, 18 ratings, height, weight]), so the block is anchored on what follows it instead."""
        d = self.data
        for q in range(max(start, 52), min(stop, len(d) - POT_OFFSET - 24)):
            cur = struct.unpack_from("<18h", d, q)
            if not any(cur) or not all(0 <= v <= RATING_MAX for v in cur):
                continue
            if not 0 <= self._i16(q + 36) <= RATING_MAX:
                continue
            if not all(0 <= v <= RATING_MAX for v in struct.unpack_from("<12h", d, q + POT_OFFSET)):
                continue
            zeros_before = d[q - 48:q] == b"\0" * 48 and d[q - 52:q - 48] == b"\x01\x00\x01\x00"
            # archive row: int16 season, 18 ratings, height, weight (42 bytes, ending at q)
            archive_before = (1900 <= self._i16(q - 42) <= 2100 and 55 <= self._i16(q - 4) <= 100
                              and 100 <= self._i16(q - 2) <= 400
                              and all(0 <= v <= RATING_MAX for v in struct.unpack_from("<18h", d, q - 40)))
            if zeros_before or archive_before:
                yield q

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
            # a record is only accepted when a ratings block and its two team fields both line up,
            # which also filters out name triples that are not player records (staff, awards, news)
            for r in self._find_ratings(p, stop):
                try:
                    t1 = self._find_team_fields(p, r)
                except CodecError:
                    continue
                pl = Player(S=s, E=p, R=r, bio_at=bio_at, name=full, first=first, last=last)
                pl.T1 = t1
                self._read_values(pl)
                players.append(pl)
                break
        self._assign_ids(players)
        return players

    def _roster_arrays(self, limit=None):
        """Every VB6 `01 00 | int32 n | int32 0` array before the first player record whose elements look like
        a roster (leading 0, then distinct positive ids). Team records appear in ascending team-id order."""
        d = self.data
        if limit is None:
            limit = self.players[0].S
        out, q = [], self.data.find(b"\x01\x00", 0, limit)
        while q != -1:
            if d[q + 4:q + 10] == b"\0" * 6:
                n = self._u16(q + 2)
                if 2 <= n <= 40:
                    vals = struct.unpack_from(f"<{n}h", d, q + 10)
                    if vals[0] == 0 and all(0 < v < 30000 for v in vals[1:]) and len(set(vals[1:])) == n - 1:
                        out.append((q, n, list(vals[1:])))
            q = d.find(b"\x01\x00", q + 1, limit)
        return out

    def _assign_ids(self, players):
        """Player ids are not stored at any fixed offset and are not contiguous once a save has aged, so they
        are recovered from the team roster arrays: records are written in ascending id order, the teams'
        arrays appear in ascending team-id order, and every player's own Team field says which team he is on.
        Matching those three facts pins each rostered record's id; the rest are interpolated."""
        override = getattr(self, "_id_override", None)
        if override and len(override) == len(players):
            for p, pid in zip(players, override):
                p.id = pid
            self.by_id = {p.id: p for p in players}
            return
        rostered = [p for p in players if p.values["Team"] >= 1]
        counts = {}
        for p in rostered:
            counts[p.values["Team"]] = counts.get(p.values["Team"], 0) + 1
        want = [counts[t] for t in sorted(counts)]
        arrays = self._roster_arrays(limit=players[0].S)
        chosen, i = [], 0
        for size in want:  # walk the arrays in file order, taking the next one with the right size
            while i < len(arrays) and arrays[i][1] - 1 != size:
                i += 1
            if i == len(arrays):
                raise CodecError(f"no roster array of size {size} left ({len(chosen)}/{len(want)} teams matched)")
            chosen.append(arrays[i])
            i += 1
        by_team = {t: sorted(a[2]) for t, a in zip(sorted(counts), chosen)}
        ids = sorted(v for a in chosen for v in a[2])
        if len(ids) != len(rostered) or len(set(ids)) != len(ids):
            raise CodecError(f"roster arrays hold {len(ids)} ids for {len(rostered)} rostered players")
        for p, pid in zip(rostered, ids):  # records are in ascending id order
            if pid not in by_team[p.values["Team"]]:
                raise CodecError(f"{p.name} (team {p.values['Team']}) would take id {pid} from another team")
            p.id = pid
        taken = set(ids)
        for i, p in enumerate(players):  # free agents and draft-pool players sit between rostered records
            if p.id:
                continue
            lo = max((q.id for q in players[:i] if q.id), default=0)
            hi = min((q.id for q in players[i + 1:] if q.id), default=lo + 1000)
            # the record carries its own id as an (id, id) int16 pair shortly before the name
            found = None
            for o in range(p.S - 60, p.S - 2, 2):
                a, b = struct.unpack_from("<2h", self.data, o)
                if a == b and lo < a < hi and a not in taken:
                    found = a
            p.id = found if found else next(v for v in range(lo + 1, hi) if v not in taken)
            taken.add(p.id)
        self.by_id = {p.id: p for p in players}
        self._roster_choice = dict(zip(sorted(counts), chosen))

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
        # Score the candidates rather than demanding a perfect match. A depth block can hold a
        # stale id - somebody released or retired since the chart was last rebuilt - and on a save
        # that has actually been played exactly one such id used to disqualify the whole region,
        # so teams() failed on every aged save. An id belonging to ANOTHER team is still fatal; an
        # id belonging to nobody is not.
        span = self.DEPTH_BLOCK * n_blocks
        best = None
        for p0 in range(max(self.players[-1].R, len(d) - span - 262144), len(d) - span + 1):
            hits = total = 0
            for k in range(n_blocks):
                q = p0 + self.DEPTH_BLOCK * k
                if d[q:q + 2] != b"\0\0":
                    total = -1
                    break
                vals = struct.unpack_from("<80h", d, q + 2)
                want = order[k // self.DEPTH_PER_TEAM]
                if not vals[0]:
                    total = -1
                    break
                for v in vals:
                    if not v:
                        continue
                    if owner.get(v) == want:
                        hits += 1
                        total += 1
                    elif owner.get(v) is None:
                        total += 1
                    else:
                        total = -1
                        break
                if total < 0:
                    break
            if total > 0 and (best is None or hits / total > best[0]):
                best = (hits / total, p0)
        if best is None or best[0] < 0.95:
            raise CodecError("depth-chart region: nothing scored above 0.95"
                             + (f" (best {best[0]:.3f})" if best else ""))
        starts = [best[1]]
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

    # ---- roster length changes (splice: bytes shift, everything is re-parsed) --------------------------
    def _splice(self, at, remove, insert=b""):
        """Apply a byte-level edit and re-parse. Record order and count never change, so ids carry over
        positionally - a released player keeps his id even though he is no longer in any roster array."""
        ids = [p.id for p in self.players]
        self.data[at:at + remove] = insert
        self._id_override = ids
        try:
            self.players = self._parse()
        finally:
            self._id_override = None

    def _roster_count_add(self, info, delta):
        header = info["roster_at"] - 12
        n = struct.unpack_from("<i", self.data, header + 2)[0]
        struct.pack_into("<i", self.data, header + 2, n + delta)

    def rename(self, pl, first, last):
        """Replace a player's name. The three name strings are re-encoded, so the record changes length."""
        for part in (first, last):
            if not part or len(part) > 40 or not all(32 <= ord(c) < 127 for c in part):
                raise CodecError(f"unusable name part {part!r}")
        end = pl.S
        for _ in range(3):
            end += 2 + self._u16(end)
        new = b"".join(struct.pack("<H", len(s)) + s.encode("latin-1") for s in (f"{first} {last}", first, last))
        self._splice(pl.S, end - pl.S, new)

    def release(self, pl):
        """Remove a rostered player from his team (he becomes a free agent). Roster array shrinks by one."""
        t = pl.values["Team"]
        if t < 1:
            raise CodecError(f"{pl.name} is not on a team ({t})")
        info = self.teams()[t]
        others = [i for i in info["ids"] if i != pl.id]
        if not others:
            raise CodecError(f"team {t} would be empty")
        for blk in info["depth_at"]:  # give his minutes to the most-used teammate in that block
            vals = struct.unpack_from("<80h", self.data, blk + 2)
            used = [v for v in vals if v and v != pl.id]
            sub = max(set(used), key=used.count) if used else others[0]
            self._replace_ids(blk + 2, 80, pl.id, sub)
        lineup = [v for v in struct.unpack_from(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"]) if v != pl.id]
        struct.pack_into(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"], *(lineup + [0] * (self.LINEUP_SLOTS - len(lineup))))
        self.set(pl, "Team", -1)
        self.set(pl, "Team1", -1)  # Team2 keeps the former team, as the game does for free agents
        elems = struct.unpack_from(f"<{info['size']}h", self.data, info["roster_at"])
        k = elems.index(pl.id)
        self._roster_count_add(info, -1)
        self._splice(info["roster_at"] + 2 * k, 2)

    def sign(self, pl, t, minutes=True):
        """Add a free agent / draft-pool player to team t. Roster array grows by one.

        A player who is only on the roster never appears in a box score, so by default he also takes over the
        depth-chart minutes of the least-used player at his position (depth block k holds position k+1)."""
        if pl.values["Team"] >= 1:
            raise CodecError(f"{pl.name} is already on team {pl.values['Team']}")
        info = self.teams()[t]
        lineup = list(struct.unpack_from(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"]))
        blk = info["depth_at"][pl.values["Position"] - 1]
        used = [v for v in struct.unpack_from("<80h", self.data, blk + 2) if v]
        weakest = min(set(used), key=used.count) if used else None
        if minutes and weakest is not None:
            self._replace_ids(blk + 2, 80, weakest, pl.id)
        if 0 in lineup:  # the 14-slot lineup is the active list; a player outside it dresses as Inactive
            lineup[lineup.index(0)] = pl.id
        elif minutes and weakest in lineup:
            lineup[lineup.index(weakest)] = pl.id
        struct.pack_into(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"], *lineup)
        for field_name in ("Team", "Team1", "Team2"):
            self.set(pl, field_name, t)
        if minutes:
            self.set(pl, "Inactive", 0)
        self._roster_count_add(info, +1)
        self._splice(info["roster_at"] + 2 * info["size"], 0, struct.pack("<h", pl.id))

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
            if not 0 <= value <= RATING_MAX:
                raise CodecError(f"{field_name}={value} out of range 0..{RATING_MAX}")
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
        if len(check.data) != len(self.data):
            tmp.unlink()
            raise CodecError("written size differs from buffer")
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
