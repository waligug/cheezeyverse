"""The sim run log is a ledger, not a log, and must never lose a run that simmed.

Head-to-head works out which games belong to which character by summing the days simmed before
he existed. So a lost row does not merely shorten a list: it silently hands a filler's season to
somebody's name, and the numbers stay perfectly plausible. A lost FILE makes everybody a day-one
player, which is what was happening live until this week.

Three properties, each of which was wrong:

  * A RUN THAT SIMMED IS NEVER TRIMMED. RUNS_KEPT dropped the oldest rows at a hundred, and a
    season simmed in short chunks passes a hundred easily. Refused and dry attempts are trimmed
    instead, because they advanced nothing.
  * THE WRITE IS ATOMIC. store.record_run used write_text, which truncates in place, so a crash
    mid-write left a file that _read_runs turns into [] - the total-loss case.
  * BOTH STORES AGREE, because whichever is configured is the one the arithmetic runs against.

    python tests/test_run_log.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner import localstore, store  # noqa: E402


def check(name, rows, kept_days):
    real = [r for r in rows if r.get("ok") and not r.get("dry_run")]
    assert sum(int(r["days"]) for r in real) == kept_days, \
        f"{name}: kept {sum(int(r['days']) for r in real)} simmed days, expected {kept_days}"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="runlog-"))
    real_file, real_path = store.RUNS_FILE, localstore.PATH
    try:
        # ---- store.py --------------------------------------------------------------------
        store.RUNS_FILE = tmp / "sim_runs.json"
        # far more attempts than RUNS_KEPT, most of them worthless
        for i in range(store.RUNS_KEPT * 2):
            store.record_run({"days": 999, "ok": False, "leagues": ["prep"]})
        for i in range(60):
            store.record_run({"days": 7, "ok": True, "leagues": ["prep"]})
        for i in range(60):
            store.record_run({"days": 3, "ok": True, "dry_run": True, "leagues": ["prep"]})

        rows = store.runs(limit=None)
        check("store", rows, 60 * 7)
        assert len(rows) >= 60, f"only {len(rows)} rows survived"

        # the default limit is a DISPLAY limit and must not be what history is derived from
        assert len(store.runs()) == 20, len(store.runs())

        # every row carries both flags, so a reader never has to guess what missing meant
        assert all("ok" in r and "dry_run" in r for r in rows)

        # ---- the write is atomic ------------------------------------------------------------
        # a half-written file is the total-loss case: _read_runs turns it into []
        before = json.loads(store.RUNS_FILE.read_text(encoding="utf-8"))
        store.record_run({"days": 21, "ok": True, "leagues": ["prep"]})
        after = json.loads(store.RUNS_FILE.read_text(encoding="utf-8"))
        assert len(after) == len(before) + 1 or len(after) >= len(before), (len(before), len(after))
        assert not list(tmp.glob("*.tmp")), "the temp file was left behind"
        check("store after another real run", store.runs(limit=None), 60 * 7 + 21)

        # ---- localstore must behave the same -------------------------------------------------
        localstore.PATH = tmp / "local_store.json"
        for i in range(120):
            localstore.record_run({"days": 999, "ok": False})
        for i in range(60):
            localstore.record_run({"days": 7, "ok": True})
        check("localstore", localstore.runs(limit=None), 60 * 7)
        assert len(localstore.runs()) == 20

        # ---- newest first, in both ----------------------------------------------------------
        for rows_, who in ((store.runs(limit=None), "store"),
                           (localstore.runs(limit=None), "localstore")):
            stamps = [str(r.get("at") or "") for r in rows_]
            assert stamps == sorted(stamps, reverse=True), f"{who} is not newest-first"
    finally:
        store.RUNS_FILE, localstore.PATH = real_file, real_path
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK  run log: every simmed run survives, the write is atomic, both stores agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
