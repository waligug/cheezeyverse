"""Run this ON THE SERVER. It answers one question: can this machine actually run a Sim Week?

Moving the universe to another box is mostly copying files, and the copying is the part that
goes right. What goes wrong is the part nobody can see: the game drives through real mouse
clicks, so the server needs a live, unlocked, rendering desktop. A locked RDP session looks
completely healthy from the command line and fails the instant a sim starts.

So this checks the boring things (Python, packages, the save files, the keys) and then the one
that actually matters - whether there is a desktop here that pywinauto can click on.

    python tools/server_doctor.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOCS = Path(r"C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3")
GAME = Path(r"C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3")

rows = []


def check(name, ok, detail="", fix=""):
    rows.append((name, bool(ok), detail, fix))
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark}  {name}" + (f"  -  {detail}" if detail else ""))
    if not ok and fix:
        print(f"        fix: {fix}")


def section(title):
    print(f"\n{title}")


def main():
    print("Cheezeyverse server check\n" + "=" * 60)

    # ---- the machine ---------------------------------------------------------------------
    section("Python and packages")
    check("python 3.9+", sys.version_info >= (3, 9), sys.version.split()[0])
    for mod, why in (("pywinauto", "drives the game"), ("flask", "serves the panel"),
                     ("requests", "talks to Supabase"), ("win32gui", "raises the game window")):
        try:
            __import__(mod)
            check(f"{mod} importable", True, why)
        except ImportError:
            check(f"{mod} importable", False, why, "pip install -r requirements.txt")

    check("node on PATH", shutil.which("node") is not None,
          "only needed for the test suite, not for simming",
          "install Node if you want to run tests/test_site_syntax.py here")

    # ---- the game ------------------------------------------------------------------------
    section("The game and the saves")
    exe = GAME / "FBPB3.exe"
    check("FBPB3 installed", exe.exists(), str(GAME),
          "install Fast Break Pro Basketball 3 to its default location")

    try:
        from commissioner.universe import config as cfg
        for spec in cfg.LEAGUES:
            dat = DOCS / "leaguedata" / spec.save_name / "league.dat"
            check(f"save {spec.save_name}", dat.exists(),
                  f"{dat.stat().st_size:,} bytes" if dat.exists() else "missing",
                  f"copy leaguedata\\{spec.save_name}\\ over from the old machine")
    except Exception as exc:
        check("league config loads", False, str(exc))

    # ---- keys ----------------------------------------------------------------------------
    section("Keys and configuration")
    env = ROOT / ".env"
    check(".env present", env.exists(), str(env), "copy .env across by hand - it is gitignored")
    if env.exists():
        text = env.read_text(encoding="utf-8", errors="replace")
        has_url = "SUPABASE_URL=" in text and "YOUR-PROJECT-REF" not in text
        # Never print a key. Length and prefix are enough to tell configured from placeholder.
        key_line = next((ln for ln in text.splitlines()
                         if ln.strip().startswith("SUPABASE_SERVICE_KEY=")), "")
        key = key_line.split("=", 1)[1].strip() if "=" in key_line else ""
        check("SUPABASE_URL set", has_url)
        check("service key looks real", len(key) > 100 and key.startswith("ey"),
              f"{len(key)} characters", "paste the service_role key from the Supabase dashboard")

    try:
        from commissioner import store
        settings = store.get_settings()
        check("Supabase reachable", True, f"season {settings.get('current_season')}")
    except Exception as exc:
        check("Supabase reachable", False, str(exc)[:80])

    # ---- git -----------------------------------------------------------------------------
    section("Publishing")
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                       capture_output=True, text=True)
    check("git remote set", r.returncode == 0, r.stdout.strip())
    r = subprocess.run(["git", "ls-remote", "--heads", "origin"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    check("can reach GitHub", r.returncode == 0,
          "push access confirmed" if r.returncode == 0 else r.stderr.strip()[:80],
          "run: gh auth login")

    # ---- the one that actually matters ----------------------------------------------------
    section("The desktop (this is the one that breaks)")
    session_name = os.environ.get("SESSIONNAME", "")
    check("has a session", bool(session_name), session_name or "none",
          "run this from a logged-in desktop session, not a service")

    interactive = True
    detail = ""
    try:
        import win32gui
        # A locked or disconnected session has no rendering desktop. GetForegroundWindow returns
        # 0 there, and every real mouse click pywinauto sends will land nowhere.
        hwnd = win32gui.GetForegroundWindow()
        interactive = hwnd != 0
        detail = f"foreground window handle {hwnd}"
    except Exception as exc:
        interactive, detail = False, str(exc)[:60]
    check("desktop is rendering (pywinauto can click)", interactive, detail,
          "the session is locked or disconnected. See docs/SERVER.md - use keep_session.bat "
          "so disconnecting from RDP hands the session back to the console instead of locking it")

    if session_name.upper().startswith("RDP"):
        print("\n  NOTE: you are on an RDP session. Windows LOCKS it when you disconnect, and a")
        print("  locked session cannot be clicked - a sim running at that moment will fail.")
        print("  Run tools\\keep_session.bat before disconnecting, or start sims only while")
        print("  connected. See docs/SERVER.md.")

    # ---- verdict --------------------------------------------------------------------------
    bad = [n for n, ok, _, _ in rows if not ok]
    print("\n" + "=" * 60)
    if bad:
        print(f"{len(bad)} problem(s): " + ", ".join(bad))
        print("\nFix those, then run:  python tools/verify_save.py")
        return 1
    print("This machine can run a Sim Week.")
    print("\nNext:  python tools/verify_save.py     (expect ALL PASS)")
    print("       python -m commissioner.app --lan  (then open it from your desktop)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
