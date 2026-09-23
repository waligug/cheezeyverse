"""ASCII-fold every latin-1 name the manifest holds, in the manifest AND in the save.

WHY. `tools/protect_rosters.py` restores a missing reserve seat by renaming an unclaimed row to
the name the manifest gives it - and `LeagueDat.rename` refuses any byte outside 32..126. So a
seat named 'Erçelik Blue' could never be restored: every sim logged

    ! renamed Kristofer Stupka but could not re-find it: unusable name part 'Erçelik'

and pro sat at 59 of 60 findable reserve slots, one signup seat permanently unusable. Seven more
rows carry the same latent fault - they are fine only because nothing has needed to rewrite them
yet.

Folding is the same transform the draft already applies for the same reason (`draft._ascii_name`:
"a name the codec cannot write is folded, not dropped"). Sepulveda, never Seplveda.
"""
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, r"C:\claude\hoops-universe")
from commissioner.codec.league_dat import LeagueDat
from commissioner import draft
import commissioner.characters as ch

LIVE = "--live" in sys.argv
MAN = Path(r"C:\claude\hoops-universe\universe\manifest.json")
man = json.loads(MAN.read_text(encoding="utf-8"))

changed_manifest = 0
for key in ("prep", "college", "pro"):
    rows = [r for r in man["players"] if r["league"] == key and any(ord(c) > 126 for c in r["name"])]
    if not rows:
        continue
    src = ch.save_path(key)
    dst = src if LIVE else Path(shutil.copy2(src, Path(sys.argv[1]) / f"{key}.dat"))
    L = LeagueDat(dst)
    wrote = 0
    for r in rows:
        old, new = r["name"], draft._ascii_name(r["name"])
        if any(p.name == new for p in L.players):
            print(f"  {key}: SKIP {old!r} -> {new!r}: that name is already taken")
            continue
        try:
            pl = L.find(old)
        except Exception:
            print(f"  {key}: {old!r} -> {new!r}  (manifest only; no row in save - the guard will restore the seat)")
            r["name"] = new
            changed_manifest += 1
            continue
        first, _, last = new.partition(" ")
        L.rename(pl, first, last)
        r["name"] = new
        changed_manifest += 1
        wrote += 1
        print(f"  {key}: {old!r} -> {new!r}  (renamed the save row too)")
    if wrote:
        L.save()
        print(f"  {key}: saved {wrote} renamed row(s)")

if LIVE:
    MAN.write_text(json.dumps(man, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"manifest rewritten, {changed_manifest} name(s) folded")
else:
    print(f"COPY RUN - manifest NOT written ({changed_manifest} name(s) would change)")
