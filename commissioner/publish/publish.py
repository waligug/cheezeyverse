"""Take what FBPB3 generated and put it where GitHub Pages can serve it.

FBPB3 writes its HTML into `leaguedata/<save>/html`. That folder is re-skinned (see restyle.py)
into `site/leagues/<key>/`, so one Pages deploy serves the character site and all three league
sites together and `site/config.js` can point at plain relative paths.

    python -m commissioner.publish.publish            # all three leagues
    python -m commissioner.publish.publish prep
"""
from __future__ import annotations

import json
import shutil
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
    """The season the universe is actually in, for the page header.

    This used to scrape the first four digits out of `saveinfo.dat`, which holds a date, not a
    season - so every page in every league said "Season 2030" for ever, however many years had
    been played. The store knows the season; ask it.
    """
    try:
        from ..simweek import store
        season = store().get_settings().get("current_season")
        if season:
            return f"Season {int(season)}"
    except Exception:
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


PAGES_BRANCH = "gh-pages"


def git_push(message=None, branch=PAGES_BRANCH, remote="origin"):
    """Publish `site/` to the Pages branch, league pages and all.

    `site/leagues/` is deliberately gitignored: it is 58 MB, it is rewritten in full on every
    single publish, and committing it to the main history would make the repository unusable
    within a month. But a plain `git add site` therefore pushed the character site with all three
    league sites MISSING - the hub would deploy with three dead links and nothing would say why.

    So the pages go to their own orphan branch through a temporary worktree: the main history
    stays clean, the deployed site is complete, and each publish replaces the branch's single
    commit rather than adding to it. `git add -f` is what gets past the ignore rule; it is
    deliberate here and nowhere else.
    """
    message = message or f"Publish the Cheezeyverse {datetime.now():%Y-%m-%d %H:%M}"
    if not (SITE / "leagues").exists():
        raise RuntimeError("site/leagues does not exist yet - publish the league sites first")

    def git(*args, cwd=ROOT, check=True):
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if check and r.returncode and "nothing to commit" not in (r.stdout + r.stderr):
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
        return r

    if not git("remote", "get-url", remote, check=False).stdout.strip():
        raise RuntimeError(
            f"no git remote called {remote!r}. Create the GitHub repository, add it as a remote, "
            "and turn on Pages for the " + branch + " branch.")

    work = ROOT / "tmp" / "pages-worktree"
    if work.exists():
        git("worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)
    exists = git("rev-parse", "--verify", branch, check=False).returncode == 0
    git("worktree", "add", *( [] if exists else ["--orphan"] ), str(work), *([branch] if exists else [branch]))
    try:
        for child in work.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(SITE, work, dirs_exist_ok=True)
        (work / ".nojekyll").write_text("", encoding="utf-8")   # or Pages skips folders like js/
        git("add", "-f", ".", cwd=work)
        git("commit", "-m", message, cwd=work, check=False)
        git("push", "--force", remote, f"HEAD:{branch}", cwd=work)
    finally:
        git("worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)
    return message


if __name__ == "__main__":
    keys = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    for row in publish(keys):
        print(f'{row["league"]:8} {row["pages"]:4} pages -> {row["path"]}')
    if "--push" in sys.argv:
        print("pushed:", git_push())
