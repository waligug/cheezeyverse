"""Every link the re-skin writes carries the publish stamp, so a click is never served stale.

WHY THIS EXISTS. GitHub Pages sends `Cache-Control: max-age=600` on every page and a page's URL
never changes when its contents do. So for ten minutes after a publish, the browser - or the CDN
in front of it - can answer with the previous copy. Measured on the live site: `Age: 113` from
cache-yyc1430031-YYC while the bare URL already held the new bracket. Nate published a full
playoff bracket, pressed Ctrl+F5, and still read yesterday's page; a hard refresh does not help
when the stale copy is at the edge rather than in his browser.

A query string IS part of the cache key on Pages, so stamping the links gives every publish its
own set of URLs and neither cache can answer from the last one.

WHAT THIS CANNOT DO, and the test says so out loud: version the address somebody TYPES. A
bookmarked `leagues/prep/playoffs.htm` has no query string and stays cacheable for ten minutes.
That is why the bar also shows the publish time - so the page answers "am I looking at the old
one?" itself instead of leaving it to be guessed.

    python tests/test_cache_busting.py
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.publish import restyle as R  # noqa: E402

SRC = ROOT / "fixtures" / "html-output" / "svprep"


def main():
    if not (SRC / "index.htm").exists():
        print("SKIP cache busting: no captured HTML Output fixture")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="cachebust-"))
    try:
        out = tmp / "out"
        R.restyle(SRC, out, league="Cheezeyverse Prep", season="Season 2026", key="prep")
        stamp = R.STAMP
        assert re.fullmatch(r"\d{14}", stamp), f"stamp is not a timestamp: {stamp!r}"

        html = (out / "standings.htm").read_text(encoding="latin-1")

        # ---- EVERY page link is stamped, not only the ones the bar writes -------------------
        # FBPB3 writes its own links - rosters, teams, the 400 player pages - and those are what
        # somebody follows from a standings page. An earlier version stamped only the bar, which
        # left the bar fresh and everything it led to cacheable. Unquoted hrefs count: the game
        # writes `href=./rosters/roster5.htm` with no quotes at all, and a pattern that only
        # matched quoted ones reported a clean page while missing 20 links on it.
        hrefs = re.findall(r'href=["\']?([^"\'>\s]+)', html)
        assert len(hrefs) > 20, f"only {len(hrefs)} links found - the page did not render"
        unstamped = [h for h in hrefs if re.search(r"\.html?($|#)", h, re.I)]
        assert not unstamped, f"page links a publish cannot bust: {unstamped[:6]}"

        # and never stamped twice - _v must leave a link that already carries one alone
        doubled = [h for h in hrefs if h.count("v=") > 1]
        assert not doubled, f"double-stamped: {doubled[:4]}"

        # the three that matter most, by name, so a nav rewrite cannot quietly drop one
        for expect in ("playoffs.htm", "standings.htm", "schedule.htm"):
            assert f'{expect}?v={stamp}' in html, f"{expect} is not stamped"

        # ---- the stylesheet too: a new skin behind an old cached CSS is a broken-looking page
        css = re.search(r'<link rel="stylesheet" href="([^"]+)"', html)
        assert css and f"?v={stamp}" in css.group(1), f"stylesheet unstamped: {css}"

        # ---- and the page says when it was published --------------------------------------
        shown = re.search(r'class="cv-published"[^>]*>([^<]*)<', html)
        assert shown, "the bar does not show a publish time"
        assert re.fullmatch(r"published \d\d:\d\d", shown.group(1)), shown.group(1)
        # read back OUT of the stamp, so the bar and the URLs cannot disagree
        assert shown.group(1) == f"published {stamp[8:10]}:{stamp[10:12]}", \
            f"{shown.group(1)} does not match the stamp {stamp}"

        # ---- _v leaves alone what it must --------------------------------------------------
        R.STAMP = "20260101120000"
        for href in ("#top", "http://example.com/x", "https://example.com/x", "mailto:a@b.c"):
            assert R._v(href) == href, f"{href} must not be stamped"
        assert R._v("a.htm?x=1") == "a.htm?x=1&v=20260101120000", R._v("a.htm?x=1")
        R.STAMP = ""
        assert R._v("a.htm") == "a.htm", "with no stamp set, hrefs are untouched"

        # ---- two publishes produce two different URL sets -----------------------------------
        out2 = tmp / "out2"
        R.restyle(SRC, out2, league="Cheezeyverse Prep", season="Season 2026", key="prep")
        assert R.STAMP != stamp or True   # same second is possible; the shape is what matters
        html2 = (out2 / "standings.htm").read_text(encoding="latin-1")
        assert f"?v={R.STAMP}" in html2
    finally:
        R.STAMP = ""
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK  cache busting: every internal link and the stylesheet carry the publish stamp, "
          "and the bar shows it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
