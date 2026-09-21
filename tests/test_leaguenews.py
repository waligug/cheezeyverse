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
    lines = leaguenews.report_lines({"prep": got}, limit=1)
    assert "Traded A & B" in lines[0] and "1 more" in lines[1], lines

print("OK  league news: parses the ledger, preserves duplicate rows, and reports only new business")
