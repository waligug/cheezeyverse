"""A deploy must not quietly delete the live site's league pages.

git_push force-pushes one machine's `site/` folder over the whole Pages branch, and
`site/leagues/` is gitignored - 58 MB, rewritten every publish - so what reaches the public site
is whatever that folder holds on the machine that ran it. Nothing in git protects it. Deploying
from a machine that has never published a league, or one still holding an abandoned universe's
pages, replaces the live site with those and deletes games.json outright. No error anywhere: the
page just goes blank, and the league pages exist nowhere else.

This is the same trap as every parser bug this week - working from a convenience copy rather than
the real thing - except a force push has no undo.

    python tests/test_deploy_guard.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.publish.publish import deploy_losses  # noqa: E402


def site(root, leagues, pages=("index.html", "h2h.html")):
    """A site tree: {league: {"season": ..., "files": [...]}} plus some top-level pages."""
    root = Path(root)
    (root / "leagues").mkdir(parents=True, exist_ok=True)
    for page in pages:
        (root / page).write_text("<html></html>", encoding="utf-8")
    for key, spec in leagues.items():
        folder = root / "leagues" / key
        folder.mkdir(parents=True, exist_ok=True)
        for name in spec.get("files", ("index.htm", "stats.json", "games.json")):
            if name == "stats.json":
                (folder / name).write_text(json.dumps({"season": spec.get("season", "2026-27")}),
                                           encoding="utf-8")
            else:
                (folder / name).write_text("x", encoding="utf-8")
    return root


def main():
    tmp = Path(tempfile.mkdtemp(prefix="deploy-guard-"))
    try:
        live = site(tmp / "live", {"prep": {}, "college": {}, "pro": {}})

        # the normal case: the same machine, everything present
        same = site(tmp / "same", {"prep": {}, "college": {}, "pro": {}})
        assert deploy_losses(live, same) == [], deploy_losses(live, same)

        # THE DESKTOP. It has league pages from the abandoned universe and no games.json at all.
        desktop = site(tmp / "desktop",
                       {"prep": {"season": "2030-31", "files": ("index.htm", "stats.json")},
                        "college": {"season": "2030-31", "files": ("index.htm", "stats.json")},
                        "pro": {"season": "2030-31", "files": ("index.htm", "stats.json")}})
        problems = deploy_losses(live, desktop)
        assert len(problems) == 6, problems
        assert sum("games.json" in p for p in problems) == 3, problems
        assert sum("different universe" in p for p in problems) == 3, problems

        # a machine that has never published a league at all
        bare = site(tmp / "bare", {})
        assert len(deploy_losses(live, bare)) == 3, deploy_losses(live, bare)

        # one league missing is still a loss, and it names which
        partial = site(tmp / "partial", {"prep": {}, "college": {}})
        problems = deploy_losses(live, partial)
        assert len(problems) == 1 and "leagues/pro" in problems[0], problems

        # a top-level page that only exists live (h2h.html landed the same way)
        no_h2h = site(tmp / "no-h2h", {"prep": {}, "college": {}, "pro": {}}, pages=("index.html",))
        assert deploy_losses(live, no_h2h) == ["h2h.html: live, and missing here"], \
            deploy_losses(live, no_h2h)

        # A ROLLOVER IS NOT A DIFFERENT UNIVERSE. The season moves on by exactly one year and
        # everything else is present, so it must deploy - this refused the real 2026 -> 2027
        # publish until the rule learned the difference.
        rolled = site(tmp / "rolled", {"prep": {"season": "2027-28"}, "college": {"season": "2027-28"},
                                       "pro": {"season": "2027-28"}})
        assert deploy_losses(live, rolled) == [], deploy_losses(live, rolled)
        # ...but four seasons ahead is the abandoned universe again, not a rollover
        far = site(tmp / "far", {"prep": {"season": "2030-31"}, "college": {"season": "2030-31"},
                                 "pro": {"season": "2030-31"}})
        assert len(deploy_losses(live, far)) == 3, deploy_losses(live, far)
        # and a season BEHIND is a machine that has not caught up
        behind = site(tmp / "behind", {"prep": {"season": "2025-26"}, "college": {}, "pro": {}})
        assert len(deploy_losses(live, behind)) == 1, deploy_losses(live, behind)

        # ADDING is never a loss: a new league, or the first games.json, must deploy freely
        older = site(tmp / "older", {"prep": {"files": ("index.htm", "stats.json")}})
        newer = site(tmp / "newer", {"prep": {}, "college": {}})
        assert deploy_losses(older, newer) == [], deploy_losses(older, newer)

        # the first publish of all: nothing live yet, so nothing can be lost
        empty = Path(tmp / "empty")
        empty.mkdir()
        assert deploy_losses(empty, same) == []

        # unreadable stats.json must not crash the guard or invent a universe mismatch
        broken = site(tmp / "broken", {"prep": {}, "college": {}, "pro": {}})
        (broken / "leagues" / "prep" / "stats.json").write_text("{not json", encoding="utf-8")
        assert deploy_losses(live, broken) == [], deploy_losses(live, broken)

        print("OK  deploy guard: a push that would delete published leagues is refused")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
