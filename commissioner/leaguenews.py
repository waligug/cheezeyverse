"""Read the transaction ledger FBPB3 already exports and report only new rows."""
from __future__ import annotations

from collections import Counter
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_tr = False
        self.in_td = False
        self.cells = []
        self.rows = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self.in_tr, self.cells = True, []
        elif self.in_tr and tag.lower() == "td":
            self.in_td, self.text = True, []

    def handle_data(self, data):
        if self.in_td:
            self.text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "td" and self.in_td:
            value = " ".join(unescape("".join(self.text)).replace("\xa0", " ").split())
            self.cells.append(value)
            self.in_td = False
        elif tag == "tr" and self.in_tr:
            if len(self.cells) >= 3 and self.cells[0].lower() != "date":
                self.rows.append(tuple(self.cells[:3]))
            self.in_tr = False


def snapshot(html_dir):
    path = Path(html_dir) / "transactions.htm"
    try:
        parser = _Rows()
        parser.feed(path.read_text(encoding="latin-1", errors="replace"))
        return parser.rows
    except OSError:
        return []


def new_rows(before, after):
    """Return new ledger entries, retaining duplicates and FBPB3's display order."""
    remaining = Counter(before)
    out = []
    for row in after:
        if remaining[row]:
            remaining[row] -= 1
        else:
            out.append({"date": row[0], "team": row[1], "action": row[2]})
    return out


def real_player_trades(rows, characters):
    """Return only trades that name one of the community's tracked characters."""
    counts = Counter()
    for character in characters or []:
        name = f'{character.get("first_name", "")} {character.get("last_name", "")}'.strip()
        if name:
            counts[name.casefold()] += 1
    # A name-only transaction ledger cannot tell two same-named characters apart. Omit that
    # ambiguous row instead of crediting both or whichever character happened to be first.
    names = [re.compile(rf'(?<!\w){re.escape(name)}(?!\w)', re.IGNORECASE)
             for name, count in counts.items() if count == 1]
    out = []
    for row in rows:
        action = row.get("action", "")
        if re.search(r"\btrad(?:e|ed|es|ing)\b", action, re.IGNORECASE) and any(
                pattern.search(action) for pattern in names):
            out.append(row)
    return out


def report_lines(news, limit=8):
    rows = [(key, row) for key, items in (news or {}).items() for row in items]
    priority = ("trade", "acquire", "sign", "claim", "waive", "cut", "activate")
    rows.sort(key=lambda item: next((i for i, word in enumerate(priority)
                                     if word in item[1]["action"].lower()), len(priority)))
    lines = []
    for key, row in rows[:limit]:
        lines.append(f'- **{key.title()} · {row["team"]}**: {row["action"]}')
    if len(rows) > limit:
        lines.append(f"- …and {len(rows) - limit} more transaction(s) on the site")
    return lines
