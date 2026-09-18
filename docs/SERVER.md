# Moving the Cheezeyverse to the server

The goal: the game and the commissioner run on the server, and you press **Sim Week** from a
browser on your desktop. Your own screen is never taken over again.

This is not a rewrite. The commissioner is already a web app driving a background worker; they
just happen to share a machine today. Almost all of this is copying files.

---

## What actually has to move

| | How | Notes |
|---|---|---|
| The code | `git clone https://github.com/waligug/cheezeyverse.git` | All of it is on GitHub already |
| FBPB3 | Install it | Default location: `C:\Program Files (x86)\GDS\Fast Break Pro Basketball 3` |
| The three saves | Copy the folder | `C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\leaguedata\` |
| `.env` | Copy by hand | Gitignored on purpose. Holds the Supabase **service_role** key |
| Python packages | `pip install -r requirements.txt` | |
| GitHub access | install `gh`, then `gh auth login` **and** `gh auth setup-git` | So the server can publish the site |

Nothing in Supabase moves. It is already remote, and both machines talk to the same project.

**The desktop does not move.** That is the one genuinely tricky part, and it is the next section.

---

## The only hard part: the server needs a desktop that is being drawn

The commissioner drives FBPB3 with **real mouse clicks**. It has to: FBPB3 is a VB6 application
with no command line, no API, and owner-drawn controls that ignore posted window messages. The
only way in is to move a mouse and click.

Real clicks need a desktop that Windows is actually rendering. Three things follow:

**You do not need a monitor.** Windows keeps a virtual display for a session whether or not
anything is plugged into the card. A headless server is fine.

**You do need to stay logged in.** This cannot run as a Windows service, ever. A service has no
interactive desktop, and every click lands nowhere.

**Disconnecting from RDP is what breaks it.** When you close a Remote Desktop window, Windows
*locks* that session. The processes keep running - a sim in progress does not die immediately -
but nothing renders and nothing can be clicked, so the sim fails at its next click. This is the
failure mode to know about, because from the command line everything looks perfectly healthy.

### The fix

Run this **on the server, as Administrator**, before you disconnect:

```
tools\keep_session.bat
```

It hands your session back to the console with `tscon`. Your RDP window disconnects
immediately - that is the point, not a crash - and the session carries on as the console
session, which never locks and renders fine with no monitor.

Reconnect by RDP any time. Run it again before disconnecting next time.

That is fine once, and annoying every time. Two better ways:

**Automate it.** Run `tools\install_session_keeper.bat` once, as Administrator. It registers a
scheduled task that watches for the "session disconnected" event and runs `tscon` for you about
two seconds later. You close the Remote Desktop window exactly as normal and the session hands
itself back to the console. Nothing to remember.

**Or stop using RDP for this machine.** Set it to log in automatically at boot, and reach it
with **VNC, AnyDesk or Parsec** instead. Those attach to the console session that is already
running rather than creating a session of their own, so disconnecting from them changes nothing
at all - no lock, no tscon, nothing to install. This is the properly correct answer; RDP is
awkward here only because it is designed to own the session exclusively.

Either way you can also simply stay connected while a sim runs, which needs no setup at all -
but if you do, two things will break it and neither is obvious:

**Do not minimize the Remote Desktop window.** A minimized `mstsc` client stops the remote
session rendering its desktop, and real-click automation stops landing, exactly as though the
session were locked. Leave the window open and visible, with your mouse outside it. (The
client-side registry value `RemoteDesktop_SuppressWhenMinimized = 2` suppresses this, if you
would rather fix it properly.)

**Do not let either machine sleep.** If your desktop sleeps, RDP drops, the hand-off task moves
the session to the console, and the resolution changes underneath a running sim.

**Install only one hand-off task.** If the machine already has one, do not add a second - both
fire on the same disconnect and race each other to call `tscon`. Check with:

```
schtasks /query /fo list | findstr /I "keep desktop session keeper"
```

---

## Step by step

On the **server**:

```
git clone https://github.com/waligug/cheezeyverse.git C:\claude\hoops-universe
cd C:\claude\hoops-universe
pip install -r requirements.txt
```

Install FBPB3. Then copy from the old machine, over the file share:

```
C:\Users\Public\Documents\GDS\Fast Break Pro Basketball 3\leaguedata\    (all three CV_* folders)
C:\claude\hoops-universe\.env
```

Copy `leaguedata` while **FBPB3 is closed on both machines**. The game holds `league.dat` in
memory and writes it back on exit, so copying a save out from under a running copy gets you a
file that is half one state and half another.

### GitHub, with the two steps people miss

`gh` is not on a fresh Windows box, and `winget install GitHub.cli` wants administrator. If the
account is not an admin, the portable zip works and needs no elevation: unpack it to
`%LOCALAPPDATA%\Programs\GitHubCLI\bin` and add that folder to the user PATH.

```
gh auth login            # device code; you complete it in a browser
gh auth setup-git        # makes git itself use that login
```

The second one matters. `gh auth login` run without a terminal skips the "authenticate Git with
your GitHub credentials?" prompt, and without `setup-git` the plain `git push` inside
`publish.py` does not necessarily use the login you just did - the site then fails to publish at
the end of a Sim Week, after all the work.

Then check the machine can actually do the job:

```
python tools\server_doctor.py
```

It checks Python, the packages, the game, all three saves, the keys, Supabase, GitHub push
access - and whether this session has a desktop that can be clicked. Everything must say `ok`.

Then confirm the saves survived the trip:

```
python tools\verify_save.py
```

Expect `ALL PASS`. If a save is short a few players or a roster is wrong, restore it:

```
python tools\restore_backup.py --league prep
python tools\restore_backup.py --restore <backup id>
```

(Copy `backups\` across too, if you want that history on the server. It is 700 MB and not
required - new backups start accumulating on the first write either way.)

---

## Running it

On the server:

```
python -m commissioner.app --lan
```

It prints the address to use, something like `http://192.168.1.50:5095`. Open that from your
desktop browser and it is the same panel as before. Press **Sim Week**; the game opens **on the
server**, plays out the week there, and publishes the site. Your machine does nothing but show
the log.

`--lan` serves with **no password**, which is what you chose. On a home network that means
anything that can reach port 5095 can press Sim Week or run the offseason. It is off by default
and it announces itself at startup so it can never be on by accident. If you later want it
reachable from outside the house, use Tailscale rather than forwarding the port - the panel has
no authentication to put in front of the open internet.

### The firewall, and the trap in it

Windows Firewall will block the panel, and the obvious advice - "allow it on private networks"
- does nothing if Windows has decided your home Ethernet is a **public** network, which it
often does on a machine that was set up headless. Check first:

```
Get-NetConnectionProfile
```

If `NetworkCategory` says `Public`, the panel is unreachable no matter what you allow, because
the rule and the interface are on different profiles. Set the profile, then add one narrow rule
rather than allowing `python.exe` wholesale:

```
Set-NetConnectionProfile -InterfaceAlias Ethernet -NetworkCategory Private
New-NetFirewallRule -DisplayName "Cheezeyverse panel" -Direction Inbound `
  -Protocol TCP -LocalPort 5095 -Profile Private -RemoteAddress LocalSubnet -Action Allow
```

That opens one port, on the private profile, to the local subnet only - rather than every port
Python ever listens on, to anything that can route to the machine.

---

## After the move

The old machine keeps a copy of everything. Leave it alone until the server has run a full Sim
Week and published successfully - then it is a spare, not a liability.

Two things must never run at once:

- **FBPB3 on both machines against the same save.** They both write `league.dat` on exit and
  the last one to close wins, silently.
- **Two commissioners.** Each holds its own `_SIM_LOCK`, and that lock is what guarantees only
  one sim at a time. Two processes means two locks, which means no lock at all.

Once the server is running the universe, stop the panel on your desktop.

---

## Claude Code on the server

Claude Code is already installed there. `cd C:\claude\hoops-universe` and it can read
`CONVENTIONS.md`, `docs/STATUS.md` and this file and be current - that is what they are for.

The useful thing about having it on both: the server is where the game is, so anything touching
saves belongs there. Site work, the schema and the rules can be done from either.
