"""Take what FBPB3 generated and put it where GitHub Pages can serve it.

FBPB3 writes its HTML into `leaguedata/<save>/html`. That folder is re-skinned (see restyle.py)
into `site/leagues/<key>/`, so one Pages deploy serves the character site and all three league
sites together and `site/config.js` can point at plain relative paths.

    python -m commissioner.publish.publish            # all three leagues
    python -m commissioner.publish.publish prep
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ..universe import config as cfg
from .restyle import restyle

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
SITE = ROOT / "site"


def season_label(key):
    """Whatever the save knows about where it is, as a short string for the page header."""
    info = DOCS / "leaguedata" / cfg.BY_KEY[key].save_name / "saveinfo.dat"
    try:
        raw = info.read_bytes().decode("latin-1", "replace")
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) >= 4:
            return f"Season {digits[:4]}"
    except OSError:
        pass
    return f"Season {cfg.START_YEAR}"


def publish_league(key):
    spec = cfg.BY_KEY[key]
    src = DOCS / "leaguedata" / spec.save_name / "html"
    dst = SITE / "leagues" / key
    if not (src / "index.htm").exists():
        raise FileNotFoundError(f"{spec.save_name} has no HTML output yet - run the driver's html_output() first")
    pages = restyle(src, dst, league=spec.name, season=season_label(key))
    return {"league": key, "name": spec.name, "pages": pages, "path": str(dst)}


def publish(keys=None):
    out = [publish_league(k) for k in (keys or [s.key for s in cfg.LEAGUES])]
    (SITE / "leagues" / "published.json").write_text(
        json.dumps({"published_at": datetime.now().isoformat(timespec="seconds"), "leagues": out}, indent=1),
        encoding="utf-8")
    return out


def git_push(message=None):
    """Commit the site folder and push, which is what makes GitHub Pages redeploy."""
    message = message or f"Publish league sites {datetime.now():%Y-%m-%d %H:%M}"
    for args in (["git", "add", "site"], ["git", "commit", "-m", message], ["git", "push"]):
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
        if r.returncode and "nothing to commit" not in (r.stdout + r.stderr):
            raise RuntimeError(f"{' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return message


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    for row in publish(keys):
        print(f'{row["league"]:8} {row["pages"]:4} pages -> {row["path"]}')
    if "--push" in sys.argv:
        print("pushed:", git_push())
