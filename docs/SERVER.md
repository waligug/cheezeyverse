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
| GitHub access | `gh auth login`, once | So the server can publish the site |

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

If you would rather not think about it: only start sims while you are connected, and stay
connected until the run finishes.

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

Windows Firewall will ask to allow Python the first time. Allow it on **private** networks only.

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
