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


def _our_players(league_key):
    """{fbpb3 player id: owner-facing name} for the real people's characters in this league.

    Keyed by the game's own player id rather than by name, because two players can share a
    name - the stock rosters do - and badging by name would decorate the wrong man. Returns
    empty on any failure: a missing badge is cosmetic, a publish that will not run is not.
    """
    try:
        from ..simweek import store
        out = {}
        for c in store().characters(league=league_key):
            if c.get("status") == "retired":
                continue
            pid = (c.get("league_player_ids") or {}).get(league_key)
            if pid is not None:
                # The live sheet travels with the name, because FBPB3's player pages print a
                # season-start snapshot rather than current ratings - and for a claimed reserve
                # slot that snapshot is the RESERVE's ratings, so every character's page showed
                # his predecessor's numbers until the season rolled over.
                out[int(pid)] = {
                    "name": f'{c["first_name"]} {c["last_name"]}',
                    "ratings": c.get("ratings") or {},
                    "potentials": c.get("potentials") or {},
                }
        return out
    except Exception as exc:
        print(f"   (could not mark our players in {league_key}: {exc})")
        return {}


def publish_league(key):
    spec = cfg.BY_KEY[key]
    src = DOCS / "leaguedata" / spec.save_name / "html"
    dst = SITE / "leagues" / key
    if not (src / "index.htm").exists():
        raise FileNotFoundError(f"{spec.save_name} has no HTML output yet - run the driver's html_output() first")
    pages = restyle(src, dst, league=spec.name, season=season_label(key), key=key,
                    ours=_our_players(key))
    # AFTER restyle, both of them. restyle(clean=True) does an rmtree of the league's folder and
    # rebuilds it from the game's export, so anything written there beforehand is deleted. That
    # is exactly what happened to games.json: run_sim wrote it before publish(), a publish then
    # removed it from the site, and the comment on that call claimed the opposite.
    stats = _write_stats(src, dst, key)
    games = _write_games(src, dst, key)
    return {"league": key, "name": spec.name, "pages": pages, "path": str(dst),
            "stats": stats, "games": games}


def _write_games(src, dst, key):
    """Emit `games.json`: each character's own game lines, for the head-to-head page.

    Read from the LeagueOutput.mdb the sim writes while the save is loaded. Done here rather
    than before publish() so that EVERY publish carries it - a sim, a manual re-skin, a
    republish after a fix - instead of only the one code path that remembered to write it first.

    Never fatal, like stats.json. A league with no MDB yet simply has no file, and the page
    treats the 404 as "not published yet" rather than as an error.
    """
    try:
        from .. import headtohead
        from ..simweek import store
        # src is <save>/html, so the MDB sits beside it. Derived from the SAME path the pages
        # came from rather than re-deriving it from the league key: two routes to one file is
        # two chances to publish a league's data from another league's export.
        mdb = Path(src).parent / "LeagueOutput.mdb"
        if not mdb.exists():
            return None
        st = store()
        people = [c for c in st.characters(league=key) if c.get("status") == "active"]
        if not people:
            return None
        settings = st.get_settings()
        data = headtohead.from_mdb(
            mdb, people,
            runs=(st.runs(limit=None) if hasattr(st, "runs") else []),
            league=key,
            season=int(settings.get("current_season", 0)) or None)
        data["generated"] = datetime.now().isoformat(timespec="seconds")
        (dst / "games.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        played = sum(len(c["games"]) for c in data["characters"])
        if not played:
            print(f"  {key}: the MDB gave no game lines at all - head-to-head will be empty")
        return {"characters": len(data["characters"]), "games": played}
    except Exception as exc:
        print(f"  no games.json for {key} ({exc}); the pages themselves are fine")
        return None


def _write_stats(src, dst, key):
    """Emit `stats.json` beside the skinned pages: counting stats and league placings.

    The site needs this to show anybody where they stand, and the alternative is the browser
    fetching and parsing four hundred player pages - which would be a second implementation of
    the export's four traps (the undefeated team reading 1.000, the repeated STL column, the
    digits in the header, the draft pool's borrowed season lines). All four are already handled
    and tested in seasonbonus; this just writes down what it works out.

    Never fatal. A publish that produced every page is a good publish even if this file is
    missing, and the pages are what people came for.
    """
    try:
        from ..seasonbonus import league_stats
        data = league_stats(src)
        data["generated"] = datetime.now().isoformat(timespec="seconds")
        data["league"] = key
        data["season"] = season_label(key)
        (dst / "stats.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        return {"players": data["count"], "elite": data["elite"]}
    except Exception as exc:
        print(f"  no stats.json for {key} ({exc}); the pages themselves are fine")
        return None


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

    kept_cname = ""
    if git("rev-parse", "--verify", f"{branch}:CNAME", check=False).returncode == 0:
        kept_cname = git("show", f"{branch}:CNAME", check=False).stdout.strip()

    work = ROOT / "tmp" / "pages-worktree"
    if work.exists():
        git("worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)
    exists = git("rev-parse", "--verify", branch, check=False).returncode == 0
    # `git worktree add --orphan <path> <branch>` is rejected: --orphan takes the branch name via
    # -b and refuses a commit-ish alongside it.
    if exists:
        git("worktree", "add", str(work), branch)
    else:
        git("worktree", "add", "--detach", str(work))
        git("checkout", "--orphan", branch, cwd=work)
        git("rm", "-rf", "--cached", ".", cwd=work, check=False)
    try:
        for child in work.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        shutil.copytree(SITE, work, dirs_exist_ok=True)
        (work / ".nojekyll").write_text("", encoding="utf-8")   # or Pages skips folders like js/

        # A custom domain lives in a CNAME file IN the published branch, and this publish replaces
        # that branch wholesale - so without this the domain would break on every single sim.
        # site/CNAME is the source of truth; keep whatever the branch already had otherwise.
        domain = (SITE / "CNAME")
        if domain.exists():
            shutil.copy2(domain, work / "CNAME")
        elif kept_cname:
            (work / "CNAME").write_text(kept_cname, encoding="utf-8")
        git("add", "-f", ".", cwd=work)
        # check=True, deliberately. A commit that fails - most commonly because the machine has
        # no git user.name/user.email - used to be swallowed here, leaving the orphan branch
        # with no commit at all. The PUSH then failed with "src refspec HEAD does not match
        # any", which describes a symptom three steps downstream of the cause and sent the
        # reader looking at refspecs. git() already tolerates "nothing to commit".
        git("commit", "-m", message, cwd=work)
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
