from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner import leaguenews


def page(rows):
    body = "".join(f"<tr><td>&nbsp;{d}</td><td><a>{t}</a></td><td>{a}</td></tr>"
                   for d, t, a in rows)
    return f"<table><tr><td>Date</td><td>Team</td><td>Action</td></tr>{body}</table>"


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    old = [("11/1/2028", "Spies", "Cut C Same Guy")]
    (root / "transactions.htm").write_text(page(old), encoding="latin-1")
    before = leaguenews.snapshot(root)
    after_rows = [("11/8/2028", "Rails", "Traded A &amp; B to Kings"), *old, *old]
    (root / "transactions.htm").write_text(page(after_rows), encoding="latin-1")
    got = leaguenews.new_rows(before, leaguenews.snapshot(root))
    assert got == [
        {"date": "11/8/2028", "team": "Rails", "action": "Traded A & B to Kings"},
        {"date": "11/1/2028", "team": "Spies", "action": "Cut C Same Guy"},
    ], got
    rows = [
        {"date": "11/9/2028", "team": "Rails", "action": "Trade C Same Guy to Kings"},
        {"date": "11/8/2028", "team": "Rails", "action": "Traded CPU Player to Kings"},
        {"date": "11/7/2028", "team": "Rails", "action": "Cut C Same Guy"},
        {"date": "11/6/2028", "team": "Rails", "action": "Traded Same Guyton to Kings"},
    ]
    filtered = leaguenews.real_player_trades(
        rows, [{"first_name": "Same", "last_name": "Guy"}])
    assert filtered == [rows[0]], filtered
    lines = leaguenews.report_lines({"prep": filtered}, limit=1)
    assert "Same Guy" in lines[0] and len(lines) == 1, lines

print("OK  league news: reports only trades involving tracked real players")
