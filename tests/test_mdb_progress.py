"""Output MDB uses an inactivity timeout, so a growing export is never cancelled."""
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.driver import fbpb3


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    target = root / "leaguedata" / "Test" / "LeagueOutput.mdb"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    game = fbpb3.FBPB3()
    game.click = lambda *a, **k: None
    marks = iter([(1, 1), (2, 2), (3, 3), (4, 4)])
    game._file_mark = lambda _path: next(marks)
    calls = [0]

    def dialog(*_a, **_k):
        calls[0] += 1
        time.sleep(.035)
        if calls[0] < 3:
            raise fbpb3.DriverError("still exporting")

    game.dismiss_message = dialog
    game._settle_dialogs = lambda: True
    started = time.monotonic()
    with patch.object(fbpb3, "DOCS", root):
        got = game.output_mdb("Test", attempts=1, timeout=.05)
    elapsed = time.monotonic() - started
    assert got == target and elapsed > .05 and calls[0] == 3, (got, elapsed, calls)

print("OK  MDB export: continued file growth renews the timeout until File Created arrives")
