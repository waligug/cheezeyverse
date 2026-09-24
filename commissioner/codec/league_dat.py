"""Read/write player records in an FBPB3 league.dat (in-place edits only; file layout never changes).

Every field is int16 EXCEPT the seven contract salaries, which are int32 - see CONTRACT_FROM_T1.

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
import contextlib
import struct
import time
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

# ---- money ---------------------------------------------------------------------------------
# Contract1..Contract7: seven consecutive int32 SALARIES, one per remaining year, at a fixed
# offset from T1. THIS IS THE ONLY 32-BIT FIELD IN THE RECORD - everything else here is int16,
# which is why it gets its own accessors instead of a `_slots` entry. Routing it through `set()`
# would pack it as int16 and silently truncate: 1000000 would land as 16960.
#
# HOW THE OFFSET WAS FOUND, since it cannot be re-derived from the game's docs (there are none).
# 255 of the 530 pro players carried exactly 1000000 (`generate.IMPORT_CONTRACT`) and 275 carried
# nothing, so the value itself was the probe: every occurrence of int32 1000000 in the file - all
# 255 of them - fell inside a player record. There is NO fixed offset from S, E or R, because a
# variable-length block sits in front of it (E+252 holds for only 44% of players). It is fixed
# from T1, which `_find_team_fields` already locates between two VB6 count-2 array headers.
#
# Verified by reading all seven years for all 530 players and diffing against the game's own
# League Editor export: 530/530 exact, including the 275 all-zero ones. The first attempt read
# T1+113 and returned 256000000 - the same bytes off by one - which is exactly the kind of error
# that looks plausible without ground truth to check it against.
CONTRACT_YEARS = 7
CONTRACT_FROM_T1 = 114
CONTRACT_MAX = 2_000_000_000   # int32 headroom; the game's own imports use 1_000_000
# What a player signed by the codec is paid when he has no deal of his own. The same number
# `generate.IMPORT_CONTRACT` and `characters.IMPORT_CONTRACT` use, so one league has one token
# salary rather than three that drift.
SIGNING_CONTRACT = 1_000_000
# Longer than one year on purpose - see _ensure_paid. One year expires at the very next
# rollover, which turns a release-on-load into a release-one-offseason-later.
SIGNING_YEARS = 3
BIO_INTS = ["Height", "Weight", "_zero", "BirthMonth", "BirthDay", "BirthYear"]
E_FIELDS = {"Position": 18, "Team": 40, "Inactive": 46, "Exp": 82}  # Inactive: -1 = dressed out
POSITIONS = {1: "C", 2: "PF", 3: "SF", 4: "SG", 5: "PG"}

_NAME_RE = re.compile(rb"[\x03-\x30]\x00[^\x00-\x1f\x7f]{3,48}")


SEASON_DAY_TAIL = struct.pack("<4h", -1, -1, -1, 0)


def find_season_day(data, limit=None):
    """(day, year) from a league.dat's header bytes, or None unless exactly one match.

    Kept out of LeagueDat so it can be tested on bytes rather than needing a whole save. See
    LeagueDat.season_day for why it is a shape and not an offset.
    """
    stop = min(limit or len(data), len(data))
    hits, i = [], data.find(SEASON_DAY_TAIL, 36)
    while i != -1 and i < stop:
        at = i - 4
        if at >= 32 and data[at - 32:at] == bytes(32):
            day, year = struct.unpack_from("<2h", data, at)
            if 1 <= day <= 400 and 1900 <= year <= 2200:
                hits.append((day, year))
        i = data.find(SEASON_DAY_TAIL, i + 1)
    return hits[0] if len(hits) == 1 else None


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
    contract: list = field(default_factory=list)   # seven int32 salaries; see CONTRACT_FROM_T1
    id: int = 0
    T1: int = 0

    @property
    def dob(self):
        return f'{self.values["BirthMonth"]}/{self.values["BirthDay"]}/{self.values["BirthYear"]}'


_REHEARSED = False


@contextlib.contextmanager
def rehearsed_writes():
    """Allow depth/lineup writes on a 20-team save for the length of the block.

    ONLY for a write that has just been proven on a clone in the game itself (see
    seasonflow._place_rehearsed). It lifts the 20-team refusal and nothing else: the region must
    still be located exactly.
    """
    global _REHEARSED
    previous, _REHEARSED = _REHEARSED, True
    try:
        yield
    finally:
        _REHEARSED = previous


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

    def _id_marked(self, p, pid):
        """Does this record carry `pid` as its own (id, id) int16 pair before the name?"""
        for o in range(p.S - 60, p.S - 2, 2):
            a, b = struct.unpack_from("<2h", self.data, o)
            if a == b == pid:
                return True
        return False

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

        # Drop the league's TEAM-ID list. _roster_arrays accepts any VB6 array of distinct
        # positive int16 behind a leading zero, and one of those is the league's own list of
        # team ids - 20 entries in pro, 16 in prep and college. Arrays are matched to teams by
        # SIZE in file order and the team list comes first, so the moment the FIRST team happens
        # to hold exactly as many players as the league has teams, that list is taken as its
        # roster. The AI padded pro's team 5 to exactly 20 and the save stopped parsing.
        #
        # Matched on the exact set of team ids, not on any "looks like small contiguous ints"
        # rule - that heuristic also throws away a genuine roster and breaks the match entirely.
        team_ids = sorted(counts)
        arrays = [a for a in arrays if sorted(a[2]) != team_ids]
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
        # Assign each team's ids to that team's records, in file order, rather than zipping the
        # whole league's records against the whole league's ids at once.
        #
        # The global zip needed record order to match id order ACROSS the league, and that held
        # until the AI signed eighty free agents in one week: CV_Pro then stopped parsing
        # altogether with "Wilford Everhart would take id 20 from another team", which blocked
        # every read and write of that save - sims, snapshots, the roster guard, the offseason.
        #
        # This needs only the far weaker property that WITHIN one team the records are in
        # ascending id order, which is the same thing the roster arrays themselves rely on. When
        # the global order does hold, both methods agree, and the check below says so - so a save
        # that used to parse still parses to exactly the same ids.
        cursor = {t: 0 for t in by_team}
        for p in rostered:
            t = p.values["Team"]
            pool = by_team[t]
            k = cursor[t]
            if k >= len(pool):
                raise CodecError(
                    f"team {t} has {len(pool)} ids in its roster array but more rostered records")
            p.id = pool[k]
            cursor[t] = k + 1

        # Sanity-check the assignment against each record's own (id, id) marker, PER TEAM.
        #
        # Not per player: the marker is about 94% reliable on a long-aged save (244 of 260 on
        # Chung), so a single disagreement means nothing and failing on one would refuse to read
        # perfectly good files. But when a team's array is the WRONG array the mismatch is total
        # - every one of its players gets an id from somewhere else - and that is the case worth
        # stopping for, because wrong ids are assigned silently and the next write edits the
        # wrong players. A whole team scoring zero is the signature.
        for t, pool in by_team.items():
            mine = [p for p in rostered if p.values["Team"] == t]
            if len(mine) < 5:
                continue
            if not any(self._id_marked(p, p.id) for p in mine):
                raise CodecError(
                    f"team {t}'s {len(mine)} players were all assigned ids that their own records "
                    f"do not carry ({pool[:4]}...) - that array is not this team's roster")
        if [p.id for p in rostered] != ids:
            # Not an error. It means this save's records are not in global id order, which is
            # exactly the case the old code could not read at all. Worth knowing it happened.
            self._global_id_order = False
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
            if found is None:
                # No free number between its neighbours. That used to raise StopIteration out of
                # a generator, which says nothing at all about what went wrong.
                found = next((v for v in range(lo + 1, hi) if v not in taken), None)
            if found is None:
                raise CodecError(
                    f"no id left for {p.name}: the gap between {lo} and {hi} is full. The roster "
                    "arrays are probably not the ones this file actually uses.")
            p.id = found
            taken.add(p.id)
        self.by_id = {p.id: p for p in players}
        self._roster_choice = dict(zip(sorted(counts), chosen))

    # ---- team structures -------------------------------------------------------------------
    # Membership lives in three places per team (see CONVENTIONS.md): the roster dynamic array in the team
    # record, a 14-slot lineup array right after it, and 5 depth-chart blocks (81 int16) near the end of file.
    # E+40 on each player is a save-time copy; it is only used to identify which array belongs to which team.
    LINEUP_GAP, LINEUP_SLOTS = 20, 14
    DEPTH_BLOCK, DEPTH_PER_TEAM = 162, 5

    def season_day(self):
        """(day, year) the save is currently sitting on, or None when it cannot be read.

        FBPB3's own day counter: day 1 is the first day of the preseason and it runs on through
        the playoffs, restarting at 1 each new season. Nothing else in the project knew it, and
        head-to-head needs it - a character's games are only his from the day he was placed, and
        that day is otherwise reconstructed by adding up a log of past runs, which is wrong the
        moment a run is missing, rewound by restore_backup, or simmed by a tool that logs
        elsewhere.

        THERE IS NO FIXED OFFSET. The pair sits at 52026 in today's CV_Prep and 51958 in the same
        save a few runs ago, because records earlier in the file change length. So it is found by
        its shape: a long run of zeros, then the day, then the season year, then -1 -1 -1 0. That
        signature was checked against all 69 prep backups plus college and pro, 2026-09-19: every
        one had EXACTLY ONE match, and the days came out 1, 8, 15, 22, 29, 36, 57, 78, 99, 106,
        113, 120, 127, 155 - exactly the run log's own sequence, in all three leagues.

        Returns None rather than guessing when there is not exactly one match, because a wrong
        day silently hands somebody else's games to a character and looks entirely plausible.
        """
        return find_season_day(self.data, self.players[0].R if self.players else None)

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
            # Codec-edited files serialize the roster vector at its actual length. Native
            # offseason saves can retain a 20-slot camp area even when the count is 14/15. Try
            # the real vector boundary first, then the native fixed boundary; accept only a
            # lineup made entirely of this team's ids.
            candidates = [q + 10 + 2 * n + self.LINEUP_GAP, q + 72]
            valid = []
            for lineup_at in dict.fromkeys(candidates):
                lineup = struct.unpack_from(f"<{self.LINEUP_SLOTS}h", d, lineup_at)
                if {v for v in lineup if v} <= ids:
                    valid.append((lineup_at, lineup))
            if not valid:
                raise CodecError(f"team {t}: lineup at {lineup_at} holds non-roster ids {lineup}")
            lineup_at, lineup = valid[0]
            teams[t] = {"roster_at": q + 12, "size": n - 1, "lineup_at": lineup_at, "ids": set(ids), "depth_at": []}
        owner = {pid: t for t, ids in members.items() for pid in ids}
        order = sorted(teams)
        n_blocks = self.DEPTH_PER_TEAM * len(order)
        # Score the candidates rather than demanding a perfect match. A depth block can hold a
        # stale id - somebody released or retired since the chart was last rebuilt - and on a save
        # that has actually been played exactly one such id used to disqualify the whole region,
        # so teams() failed on every aged save.
        #
        # AN ID NOW ON ANOTHER TEAM IS ALSO NOT FATAL, and used to be. That rule assumed a stale
        # entry means a player who left the league, but FREE AGENCY moves players BETWEEN teams -
        # so once Full Finances was turned on in pro, a depth chart written before free agency was
        # full of men who had since signed elsewhere. Measured on the live 2031 pro save: 104 of
        # 4721 entries (2.2%) pointed at another team, and each one alone was enough to reject the
        # real region. `teams()` then failed on pro and only pro, scoring 0.000 on a garbage match,
        # and the post-sim tidy could not put a benched character back in the lineup. Prep and
        # college, which have no free agency, never saw it.
        #
        # So a wrong-team id now COSTS a point instead of disqualifying, and the 0.95 threshold is
        # what keeps the region honest - it has to be right about 19 entries in 20. That margin is
        # not close: on the same save the true region scores 0.978 and the best impostor 0.000.
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
                    total += 1
                    if owner.get(v) == want:
                        hits += 1
            if total > 0 and (best is None or hits / total > best[0]):
                best = (hits / total, p0)
        if best is None or best[0] < 0.95:
            raise CodecError("depth-chart region: nothing scored above 0.95"
                             + (f" (best {best[0]:.3f})" if best else ""))
        starts = [best[1]]
        for k in range(n_blocks):
            teams[order[k // self.DEPTH_PER_TEAM]]["depth_at"].append(starts[0] + self.DEPTH_BLOCK * k)
        # GOOD ENOUGH TO READ IS NOT GOOD ENOUGH TO WRITE. The 0.95 threshold lets a save whose
        # depth charts are full of players who have since changed teams be READ - pro after free
        # agency scores ~0.978 - and that stays. But on 2026-09-23 every codec write into pro's
        # depth charts at that certainty left a save FBPB3 could not open ("Run-time error '9':
        # Subscript out of range"): the roster guard's release/sign pass at 10:51, then the
        # weekly re-dress - 48 bytes, nothing else - and seven sims died loading it. So the
        # writers below refuse unless the region was located EXACTLY.
        self.depth_exact = best[0] >= 1.0
        self.depth_score = best[0]
        return teams

    def _require_exact_depth(self, what):  # noqa: C901 - see _REHEARSED below
        """Refuse a depth-chart/lineup write unless teams() found the region with certainty."""
        teams = self.teams()
        # AND NEVER ON THE 20-TEAM (PRO) SAVE, for now. Both writes that broke CV_Pro were depth/
        # lineup writes, and the second landed in a region located EXACTLY (1.000) - so certainty
        # of location is not the whole story, and until it is understood the only safe answer is
        # not to write there. Prep and college (16 teams) take the same writes without trouble.
        # A character already on a pro roster keeps playing; only the re-dress is skipped.
        #
        # The one way through is `rehearsed_writes()`, which seasonflow holds only after the SAME
        # write has been made on a clone of the save and FBPB3 has loaded that clone and simmed a
        # day on it. That is how Dodger Manson went back on LCH after the 2032 rollover.
        if len(teams) >= 20 and not _REHEARSED:
            raise CodecError(f"refusing to {what} on a {len(teams)}-team save: codec writes to "
                             "its depth charts have twice left it unloadable (2026-09-23)")
        # EXACTNESS IS PRO'S RULE, NOT EVERYONE'S. It was added for every league on 2026-09-23
        # in answer to the pro breakage alone, and that evening it stopped the 2033 offseason
        # dead in PREP: the game's own rollover retires and releases players, their ids linger
        # in the depth charts, and the true region scored 0.986 - so age-out could not release
        # the over-age class. Prep and college wrote at >= 0.95 (teams() refuses below that) for
        # their whole history without one unloadable save.
        #
        # ON PRO THE REHEARSAL IS THE CHECK, not the score. Both of the morning's breakages were
        # at an EXACT 1.000, so exactness never predicted safety - and after the 2033 rollover
        # pro read 0.998 (players free agency had moved), which left Chris Zimmer, the #1 pick,
        # unplaceable. Outside a rehearsal pro is refused outright above; inside one, the clone
        # has already been loaded and simmed with this very write.
        if len(teams) >= 20 and not _REHEARSED and not getattr(self, "depth_exact", False):
            raise CodecError(f"refusing to {what}: the depth-chart region was located at "
                             f"{getattr(self, 'depth_score', 0):.3f}, not exactly, and a write "
                             "there has left saves FBPB3 cannot load")

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

    # ---- structural edits -------------------------------------------------------------------
    def _reparse(self):
        ids = [p.id for p in self.players]
        self._id_override = ids
        try:
            self.players = self._parse()
        finally:
            self._id_override = None

    def _splice(self, at, remove, insert=b""):
        """Apply a byte-level edit and re-parse. Record order and count never change, so ids carry over
        positionally - a released player keeps his id even though he is no longer in any roster array."""
        self.data[at:at + remove] = insert
        self._reparse()

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

    def rename_many(self, changes):
        """Rename many players with one re-parse.

        Applying edits from the end of the file backwards keeps every earlier player offset
        valid even when names change length.  The intake used to re-parse the entire save after
        each of roughly fifty names.
        """
        edits = []
        for pl, first, last in changes:
            for part in (first, last):
                if not part or len(part) > 40 or not all(32 <= ord(c) < 127 for c in part):
                    raise CodecError(f"unusable name part {part!r}")
            end = pl.S
            for _ in range(3):
                end += 2 + self._u16(end)
            value = b"".join(struct.pack("<H", len(s)) + s.encode("latin-1")
                             for s in (f"{first} {last}", first, last))
            edits.append((pl.S, end - pl.S, value))
        for at, remove, value in sorted(edits, reverse=True):
            self.data[at:at + remove] = value
        if edits:
            self._reparse()

    def release(self, pl):
        """Remove a rostered player from his team (he becomes a free agent). Roster array shrinks by one."""
        self._require_exact_depth("release a player")
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

    def release_many(self, players):
        """Release several players from one team with one roster splice and one re-parse.

        Age-out used to call :meth:`release` once per player. Each call re-parses every player
        record, so releasing a graduating class cost minutes. Team structures can be updated in
        one pass: remove every id from the lineup/depth chart, replace the roster array once,
        and then re-parse once. The individual fields are fixed before the splice while all
        player offsets are still valid.
        """
        self._require_exact_depth("release players")
        players = list(players)
        if not players:
            return
        teams = {p.values["Team"] for p in players}
        if len(teams) != 1 or next(iter(teams)) < 1:
            raise CodecError("release_many needs rostered players from one team")
        self.release_groups({next(iter(teams)): players})

    def release_groups(self, groups):
        """Release batches from several teams and re-parse once."""
        self._require_exact_depth("release players")
        groups = {int(t): list(players) for t, players in groups.items() if players}
        if not groups:
            return
        infos = self.teams()
        edits = []
        for t, players in groups.items():
            if t < 1 or any(p.values["Team"] != t for p in players):
                raise CodecError("release_groups contains a player on the wrong team")
            info = infos[t]
            remove = {p.id for p in players}
            current = struct.unpack_from(f"<{info['size']}h", self.data, info["roster_at"])
            others = [i for i in current if i not in remove]
            if not others:
                raise CodecError(f"team {t} would be empty")
            for blk in info["depth_at"]:
                vals = list(struct.unpack_from("<80h", self.data, blk + 2))
                used = [v for v in vals if v and v not in remove]
                sub = max(set(used), key=used.count) if used else others[0]
                vals = [sub if v in remove else v for v in vals]
                struct.pack_into("<80h", self.data, blk + 2, *vals)
            lineup = [v for v in struct.unpack_from(f"<{self.LINEUP_SLOTS}h", self.data,
                                                    info["lineup_at"]) if v not in remove]
            struct.pack_into(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"],
                             *(lineup + [0] * (self.LINEUP_SLOTS - len(lineup))))
            for pl in players:
                self.set(pl, "Team", -1)
                self.set(pl, "Team1", -1)
            self._roster_count_add(info, -len(remove))
            edits.append((info["roster_at"], 2 * info["size"],
                          struct.pack(f"<{len(others)}h", *others)))
        for at, remove, value in sorted(edits, reverse=True):
            self.data[at:at + remove] = value
        self._reparse()

    def _ensure_paid(self, pl, years=None):
        """A rostered player must have a contract. Never re-prices a man who already has one.

        A ROSTERED PLAYER MUST HAVE A CONTRACT, and a signing function is the only place that can
        guarantee it. Under Full Finances the game RELEASES a contract-less player as the league
        loads, so signing one is a move that undoes itself - and the pool these sign FROM is 150
        free agents of whom every single one is bare.

        The default term is deliberately longer than one year. A one-year deal expires at the
        very next rollover's FREE AGENCY stage, so a backfill signed on a single year would be
        gone again one offseason later - slower than being released on load, and harder to see.
        """
        if any(self.contract_of(pl)):
            return False
        n = SIGNING_YEARS if years is None else years
        try:
            n = max(1, min(int(n), CONTRACT_YEARS))
        except (TypeError, ValueError):
            n = SIGNING_YEARS
        self.set_contract(pl, [SIGNING_CONTRACT] * n)
        return True

    def sign(self, pl, t, minutes=True, years=SIGNING_YEARS):
        """Add a free agent / draft-pool player to team t. Roster array grows by one.

        A player who is only on the roster never appears in a box score, so by default he also takes over the
        depth-chart minutes of the least-used player at his position (depth block k holds position k+1)."""
        self._require_exact_depth("sign a player")
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
        # A ROSTERED PLAYER MUST HAVE A CONTRACT, and this is the only place that can guarantee
        # it. Under Full Finances the game RELEASES a contract-less player as the league loads,
        # so signing one is a move that undoes itself on the next load - and the pool this signs
        # FROM is 150 free agents of whom every single one is bare. The roster guard backfills a
        # short team from exactly that pool every publish, so the league would have shed players
        # and re-signed them for ever, one team at a time, with nothing reporting it.
        #
        # It belongs here rather than in ageout and protect_rosters because it is a property of
        # the FILE, not of anyone's intent: there is no correct way to put a man on a roster
        # without paying him. An existing deal is never touched - he keeps what he had.
        self._ensure_paid(pl, years)
        self._roster_count_add(info, +1)
        self._splice(info["roster_at"] + 2 * info["size"], 0, struct.pack("<h", pl.id))

    def sign_many(self, assignments, minutes=True, years=SIGNING_YEARS):
        """Sign ``(player, team)`` assignments and re-parse the save once."""
        self._require_exact_depth("sign players")
        assignments = list(assignments)
        if not assignments:
            return
        teams = self.teams()
        sizes = {t: info["size"] for t, info in teams.items()}
        by_team = {}
        for pl, t in assignments:
            if pl.values["Team"] >= 1:
                raise CodecError(f"{pl.name} is already on team {pl.values['Team']}")
            info = teams[t]
            lineup = list(struct.unpack_from(f"<{self.LINEUP_SLOTS}h", self.data,
                                             info["lineup_at"]))
            blk = info["depth_at"][pl.values["Position"] - 1]
            used = [v for v in struct.unpack_from("<80h", self.data, blk + 2) if v]
            weakest = min(set(used), key=used.count) if used else None
            if minutes and weakest is not None:
                self._replace_ids(blk + 2, 80, weakest, pl.id)
            if 0 in lineup:
                lineup[lineup.index(0)] = pl.id
            elif minutes and weakest in lineup:
                lineup[lineup.index(weakest)] = pl.id
            struct.pack_into(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"], *lineup)
            for field_name in ("Team", "Team1", "Team2"):
                self.set(pl, field_name, t)
            if minutes:
                self.set(pl, "Inactive", 0)
            # THIS IS THE PATH PRODUCTION USES. sign() carries the same guarantee and is called
            # by nothing but tests: protect_rosters' backfill (every sim week) and ageout's
            # recycled-body intake both come through here. Leaving it out meant the guard in
            # sign() was unreachable and every backfilled player was RELEASED on the next load
            # under Full Finances - teams short, the guard re-signing them next week, for ever.
            self._ensure_paid(pl, years)
            by_team.setdefault(t, []).append(pl.id)
            sizes[t] += 1
        edits = []
        for t, ids in by_team.items():
            info = teams[t]
            self._roster_count_add(info, len(ids))
            edits.append((info["roster_at"] + 2 * info["size"], 0,
                          struct.pack(f"<{len(ids)}h", *ids)))
        for at, remove, value in sorted(edits, reverse=True):
            self.data[at:at + remove] = value
        self._reparse()

    def dress(self, pl, take_minutes_from_weakest=True):
        """Put an already-rostered player in the lineup and on the depth chart.

        A reserve slot is created deliberately unused: floor ratings and no depth-chart minutes.
        When a character takes one over he inherits that emptiness, so however good his ratings
        are he starts the season dressed as nobody and never appears in a box score. `sign` does
        this work for a free agent joining a roster; a character claiming a slot needs the same
        thing without the roster move.

        Returns True if anything changed.
        """
        self._require_exact_depth("dress a player")
        t = pl.values["Team"]
        if t < 1:
            raise CodecError(f"{pl.name} is not on a team")
        info = self.teams()[t]
        changed = False
        if pl.values.get("Inactive") != 0:
            self.set(pl, "Inactive", 0)
            changed = True

        lineup = list(struct.unpack_from(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"]))
        if pl.id not in lineup:
            if 0 in lineup:
                lineup[lineup.index(0)] = pl.id
                changed = True
            struct.pack_into(f"<{self.LINEUP_SLOTS}h", self.data, info["lineup_at"], *lineup)

        blk = info["depth_at"][pl.values["Position"] - 1]
        used = [v for v in struct.unpack_from("<80h", self.data, blk + 2) if v]
        if pl.id not in used and take_minutes_from_weakest and used:
            weakest = min(set(used), key=used.count)
            # take half of the least-used player's slots, not all of them: a fourteen year old
            # earning a rotation spot is the story, replacing somebody outright is not.
            share = [i for i, v in enumerate(struct.unpack_from("<80h", self.data, blk + 2))
                     if v == weakest]
            for i in share[: max(1, len(share) // 2)]:
                struct.pack_into("<h", self.data, blk + 2 + 2 * i, pl.id)
            changed = True
        return changed

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
        pl.contract = self.contract_of(pl)

    # ---- money -----------------------------------------------------------------------------
    def _contract_at(self, pl):
        at = pl.T1 + CONTRACT_FROM_T1
        if at < 0 or at + 4 * CONTRACT_YEARS > len(self.data):
            raise CodecError(f"contract block for {pl.name} falls outside the file")
        return at

    def contract_of(self, pl):
        """The seven yearly salaries. All zeros means NO CONTRACT, which is not the same as a
        contract worth nothing: with Full Finances the game RELEASES a player with no contract
        the moment the league loads (CONVENTIONS.md), which is why this is worth reading."""
        return list(struct.unpack_from(f"<{CONTRACT_YEARS}i", self.data, self._contract_at(pl)))

    def set_contract(self, pl, salaries):
        """Write the contract years. Shorter input is zero-filled, so `[1000000]` is a one-year deal.

        Values are ints and are range-checked rather than truncated: this is the one field in the
        record wide enough to be silently mangled by a careless write, and it decides whether a
        player survives the next load."""
        vals = list(salaries or [])
        if len(vals) > CONTRACT_YEARS:
            raise CodecError(f"a contract runs at most {CONTRACT_YEARS} years, got {len(vals)}")
        vals += [0] * (CONTRACT_YEARS - len(vals))
        out = []
        for i, v in enumerate(vals):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise CodecError(f"contract year {i + 1} is not a number: {v!r}") from None
            if not 0 <= v <= CONTRACT_MAX:
                raise CodecError(f"contract year {i + 1} = {v} out of range 0..{CONTRACT_MAX}")
            out.append(v)
        struct.pack_into(f"<{CONTRACT_YEARS}i", self.data, self._contract_at(pl), *out)
        pl.contract = out
        return out

    # ---- public API ------------------------------------------------------------------------
    def find(self, name, dob=None):
        hits = [p for p in self.players if p.name == name and (dob is None or p.dob == dob)]
        if len(hits) != 1:
            raise CodecError(f"{len(hits)} players match {name!r} dob={dob!r}")
        return hits[0]

    def set(self, pl, field_name, value):
        slots = self._slots(pl)
        if field_name not in slots:
            if str(field_name).startswith("Contract"):
                raise CodecError(f"{field_name} is a 32-bit salary; use set_contract(), because "
                                 "this writes int16 and would truncate it")
            raise CodecError(f"unknown field {field_name}")
        if field_name in RATINGS or field_name in POTENTIALS:
            if not 0 <= value <= RATING_MAX:
                raise CodecError(f"{field_name}={value} out of range 0..{RATING_MAX}")
        struct.pack_into("<h", self.data, slots[field_name], int(value))
        pl.values[field_name] = int(value)

    def save(self, backup_dir=None):
        """Write in place after verifying a fresh parse sees exactly the intended values."""
        if backup_dir:
            # Name the folder after the SAVE, not just the clock. Every league's file is called
            # league.dat, so a bare timestamp left 147 folders of identically named files with
            # no way to tell a Prep backup from a Pro one without parsing each candidate - which
            # is exactly the moment you least want to be guessing.
            who = self.path.parent.name or "unknown"
            dest = Path(backup_dir) / f'{datetime.now().strftime("%Y%m%d-%H%M%S")}-{who}'
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
            # Money is verified on the same footing as everything else. A truncated or
            # misplaced salary does not look wrong in the file; it looks wrong weeks later,
            # when the player is quietly released on a load.
            if a.contract != b.contract:
                tmp.unlink()
                raise CodecError(f"contract verification failed for {a.name}: "
                                 f"wrote {a.contract}, read back {b.contract}")
        try:
            check.teams()  # rosters, lineups and depth charts must agree with every player's team field
        except CodecError:
            tmp.unlink()
            raise
        # A bounded retry, because the failure is environmental rather than ours: a virus
        # scanner or the search indexer opening a freshly written 4 MB file holds it for a
        # moment and the replace comes back WinError 5. That is exactly what happened after
        # one Sim Week - the post-sim roster tidy lost its write and prep went untidied,
        # reported only as a line in the log. Every writer goes through this one call, so
        # one retry here covers the apply, the guard, the scrub, stamping and the offseason.
        # It cannot change semantics: the replace either succeeds or raises as before.
        delay = 0.2
        for attempt in range(10):
            try:
                tmp.replace(self.path)
                break
            except PermissionError as exc:
                # Say so even when the next attempt works. "The retry logged nothing" was offered
                # as evidence that the lock had not recurred, and it cannot be: a silent success
                # is indistinguishable from never having fired. One line per attempt is what
                # makes the absence of lines mean something.
                print(f"league.dat is locked ({exc.__class__.__name__}), retrying "
                      f"{attempt + 1}/10 in {delay:.1f}s: {self.path.parent.name}", flush=True)
                if attempt == 9:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 1.0)
