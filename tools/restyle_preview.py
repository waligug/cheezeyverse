"""Skin an FBPB3 html folder as the Cheezeyverse.

  python tools/restyle_preview.py <src> <dst> [--league "Cheezeyverse Prep"] [--season "S1"]

With no arguments it skins the captured Stabbyverse fixture into tmp/preview/, which is how the
look is reviewed without needing a generated save.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commissioner.publish.restyle import restyle  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("src", nargs="?", default=str(ROOT / "fixtures/html-output/svprep"))
ap.add_argument("dst", nargs="?", default=str(ROOT / "tmp/preview"))
ap.add_argument("--league", default="Cheezeyverse Prep")
ap.add_argument("--season", default="Season 1")
a = ap.parse_args()

n = restyle(a.src, a.dst, league=a.league, season=a.season)
print(f"skinned {n} pages -> {Path(a.dst).resolve()}\nopen {Path(a.dst).resolve() / 'index.htm'}")
