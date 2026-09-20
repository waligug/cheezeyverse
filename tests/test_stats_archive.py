"""All-time career stats: kept forever, and never welded onto the wrong person.

WHY AN ARCHIVE AT ALL. `LeagueOutput.mdb` is a snapshot of the save as it stands, and the save
retires people. Four pro players aged 34-35 were retired by the 2026 rollover and are already
gone from the file entirely - so a leaderboard built from the current export is a leaderboard of
players who happen to still be active, which is the opposite of an all-time record. A season is
written down once and then never rewritten.

THE FAILURE THIS GUARDS AGAINST. `SeasonStats.ID` is what the game gives us and `HistoricalID`
is empty in every row. Ids are stable across seasons today - 247 of pro's appear in both 2026
and 2027 - but retirement frees an id, and nothing promises a draftee will not be handed a dead
man's number. Summing by id alone would silently weld two people into one career, and the result
would look entirely plausible: a 34-year-old's totals with a rookie's, under whichever name got
written last. So identity is NAME AND BIRTHDAY, the same pair the codec finds people by, and two
seasons that disagree about who an id belonged to are two different careers.

    python tests/test_stats_archive.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import statsarchive as sa  # noqa: E402


def row(id, name, dob, **stats):
    base = {f: 0 for f in sa.COUNTING}
    base.update({"id": str(id), "name": name, "dob": dob, "team": "SAS", "age": 25})
    base.update(stats)
    return base


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cv-stats-"))
    real = sa.ARCHIVE
    sa.ARCHIVE = tmp
    try:
        # ---- a season is written once and then left alone ----------------------------------
        assert sa.save("prep", 2026, [row(1, "Ann Blake", "1/1/2010", Games=10, Points=100)])
        assert sa.save("prep", 2026, [row(1, "Ann Blake", "1/1/2010", Games=99, Points=999)]) is None, \
            "a finished season was rewritten - that is the one thing this must never do"
        kept = json.loads((tmp / "stats-prep-2026.json").read_text(encoding="utf-8"))
        assert kept["players"][0]["Points"] == 100, "the original season did not survive"
        assert sa.save("prep", 2026, [row(1, "Ann Blake", "1/1/2010", Games=12, Points=120)],
                       overwrite=True), "an explicit overwrite must be possible, for repairs"

        # ---- totals add up across seasons --------------------------------------------------
        sa.save("prep", 2027, [row(1, "Ann Blake", "1/1/2010", Games=20, Points=400, Rebounds=50)])
        careers = sa.careers("prep")
        ann = next(c for c in careers if c["name"] == "Ann Blake")
        assert ann["Points"] == 520, f"career points should be 120+400: {ann['Points']}"
        assert ann["Games"] == 32, ann["Games"]
        assert ann["first_season"] == 2026 and ann["last_season"] == 2027, ann
        assert len(ann["seasons"]) == 2, "the year-by-year line was lost"
        assert ann["ppg"] == round(520 / 32, 1), f"ppg must come from the totals: {ann['ppg']}"

        # ---- A RECYCLED ID IS NOT A CAREER ---------------------------------------------------
        # Same id in a later season, different person. If these merge, a rookie inherits a
        # retired man's numbers and nothing anywhere looks wrong.
        sa.save("pro", 2026, [row(7, "Old Timer", "3/3/1992", Games=80, Points=1600)])
        sa.save("pro", 2027, [row(7, "Fresh Rookie", "5/5/2008", Games=70, Points=700)])
        pro = sa.careers("pro")
        assert len(pro) == 2, f"a recycled id was merged into one career: {[c['name'] for c in pro]}"
        old = next(c for c in pro if c["name"] == "Old Timer")
        new = next(c for c in pro if c["name"] == "Fresh Rookie")
        assert old["Points"] == 1600 and new["Points"] == 700, (old["Points"], new["Points"])
        assert old["Games"] == 80 and new["Games"] == 70

        # the same person keeps merging even if his id changes - identity is the name and dob
        sa.save("college", 2026, [row(11, "Moved On", "2/2/2009", Games=30, Points=300)])
        sa.save("college", 2027, [row(99, "Moved On", "2/2/2009", Games=30, Points=300)])
        moved = sa.careers("college")
        assert len(moved) == 1, "a changed id split one career in two"
        assert moved[0]["Points"] == 600 and sorted(moved[0]["ids"]) == ["11", "99"], moved[0]

        # ---- leaders ------------------------------------------------------------------------
        board = sa.leaders(pro, "Points", count=5)
        assert [c["name"] for c in board] == ["Old Timer", "Fresh Rookie"], board
        assert sa.leaders(pro, "Points", min_games=75)[0]["name"] == "Old Timer"
        assert len(sa.leaders(pro, "Points", min_games=100)) == 0, "min_games is not filtering"

        # ---- a damaged season must not take the others down ---------------------------------
        (tmp / "stats-prep-2028.json").write_text("{not json", encoding="utf-8")
        still = sa.careers("prep")
        assert still, "one unreadable year destroyed the whole archive"
        assert next(c for c in still if c["name"] == "Ann Blake")["Points"] == 520

        # ---- a row with no name is not attributed to anybody --------------------------------
        # This is what the encoding bug produced: 390 player seasons with every name blank.
        # Summing those together would have created one enormous nameless career.
        sa.save("prep", 2029, [row(1, "", "1/1/2010", Games=50, Points=5000),
                               row(2, "", "2/2/2010", Games=50, Points=5000)])
        after = sa.careers("prep")
        assert not any(not c["name"] for c in after), "nameless rows became a career"
        assert next(c for c in after if c["name"] == "Ann Blake")["Points"] == 520, \
            "nameless rows contaminated a real career"

        print(f"OK  stats archive: seasons are written once, totals add up, a recycled id stays "
              f"two careers, and nameless rows are dropped")
        return 0
    finally:
        sa.ARCHIVE = real
        for p in tmp.glob("*"):
            p.unlink()
        tmp.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
