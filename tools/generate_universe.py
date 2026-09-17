"""Write the three league files and roster files. Usage: python tools/generate_universe.py [--dry DIR]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.universe.generate import generate  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--docs", help="write into this folder instead of the live FBPB3 documents folder")
args = ap.parse_args()

kwargs = {"docs": args.docs, "out_dir": Path(args.docs) / "universe"} if args.docs else {}
for row in generate(**kwargs):
    print(f'{row["league"]:8} {row["teams"]:3} teams  {row["players"]:4} players '
          f'({row["reserves"]} reserve)  -> {Path(row["league_file"]).name}, {Path(row["player_file"]).name}')
