"""The off-drive backup: a copy nobody verified is a story about a backup.

WHAT IT IS FOR. `league.dat` IS the universe and FBPB3 keeps no other copy of it. Sim Week backs
a save up before it writes, which guards against a bad write and not at all against the drive
those backups sit on - they live beside the saves on C:, 288 folders of them.

THE TWO THINGS THAT MUST HOLD, because both fail silently:

  a copy that did not survive is not a backup      hashes are taken at the source and again at
                                                   the destination, and a mismatch throws the
                                                   whole snapshot away rather than keeping it
  a half-written snapshot is worse than none       it looks like a backup in every listing, and
                                                   nobody finds out until a restore

    python tests/test_offsite_backup.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import offsite_backup as ob  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp(prefix="cv-backup-"))
    try:
        src = tmp / "src"
        src.mkdir()
        (src / "league.dat").write_bytes(b"universe" * 1000)
        (src / ".env").write_text("SECRET=x", encoding="utf-8")
        items = [("CV_Test/league.dat", src / "league.dat"), ("repo/.env", src / ".env")]
        dest = tmp / "dest"

        real_sources = ob.sources
        ob.sources = lambda: items
        try:
            # ---- a good snapshot -------------------------------------------------------
            folder = ob.take(dest=dest, keep=5, log=lambda *_: None)
            assert folder and folder.exists(), "no snapshot was written"
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            assert len(manifest["files"]) == 2, manifest
            assert all(f["sha256"] for f in manifest["files"]), "a file went in without a hash"
            copied = folder / "CV_Test__league.dat"
            assert copied.read_bytes() == (src / "league.dat").read_bytes(), "the copy differs"

            # ---- a copy that does not survive is refused, and leaves nothing behind -------
            before = len(ob.snapshots(dest))
            real_copy = shutil.copy2

            def lying_copy(a, b, *args, **kw):
                real_copy(a, b, *args, **kw)
                Path(b).write_bytes(b"corrupted")      # the disk "lost" it after the write
                return b

            shutil.copy2 = lying_copy
            try:
                out = ob.take(dest=dest, keep=5, log=lambda *_: None)
            finally:
                shutil.copy2 = real_copy
            assert out is None, "a snapshot whose copy did not match was kept"
            assert len(ob.snapshots(dest)) == before, \
                "the failed snapshot was left on disk, where it looks exactly like a good one"

            # ---- pruning keeps the newest N ----------------------------------------------
            for _ in range(4):
                ob.take(dest=dest, keep=3, log=lambda *_: None)
            kept = ob.snapshots(dest)
            assert len(kept) == 3, f"keep=3 left {len(kept)}"
            assert kept == sorted(kept, key=lambda d: d.name), "snapshots must be oldest first"

            # ---- and --check tells the truth about how fresh it is ------------------------
            assert ob.check(dest=dest, log=lambda *_: None) == 0, "a fresh backup read as stale"

            old = json.loads((kept[-1] / "manifest.json").read_text(encoding="utf-8"))
            old["taken_at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
            for folder in kept:
                payload = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
                payload["taken_at"] = old["taken_at"]
                (folder / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
            assert ob.check(dest=dest, log=lambda *_: None) == 1, \
                "a four-day-old backup must report as stale - that is the whole point of --check"

            # nothing at all is a failure, not a pass
            assert ob.check(dest=tmp / "nowhere", log=lambda *_: None) == 1
        finally:
            ob.sources = real_sources
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # the real source list must name the three saves and the keys, or it is backing up nothing
    labels = [label for label, _ in ob.sources()]
    assert any("league.dat" in x for x in labels), f"no league.dat in the backup set: {labels}"
    print(f"OK  offsite backup: verifies every copy, discards a failed snapshot, prunes to the "
          f"newest few, and reports staleness ({len(labels)} files in the live set)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
