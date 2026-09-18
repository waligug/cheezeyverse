"""Gather everything the server needs into one folder, ready to copy across.

The code is on GitHub and the server clones it. What GitHub cannot carry is exactly the stuff
that matters most: the three save files, and the Supabase service key. This puts both in one
place, with a README beside them, so the move is a single drag rather than a scavenger hunt.

    python tools/stage_for_server.py                       # stage into tmp/to-server/
    python tools/stage_for_server.py --to \\\\SERVER\\share\\cheezey   # stage straight onto it
    python tools/stage_for_server.py --check \\\\SERVER\\share        # can I reach that path?

FBPB3 must be closed. The game keeps `league.dat` in memory and writes it back when it exits,
so copying a save out from under a running copy gets you a file that is half one state and half
another - and it will still open, which is the dangerous part.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FBPB3.exe"],
                             capture_output=True, text=True)
        return "FBPB3.exe" in out.stdout
    except Exception:
        return False


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


README = """CHEEZEYVERSE - what to do with this folder
==========================================

Staged {when} from {host}.

This holds the two things GitHub cannot carry: the save files, and the Supabase service key.
Everything else is code, and the server clones that.

ON THE SERVER
-------------

1.  Install FBPB3 (Fast Break Pro Basketball 3) to its default location:
        C:\\Program Files (x86)\\GDS\\Fast Break Pro Basketball 3
    Do not create any leagues. The saves below replace whatever it starts with.

2.  Get the code:
        git clone https://github.com/waligug/cheezeyverse.git C:\\claude\\hoops-universe
        cd C:\\claude\\hoops-universe
        pip install -r requirements.txt

3.  Copy from this folder:

        leaguedata\\        ->  C:\\Users\\Public\\Documents\\GDS\\Fast Break Pro Basketball 3\\
        env\\.env           ->  C:\\claude\\hoops-universe\\.env

    FBPB3 must be CLOSED while you do this, on both machines.

4.  Let the server publish the site:
        gh auth login

5.  Check the machine can actually do the job:
        python tools\\server_doctor.py

    Everything must say ok. The last check is the one that matters - whether this session has
    a desktop that can be clicked. See docs\\SERVER.md.

6.  Confirm the saves survived the trip:
        python tools\\verify_save.py

    Expect ALL PASS. checksums.txt in this folder lists the SHA-256 of every file as it left
    the old machine, if you want to prove the copy was clean:
        certutil -hashfile "<file>" SHA256

7.  Run it:
        python -m commissioner.app --lan

    It prints the address. Open that from your desktop and press Sim Week.

THEN, AND ONLY THEN
-------------------

Stop the commissioner on the old machine and do not open FBPB3 there again while the server
owns the universe. Both machines write league.dat on exit and the last one to close wins,
silently. The old machine stays a complete backup until the server has run a full Sim Week
and published.

WHAT IS IN HERE
---------------
{manifest}
"""


def main():
    ap = argparse.ArgumentParser(description="Stage the save files and keys for the server")
    ap.add_argument("--to", metavar="PATH",
                    help=r"stage straight onto the server, e.g. \\SERVER\share\cheezey")
    ap.add_argument("--check", metavar="PATH", help="just test whether a path is reachable")
    args = ap.parse_args()

    if args.check:
        target = Path(args.check)
        try:
            reachable = target.exists()
        except OSError as exc:
            print(f"cannot reach {target}: {exc}")
            return 1
        print(f"{target}: {'reachable' if reachable else 'not found'}")
        if reachable:
            try:
                probe = target / ".cheezey-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                print("and writable")
            except OSError as exc:
                print(f"but NOT writable: {exc}")
                return 1
        return 0 if reachable else 1

    if game_running():
        sys.exit("FBPB3 is open. Close it first - it writes league.dat when it exits, and a save "
                 "copied out from under it is half one state and half another.")

    dest = Path(args.to) if args.to else ROOT / "tmp" / "to-server"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"staging into {dest}")

    lines = []

    # ---- the saves ------------------------------------------------------------------------
    src = DOCS / "leaguedata"
    if not src.exists():
        sys.exit(f"no leaguedata at {src}")
    out = dest / "leaguedata"
    if out.exists():
        shutil.rmtree(out)
    # Only the three Cheezeyverse saves. `Chung` and `Chung_test` are stock leagues kept on the
    # old machine as codec controls - tests/test_codec.py round-trips against them - and they
    # have nothing to do with the universe. Copying them would put 13 MB of confusing extra
    # saves in the game's load list on the server, where somebody would eventually open one.
    from commissioner.universe import config as cfg
    wanted = {spec.save_name for spec in cfg.LEAGUES}
    print(f'  copying {len(wanted)} saves: {", ".join(sorted(wanted))}')
    out.mkdir(parents=True)
    for folder in sorted(src.iterdir()):
        if folder.is_dir() and folder.name in wanted:
            shutil.copytree(folder, out / folder.name)
        elif folder.is_file():
            shutil.copy2(folder, out / folder.name)

    sums = []
    for dat in sorted(out.rglob("league.dat")):
        d = digest(dat)
        rel = dat.relative_to(dest)
        sums.append(f"{d}  {rel}")
        lines.append(f"  {rel}   {dat.stat().st_size:,} bytes")
        print(f"    {rel.parent.name}: {dat.stat().st_size:,} bytes")

    # Everything else FBPB3 keeps beside the saves - the league and roster CSVs the wizard
    # reads. Small, and rebuilding the universe needs them.
    for extra in ("LeagueFiles", "PlayerFiles"):
        folder = DOCS / extra
        if folder.exists():
            shutil.copytree(folder, dest / extra, dirs_exist_ok=True)
            n = len(list((dest / extra).rglob("*")))
            lines.append(f"  {extra}\\   {n} file(s)")
            print(f"  copied {extra} ({n} files)")

    # ---- the key --------------------------------------------------------------------------
    env = ROOT / ".env"
    if env.exists():
        (dest / "env").mkdir(exist_ok=True)
        shutil.copy2(env, dest / "env" / ".env")
        lines.append("  env\\.env   the Supabase service_role key - do not put this anywhere public")
        print("  copied .env (service key - handle it like a password)")
    else:
        print("  no .env found; the server will need SUPABASE_URL and SUPABASE_SERVICE_KEY")

    (dest / "checksums.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    lines.append("  checksums.txt   SHA-256 of each save as it left this machine")

    import socket
    (dest / "READ-ME-FIRST.txt").write_text(
        README.format(when=datetime.now().strftime("%Y-%m-%d %H:%M"),
                      host=socket.gethostname(), manifest="\n".join(lines)),
        encoding="utf-8")

    total = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"\nstaged {total / 1e6:.0f} MB into {dest}")
    print("READ-ME-FIRST.txt in there has the steps for the server.")
    if not args.to:
        print("\nCopy that whole folder to the server, or re-run with:")
        print(r"    python tools/stage_for_server.py --to \\SERVER\share\somewhere")
    return 0


if __name__ == "__main__":
    sys.exit(main())
