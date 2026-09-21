"""Incremental restyling leaves unchanged pages untouched and rebuilds changed input."""
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.publish.restyle import restyle  # noqa: E402


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    src, dst, cache = root / "src", root / "dst", root / "cache.json"
    src.mkdir()
    (src / "index.htm").write_text("<html><body>index</body></html>", encoding="latin-1")
    (src / "standings.htm").write_text("<html><body>one</body></html>", encoding="latin-1")
    restyle(src, dst, league="Test", season="Season 2028", key="prep", cache_path=cache)
    index_time = (dst / "index.htm").stat().st_mtime_ns
    standings_time = (dst / "standings.htm").stat().st_mtime_ns
    time.sleep(.02)
    restyle(src, dst, league="Test", season="Season 2028", key="prep", cache_path=cache)
    assert (dst / "index.htm").stat().st_mtime_ns == index_time
    assert (dst / "standings.htm").stat().st_mtime_ns == standings_time
    time.sleep(.02)
    (src / "standings.htm").write_text("<html><body>two</body></html>", encoding="latin-1")
    restyle(src, dst, league="Test", season="Season 2028", key="prep", cache_path=cache)
    assert (dst / "index.htm").stat().st_mtime_ns == index_time
    assert (dst / "standings.htm").stat().st_mtime_ns > standings_time

print("OK  restyle: unchanged pages are retained and changed source is rebuilt")
