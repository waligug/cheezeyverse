"""The Pages deploy, driven end to end against a real local remote.

git_push no longer builds the commit in a worktree. It used to add one (a full checkout of the
branch), delete every file in it, copy site/ over the top and commit that - three passes over
112 MB to produce a commit `write-tree` can make from site/ where it already sits. Now it stages
site/ through a throwaway index and writes the tree, the commit and the ref by hand.

That is a faster deploy and a much easier one to get subtly wrong, so this pushes to a real bare
repository on disk and reads back what actually landed:

  * the gitignored site/leagues/ IS published (the whole reason `git add -f` is here),
  * the branch keeps exactly ONE commit, with no parent, publish after publish,
  * .nojekyll is in the tree, or Pages would skip site/js/,
  * a CNAME already on the branch survives a publish that has no site/CNAME,
  * site/ itself is never modified to make a deploy work, and
  * the repository's own index and HEAD are left completely alone.
  * deploy_losses still stops a deploy that would delete published league pages.

    python tests/test_deploy_push.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.publish import publish as publishing  # noqa: E402

FAILS = []


def ok(cond, msg):
    if not cond:
        FAILS.append(msg)


def git(*args, cwd, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr or r.stdout}")
    return r.stdout.strip()


def build_repo(root):
    """A miniature of the real thing: a repo whose site/leagues is gitignored."""
    root.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "test@example.com", cwd=root)
    git("config", "user.name", "Test", cwd=root)
    (root / ".gitignore").write_text("site/leagues/\ntmp/\n", encoding="utf-8")
    site = root / "site"
    (site / "js").mkdir(parents=True)
    (site / "index.html").write_text("<h1>hub</h1>", encoding="utf-8")
    (site / "h2h.html").write_text("<h1>h2h</h1>", encoding="utf-8")
    (site / "js" / "ui.js").write_text("// ui", encoding="utf-8")
    for key in ("prep", "college", "pro"):
        d = site / "leagues" / key
        d.mkdir(parents=True)
        (d / "index.htm").write_text(f"<h1>{key}</h1>", encoding="utf-8")
        (d / "games.json").write_text('{"characters":[]}', encoding="utf-8")
        (d / "stats.json").write_text('{"season":"Season 2027"}', encoding="utf-8")
    # A FILE sitting directly in leagues/, beside the league folders. publish() writes this on
    # every run, and reading the live branch as if every name under leagues/ were a directory
    # turned it into one - so the guard saw a league with no pages and refused every deploy.
    (site / "leagues" / "published.json").write_text('{"leagues":[]}', encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "sources", cwd=root)
    bare = root.parent / "remote.git"
    git("init", "-q", "--bare", str(bare), cwd=root.parent)
    git("remote", "add", "origin", str(bare), cwd=root)
    return site, bare


def tree_paths(root, branch):
    return set(git("ls-tree", "-r", "--name-only", branch, cwd=root).splitlines())


def main():
    work = Path(tempfile.mkdtemp(prefix="deploy-push-"))
    root = work / "repo"
    keep = (publishing.ROOT, publishing.SITE)
    try:
        site, bare = build_repo(root)
        publishing.ROOT, publishing.SITE = root, site

        head_before = git("rev-parse", "HEAD", cwd=root)
        index_before = git("status", "--porcelain", cwd=root)

        publishing.git_push("first publish")
        branch = publishing.PAGES_BRANCH
        paths = tree_paths(root, branch)

        # The whole reason `git add -f` is in this function.
        for key in ("prep", "college", "pro"):
            ok(f"leagues/{key}/index.htm" in paths,
               f"the gitignored league pages did not publish: leagues/{key}/index.htm missing")
        ok("index.html" in paths and "js/ui.js" in paths, "the character site did not publish")
        ok(".nojekyll" in paths, ".nojekyll is missing - Pages would skip site/js/")
        ok("site/index.html" not in paths,
           "the site/ prefix leaked into the branch; pages would deploy one folder down")

        # Published, not committed to main; and nothing of ours touched the working copy.
        ok(git("rev-parse", "HEAD", cwd=root) == head_before, "the deploy moved HEAD")
        ok(git("status", "--porcelain", cwd=root) == index_before,
           "the deploy left changes in the repository's own index or working tree")
        ok(not (site / ".nojekyll").exists(),
           "the deploy wrote .nojekyll into site/; deploy files must not touch the sources")

        # It reached the remote branch; GitHub Pages deployment is a separate step.
        remote_head = git("rev-parse", branch, cwd=bare)
        ok(remote_head == git("rev-parse", branch, cwd=root), "the local branch and the remote disagree")

        # ---- one commit, no parent, publish after publish ----------------------------------
        (site / "index.html").write_text("<h1>hub 2</h1>", encoding="utf-8")
        publishing.git_push("second publish")
        ok(git("rev-parse", branch, cwd=root) != remote_head, "the second publish changed nothing")
        ok(git("rev-list", "--count", branch, cwd=root) == "1",
           "the Pages branch is accumulating commits instead of being replaced")
        ok(git("log", "-1", "--format=%P", branch, cwd=root) == "",
           "the publish commit has a parent; the branch is no longer a single orphan commit")
        ok(git("show", f"{branch}:index.html", cwd=root) == "<h1>hub 2</h1>",
           "the second publish did not carry the changed page")

        # ---- a CNAME on the branch survives a publish that has none -------------------------
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root,
                              input=b"cheezey.example\n", capture_output=True).stdout.decode().strip()
        idx = work / "cname-index"
        env = {**os.environ, "GIT_INDEX_FILE": str(idx), "GIT_DIR": str(root / ".git")}
        subprocess.run(["git", "read-tree", branch], cwd=root, env=env, check=True)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},CNAME"],
                       cwd=root, env=env, check=True)
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              capture_output=True, text=True).stdout.strip()
        commit = subprocess.run(["git", "commit-tree", tree, "-m", "domain"], cwd=root, env=env,
                                capture_output=True, text=True).stdout.strip()
        git("update-ref", f"refs/heads/{branch}", commit, cwd=root)

        publishing.git_push("third publish")
        ok(git("show", f"{branch}:CNAME", cwd=root) == "cheezey.example",
           "the custom domain was dropped; every sim would break the domain")
        ok(not (site / "CNAME").exists(), "the deploy wrote CNAME into site/")

        # ---- the guard still stops a deploy that would delete published pages ----------------
        shutil.rmtree(site / "leagues" / "pro")
        try:
            publishing.git_push("fourth publish")
            FAILS.append("a deploy missing a live league went through; the pro pages would be gone")
        except RuntimeError as exc:
            ok("leagues/pro" in str(exc),
               f"the guard fired but did not name the league that would be lost: {exc}")
        ok(git("rev-parse", branch, cwd=bare) == git("rev-parse", branch, cwd=root),
           "the refused deploy pushed anyway")
    finally:
        publishing.ROOT, publishing.SITE = keep
        shutil.rmtree(work, ignore_errors=True)

    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  deploy: gitignored league pages publish, the branch stays one parentless commit, "
          ".nojekyll and an inherited CNAME survive, site/ and the repo index are untouched, "
          "and the loss guard still refuses a deploy that would delete a live league")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
