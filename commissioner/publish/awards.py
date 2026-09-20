"""Add season-specific All-Star selections to the game's Season Awards page."""
from __future__ import annotations

import re
from html import escape, unescape

from ..seasonbonus import player_honours


def add_all_stars(html, html_dir, season):
    """Use the same dated honours as the bonus calculation; never borrow last year's list."""
    year = re.search(r"\b(\d{4})\b", str(season))
    if not year:
        return html
    year = int(year.group(1))
    players = sorted((p for p in player_honours(html_dir) if year in p["all_stars"]),
                     key=lambda p: unescape(p["name"]).casefold())

    def visible(value):
        # Hosts can send a UTF-8 HTTP header even though the legacy export is Latin-1.
        # Numeric entities keep accented names correct under either response encoding.
        return escape(unescape(value)).encode("ascii", "xmlcharrefreplace").decode("ascii")

    cards = "".join(
        f'<li><a class="linkmain" href="./{escape(p["page"], quote=True)}">'
        f'{visible(p["name"])}</a><span>{visible(p["position"])} &middot; '
        f'{visible(p["team"])}</span></li>' for p in players)
    listing = (f'<ul class="cv-star-grid">{cards}</ul>' if players else
               f'<p class="cv-award-empty">All-Star selections for {year} have not been '
               'published yet. They will appear here after the next export that includes them.</p>')
    section = (
        '<section class="cv-all-stars" id="all-stars" aria-labelledby="all-stars-title">'
        f'<h2 id="all-stars-title">{year} All-Stars '
        f'<small>{len(players)} selected</small></h2>'
        '<p class="cv-award-rewards">All-Star selection: <strong>+2 points</strong>. '
        'All-League: <strong>1st team +3</strong> &middot; <strong>2nd team +2</strong> '
        '&middot; <strong>3rd team +1</strong>.<br>'
        'Award points are earned at offseason settlement and share the 10-point season-bonus cap. '
        '<a href="#season-award-winners">View season award winners &darr;</a></p>'
        f'{listing}</section><div id="season-award-winners"></div>')
    # The export opens with a small title table, then its award winners. Keep the page title
    # first and the existing winners intact. A changed export layout still gets the section.
    header = re.search(r'<table\b[^>]*>\s*<tr>\s*<td\b[^>]*>\s*Season Awards'
                       r'.*?</table>', html, re.I | re.S)
    if header:
        return html[:header.end()] + section + html[header.end():]
    return re.sub(r'(<body\b[^>]*>)', lambda m: m.group(1) + section, html, count=1, flags=re.I)
