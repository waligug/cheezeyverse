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
import re
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


def _scheduled_tasks():
    """{lowercased task name: "Name [State]"} for every task this user can see.

    Asked through PowerShell because schtasks.exe localises its output and parsing a localised
    table is how a check ends up reporting "missing" on a machine that has the task.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | ForEach-Object { \"$($_.TaskName)|$($_.State)\" }"],
            capture_output=True, text=True, timeout=90).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        if "|" in line:
            name, state = line.rsplit("|", 1)
            found[name.strip().lower()] = f"{name.strip()} [{state.strip()}]"
    return found


def _task_script(name):
    """The -File path a scheduled task's action runs, or "" if it has none."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-ScheduledTask -TaskName '{name}').Actions "
             "| ForEach-Object { $_.Arguments }"],
            capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r'-File\s+"([^"]+)"', out) or re.search(r"-File\s+(\S+)", out)
    return m.group(1) if m else ""


def _lan_reachable(port=5095):
    """(ok, why) - is there an enabled firewall rule for the panel, on a Private network?"""
    script = (
        "$r = Get-NetFirewallRule -DisplayName 'Cheezeyverse panel' -ErrorAction SilentlyContinue"
        " | Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Allow' };"
        "$cat = (Get-NetConnectionProfile | Select-Object -First 1).NetworkCategory;"
        "\"$([bool]$r)|$cat\""
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=90).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False, "could not read the firewall rules"
    has_rule, _, category = out.partition("|")
    ok = has_rule.strip().lower() == "true"
    if not ok:
        return False, f"no enabled rule for TCP {port}"
    if category.strip() in ("Public", ""):
        # The rule is scoped to LocalSubnet on the Private profile, so a network that has become
        # Public silently stops matching it.
        return False, f"rule exists, but this network is {category.strip() or 'unknown'}"
    return True, f"TCP {port} from LocalSubnet, {category.strip()} network"


def _panel_up(port=5095):
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=8) as r:
            return 200 <= r.status < 300
    except Exception:                                            # noqa: BLE001
        return False


def _auto_logon():
    """True when Windows logs this machine back in by itself after a reboot."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon")
        with key:
            value, _ = winreg.QueryValueEx(key, "AutoAdminLogon")
            return str(value).strip() == "1"
    except Exception:                                            # noqa: BLE001
        return False


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
    # `git ls-remote` succeeds ANONYMOUSLY on a public repo, so it reported "push access
    # confirmed" on a machine where gh was not even installed yet. A read test cannot prove a
    # write. --dry-run contacts receive-pack, which requires real credentials, and sends nothing.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    # Publishing commits to an orphan branch first, and git refuses to commit without an
    # identity. A fresh machine has none, and the failure surfaces three steps later as an
    # unrelated-looking refspec error from the push.
    who = subprocess.run(["git", "config", "user.email"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    check("git knows who you are", bool(who), who or "no user.email set",
          'git config user.name "Your Name" && git config user.email "you@example.com"')

    r = subprocess.run(["git", "push", "--dry-run", "origin", "HEAD:refs/heads/cv-auth-probe"],
                       cwd=ROOT, capture_output=True, text=True, timeout=90, env=env)
    check("can PUSH to GitHub", r.returncode == 0,
          "credentials work (dry run, nothing was sent)" if r.returncode == 0
          else (r.stderr.strip().splitlines() or [""])[-1][:90],
          "install gh, then: gh auth login && gh auth setup-git")

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
          "the session is locked or disconnected. See docs/SERVER.md: hand the session back to "
          "the console on disconnect (tools/install_session_keeper.bat registers a task that "
          "does it), and check the console is big enough for the game window")

    # Display scaling. The driver addresses FBPB3's controls by their position in pixels - it
    # has to, because the owner-drawn ones have no other handle - and those numbers were
    # measured at 100% scaling. At 125% or 150% Windows moves everything, every click lands in
    # the wrong place, and the failure looks like "the game ignored me" rather than anything to
    # do with DPI. An RDP session often defaults to the client's scaling, so this is a real risk
    # on exactly this kind of move.
    try:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor, so we read the truth
        except Exception:
            pass
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)     # LOGPIXELSX
        user32.ReleaseDC(0, hdc)
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        on_rdp = session_name.upper().startswith("RDP")
        where = "this RDP session" if on_rdp else "the console"
        check("display scaling is 100%", dpi == 96,
              f"{dpi} dpi ({dpi / 96 * 100:.0f}%), {where} is {w}x{h}",
              "Settings > System > Display > Scale = 100%. The control positions the driver "
              "clicks were measured at 100%; anything else moves them and every click misses.")
        # Measured, not guessed: FBPB3's window is 1019x762. A screen of exactly 1024x768
        # therefore fits it with five pixels to spare horizontally and six vertically - and
        # then the taskbar takes forty of those six, so it does not fit at all. The old
        # threshold of 1024x720 would have passed a screen the game cannot be displayed on.
        GAME_W, GAME_H, CHROME = 1019, 762, 48
        fits = w >= GAME_W and h >= GAME_H + CHROME
        check("screen is big enough for the game window", fits,
              f"{w}x{h}; the window is {GAME_W}x{GAME_H} and the taskbar wants ~{CHROME} more",
              "Raise the resolution. A headless card falls back to 1024x768, which is six "
              "pixels taller than the game window before the taskbar takes forty of them. A "
              "dummy HDMI/DP plug makes the console report a real monitor's size.")
        if on_rdp:
            # This number is true of the session it was measured in and NOT of the one a sim
            # will run in after a disconnect. A headless console falls back to whatever the
            # card reports with nothing plugged in - 1024x768 on the Quadro K620 here - while
            # RDP negotiates the client's own size. Reporting the RDP figure as though it were
            # the machine's is how a check passes and the thing it checks still fails.
            print(f"        NOTE: {w}x{h} is THIS RDP session. Once you disconnect, the sim")
            print("        runs on the console, which on a headless box is usually smaller")
            print("        (1024x768 is common). Check it with the session on the console:")
            print("        the game window needs to fit, or clicks land off-screen.")
            print("        A dummy HDMI/DP plug makes the console match a real monitor.")
            print("        Until then, handing the session back to the console does NOT make it")
            print("        safe to disconnect mid-sim on a box whose console is too small - the")
            print("        session stays unlocked but the game no longer fits. Stay connected.")
    except Exception as exc:
        check("display metrics readable", False, str(exc)[:60])

    if session_name.upper().startswith("RDP"):
        print("\n  NOTE: you are on an RDP session. Windows LOCKS it when you disconnect, and a")
        print("  locked session cannot be clicked - a sim running at that moment will fail.")
        print("  Hand the session back to the console before disconnecting (a scheduled task")
        print("  can do it: tools\\install_session_keeper.bat), or start sims only while")
        print("  connected. See docs/SERVER.md.")

    # ---- what happens when nobody is watching -----------------------------------------------
    section("Surviving a reboot (and a disconnect)")
    # Everything above answers "can this machine sim right now". This answers "will it still be
    # able to tomorrow", which is a different question and the one that had never been asked.
    # Found on 2026-09-20: the panel was running only as a child of an agent session, so it
    # would have died with it and nothing would have brought it back, and the task that keeps a
    # disconnected RDP session clickable had been installed in September and was simply GONE.
    tasks = _scheduled_tasks()

    panel_task = tasks.get("cheezeyverse panel")
    check("the panel restarts itself (scheduled task)", bool(panel_task),
          panel_task or "not registered",
          "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\install_serverpc_tasks.ps1")

    check("the panel is answering", _panel_up(), "http://127.0.0.1:5095/healthz",
          'schtasks /run /tn "Cheezeyverse panel"   (then check '
          '%LOCALAPPDATA%\\Cheezeyverse\\panel.log)')

    keeper = next((v for k, v in tasks.items() if "session keeper" in k or "keep desktop" in k),
                  None)
    check("a disconnected RDP session stays clickable", bool(keeper),
          keeper or "no tscon task - closing Remote Desktop will LOCK the session",
          "right-click tools\\install_session_keeper.bat -> Run as administrator. Until then, "
          "do not disconnect while a sim is running.")
    if keeper:
        # A registered task proves nothing if it points at a script that has moved. This one
        # runs as SYSTEM on an event nobody watches, so a broken path would sit there looking
        # installed until the day somebody disconnects mid-sim.
        script = ROOT / "tools" / "console_handoff.ps1"
        pointed = _task_script("Cheezeyverse session keeper")
        check("...and the script it points at is there", bool(pointed) and Path(pointed).exists(),
              pointed or "could not read the task's action",
              f"re-run the installer; the script belongs at {script}")

    # A logon-triggered task only fires if somebody logs on, and nobody is here to type a
    # password after a power cut.
    auto = _auto_logon()
    check("the machine logs itself back in after a reboot", auto,
          "AutoAdminLogon" if auto else "no auto-logon: after a reboot there is no desktop, so "
          "the panel task never fires and the game could not be clicked anyway",
          "set it with netplwiz (uncheck 'Users must enter a user name and password')")

    # A panel nobody can reach is not a running panel. The rule and the network category are
    # both things Windows changes on its own - a new adapter, a driver update, a "do you want
    # this PC to be discoverable" prompt answered No - and neither announces itself.
    reachable, why = _lan_reachable()
    check("the panel is reachable from the house", reachable, why,
          'New-NetFirewallRule -DisplayName "Cheezeyverse panel" -Direction Inbound '
          "-LocalPort 5095 -Protocol TCP -RemoteAddress LocalSubnet -Action Allow  "
          "(as administrator), and set the Ethernet profile to Private")

    backup_task = tasks.get("cheezeyverse offsite backup")
    check("the saves are copied off this drive, daily", bool(backup_task),
          backup_task or "not registered",
          "powershell -NoProfile -ExecutionPolicy Bypass -File tools\\install_serverpc_tasks.ps1")
    try:
        from tools.offsite_backup import DEST, newest
        when, payload = newest()
        if when:
            from datetime import datetime, timezone
            hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
            size = sum(f.get("bytes", 0) for f in payload.get("files") or [])
            check("that copy is recent", hours <= 48,
                  f"{hours:.0f} h old, {len(payload.get('files') or [])} files, "
                  f"{size / 1e6:.0f} MB, {DEST}",
                  "python tools/offsite_backup.py")
        else:
            check("that copy is recent", False, f"nothing in {DEST}",
                  "python tools/offsite_backup.py")
    except Exception as exc:                                     # noqa: BLE001
        check("the off-drive backup is readable", False, str(exc)[:70])

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
