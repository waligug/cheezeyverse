"""Re-skin FBPB3's generated HTML Output as the Cheezeyverse.

FBPB3 writes the same two-frame site the Stabbyverse uses: `index.htm` (frameset), `menu.htm`
(left nav) and a pile of data pages, plus `rosters/roster<id>.htm` per team. Every page carries its
own inline `<style>` block keyed to the league colour, and there is no way to theme it from inside
the game.

So we post-process: keep the structure exactly (frames, menu, tables, links all stay), inject one
stylesheet whose rules are `!important`, and let it win over the inline block regardless of order.
Nothing is reflowed and no table is rewritten, which means a future FBPB3 patch changing the markup
degrades to "looks stock" rather than breaking.
"""
from __future__ import annotations

import re
import shutil
import hashlib
import json
from datetime import datetime
from pathlib import Path

from .awards import add_all_stars

CSS_NAME = "cheezey.css"

PALETTE = {
    "gold": "#F2B705",      # young cheddar, the primary chrome
    "deep": "#D9901A",      # aged, for header bands
    "crust": "#8A5A00",     # rind
    "ink": "#2E2100",       # text on gold
    "cream": "#FFF8E6",     # page background
    "cream2": "#F3E4BE",    # zebra stripe
    "line": "#D9C48A",
    "link": "#7A4B00",
    "human": "#1D5C8A",     # the "human coach" blue FBPB3 uses for played teams
}

STYLESHEET = """/* Cheezeyverse skin over FBPB3 HTML Output. Every rule is !important because the game
   writes its own <style> block into each page and we cannot control the order. */
@font-face {{ font-family: 'CVFallback'; src: local('Trebuchet MS'), local('Verdana'); }}

html, body {{
  background: {cream} !important;
  color: {ink} !important;
  font-family: 'Trebuchet MS', Verdana, sans-serif !important;
}}

/* ---- the left menu: a wedge of cheese, holes and all ---- */
body.cv-menu {{
  background-color: {gold} !important;
  background-image:
    radial-gradient(circle at 22% 14%, {crust}22 0 9px, transparent 10px),
    radial-gradient(circle at 74% 27%, {crust}22 0 6px, transparent 7px),
    radial-gradient(circle at 34% 46%, {crust}22 0 11px, transparent 12px),
    radial-gradient(circle at 80% 63%, {crust}22 0 7px, transparent 8px),
    radial-gradient(circle at 26% 78%, {crust}22 0 8px, transparent 9px),
    radial-gradient(circle at 66% 90%, {crust}22 0 5px, transparent 6px),
    linear-gradient(160deg, {gold} 0%, {deep} 100%) !important;
  background-repeat: no-repeat !important;
  margin: 0 !important;
  padding: 0 !important;
}}
.cv-wordmark {{
  font: 700 17px/1.05 'Trebuchet MS', Verdana, sans-serif !important;
  color: {ink} !important;
  padding: 12px 10px 10px 12px !important;
  letter-spacing: .5px !important;
  text-shadow: 0 1px 0 #FFFFFF80 !important;
  border-bottom: 2px solid {crust} !important;
  margin-bottom: 6px !important;
}}
.cv-wordmark small {{
  display: block !important;
  font: 600 10px/1.4 'Trebuchet MS', Verdana, sans-serif !important;
  letter-spacing: 1.6px !important;
  text-transform: uppercase !important;
  color: {crust} !important;
  text-shadow: none !important;
}}
body.cv-menu table {{ width: 100% !important; border-collapse: collapse !important; }}
body.cv-menu td {{ padding: 0 !important; }}
a.menulink {{
  display: block !important;
  color: {ink} !important;
  font: 600 11px/1 'Trebuchet MS', Verdana, sans-serif !important;
  padding: 6px 10px 6px 12px !important;
  text-decoration: none !important;
  border-left: 3px solid transparent !important;
}}
a.menulink:hover {{
  background: #FFFFFF55 !important;
  border-left-color: {crust} !important;
}}

/* ---- the nav bar every content page carries ---- */
/* Inside the frameset, FBPB3's own menu is already down the left-hand side and this bar
   repeats it - two navigations doing the same job, which is what it looked like. The bar
   exists for pages opened DIRECTLY: a shared link, a search result, any of the 600-odd
   player pages, none of which carry a menu of their own. So: show it when the page stands
   alone, hide it when it is framed. The class is set by the snippet _skin_page injects. */
html.cv-framed .cv-bar {{ display: none !important; }}

/* Season honours stay beside the game's award winners, with links to every player. */
.cv-all-stars {{ max-width: 800px; margin: 12px 0 24px; }}
.cv-all-stars h2 {{ font-size: 22px; margin: 0 0 8px; color: {ink}; }}
.cv-all-stars h2 small {{ font-size: 12px; font-weight: normal; color: {crust}; margin-left: 8px; }}
.cv-award-rewards, .cv-award-empty {{ font-size: 12px; line-height: 1.7; color: {crust}; }}
.cv-star-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px; list-style: none; padding: 0; margin: 14px 0 0; }}
.cv-star-grid li {{ padding: 10px 12px; background: {cream2}; border: 1px solid {line};
  border-radius: 6px; min-width: 0; }}
.cv-star-grid a {{ font-size: 13px !important; font-weight: bold; overflow-wrap: anywhere; }}
.cv-star-grid span {{ display: block; font-size: 11px; margin-top: 5px; color: {crust}; }}
@media (max-width: 600px) {{ .cv-star-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
@media (max-width: 360px) {{ .cv-star-grid {{ grid-template-columns: 1fr; }} }}

/* When this copy was published. Pushed to the far end and kept quiet: it is the answer to "am I
   looking at a stale page?", which matters only when somebody is already asking. */
.cv-bar .cv-published {{
  margin-left: auto !important;
  font-size: 10.5px !important;
  font-weight: 600 !important;
  opacity: .7 !important;
  white-space: nowrap !important;
}}

/* A real person's player, among four hundred the game invented. Without this they are
   indistinguishable on a roster page, which is the opposite of the point: the whole site
   exists so somebody can find THEIR guy. */
a.cv-ours {{
  background: {gold} !important;
  color: {ink} !important;
  font-weight: 700 !important;
  padding: 1px 5px 1px 4px !important;
  border-radius: 3px !important;
  border: 1px solid {crust} !important;
  text-decoration: none !important;
}}
a.cv-ours:hover {{ background: {deep} !important; }}
/* ROOKIES, in the human blue FBPB3 already uses for a played team - a colour the eye already
   reads as "not ordinary filler" on these pages. Underlined rather than filled, so a leaderboard
   of thirty names does not turn into a block of colour and so it can never be confused with the
   gold badge that means the player is YOURS. */
a.cv-rookie {{
  color: {human} !important;
  font-weight: 700 !important;
  text-decoration: underline !important;
  text-underline-offset: 2px !important;
}}
/* No ::before glyph here on purpose. The obvious cheese-emoji escape is a trap twice
   over: STYLESHEET is an ordinary Python string, so a CSS escape beginning with a digit
   is read as an OCTAL escape and the generated CSS ends up holding a real 0x01 byte; and
   these pages are served as iso-8859-1, which a linked stylesheet inherits, so a literal
   emoji would not survive either. The gold pill and border say 'this one is a person'
   perfectly well on their own. */

.cv-legend {{
  font: 11px 'Trebuchet MS', Verdana, sans-serif !important;
  color: {crust} !important;
  margin: 2px 0 10px 0 !important;
  display: flex !important; flex-wrap: wrap !important;
  align-items: center !important; gap: 4px 6px !important;
}}
.cv-legend i {{
  width: 11px !important; height: 11px !important; display: inline-block !important;
  border: 1px solid {crust} !important; border-radius: 2px !important;
}}
.cv-legend b {{ font-weight: 700 !important; margin-right: 3px !important; }}

/* A result with no box score behind it. Left looking like data rather than a broken link. */
.cv-noscore {{ color: {ink} !important; }}

.cv-bar {{
  display: flex !important;
  flex-wrap: wrap !important;
  align-items: baseline !important;
  gap: 4px 14px !important;
  background: linear-gradient(170deg, {gold}, {deep}) !important;
  border-bottom: 3px solid {crust} !important;
  padding: 7px 12px 8px !important;
  margin: -8px -8px 12px !important;
  font-family: 'Trebuchet MS', Verdana, sans-serif !important;
}}
.cv-bar .cv-home {{
  font-weight: 700 !important;
  font-size: 15px !important;
  color: {ink} !important;
  text-decoration: none !important;
  margin-right: 6px !important;
}}
.cv-bar .cv-home small {{
  display: block !important;
  font-weight: 600 !important;
  font-size: 9.5px !important;
  letter-spacing: 1.4px !important;
  text-transform: uppercase !important;
  color: {crust} !important;
}}
.cv-bar nav {{ display: flex !important; flex-wrap: wrap !important; gap: 2px 12px !important; }}
.cv-bar nav a {{
  color: {ink} !important;
  font-size: 11.5px !important;
  font-weight: 600 !important;
  text-decoration: none !important;
  padding: 2px 0 !important;
  border-bottom: 2px solid transparent !important;
}}
.cv-bar nav a:hover {{ border-bottom-color: {crust} !important; }}
.cv-bar nav a.on {{ border-bottom-color: {ink} !important; }}
.cv-universe {{
   display: flex !important; flex-wrap: wrap !important; gap: 2px 14px !important;
   width: 100% !important; margin: 0 0 6px 0 !important; padding: 0 0 5px 0 !important;
   border-bottom: 1px solid {crust} !important; font-size: 9pt !important;
}}
.cv-universe a {{
   color: {ink} !important; text-decoration: none !important; opacity: .75 !important;
}}
.cv-universe a:hover {{ opacity: 1 !important; text-decoration: underline !important; }}
.cv-universe a.on {{ opacity: 1 !important; font-weight: 700 !important; }}
.cv-universe .cv-up {{ font-weight: 700 !important; opacity: 1 !important; }}
body.cv-menu .cv-universe {{
   flex-direction: column !important; gap: 3px !important; margin: 0 0 8px 0 !important;
   padding: 0 8px 8px 8px !important;
}}

/* ---- data pages ---- */
td.main, td.header, td.plainheader, td.headerbg, td.teamheader, td.teamheader2,
td.tableheader, td.newheader {{ font-family: 'Trebuchet MS', Verdana, sans-serif !important; }}

td.plainheader, td.newheader {{
  background: transparent !important;
  color: {crust} !important;
  font-size: 21px !important;
  letter-spacing: -.4px !important;
  padding: 10px 0 6px 0 !important;
}}
td.header, td.headerbg {{
  background: {deep} !important;
  color: {ink} !important;
  font-weight: 700 !important;
  padding: 3px 5px !important;
  border-bottom: 2px solid {crust} !important;
}}
td.tableheader {{ color: {crust} !important; }}
tr.row1 {{ background: {cream} !important; }}
tr.row2 {{ background: {cream2} !important; }}
tr.teamcolor {{ background: {crust} !important; color: #FFF8E6 !important; }}
td.main {{ padding: 2px 5px !important; }}
a.linkmain {{ color: {link} !important; font-weight: 600 !important; }}
a.linkhuman {{ color: {human} !important; font-weight: 700 !important; }}
a:hover {{ text-decoration: underline !important; }}

/* Dropping the missing player photos leaves the cell that held them behind; an empty cell with a
   team-coloured background reads as a rendering fault. */
/* Empty cells are usually FBPB3 padding and look like holes once the skin is on, so they
   are flattened - EXCEPT the ones carrying a bgcolor. Those are the ability swatches beside
   every player (left = what he is now, right = what he could become), and they are empty on
   purpose: the colour IS the value. Blanking them turned every swatch on every roster page
   into an empty white box, which is what they looked like until somebody asked. */
td:empty:not([bgcolor]), td.cv-blank:not([bgcolor]),
td.teamheader:empty:not([bgcolor]), td.headerbg:empty:not([bgcolor]) {{
  background: transparent !important; padding: 0 !important;
}}

/* The swatches themselves. FBPB3 wraps each in a 1px-bordered table of its own; give them a
   consistent size and a border that belongs to this skin rather than 1996. */
td[bgcolor] {{
  padding: 0 !important;
  width: 11px !important;
  height: 11px !important;
  border-radius: 2px !important;
}}
td.main > table {{ border-collapse: separate !important; }}
td.main > table, td.main > table td {{ border-color: {crust} !important; }}

/* team pages keep their own team colour on the big banner, but the chrome matches the league */
td.teamheader {{ color: #FFF8E6 !important; letter-spacing: -.5px !important; }}
td.teamheader2 {{ color: #FFF8E6 !important; }}
"""

MENU_MARK = ('<div class="cv-wordmark">{league}<small>{season}</small></div>')

# FBPB3's site is a frameset: the menu only exists inside index.htm, so every page reached
# directly - a shared link, a search result, and all 400-odd player pages - arrives with no
# navigation whatsoever. This bar goes on every content page so each one stands on its own.
# EVERYTHING FBPB3'S OWN MENU OFFERS, because this bar is now the only navigation there is.
# The frameset used to mean a visitor got the game's left menu (18 links) or this bar (11),
# depending entirely on how they arrived - deep link versus the league's front door - and the
# two disagreed about what existed. The seven at the end are the ones that were only ever
# reachable from the left menu.
NAV_LINKS = [
    ("standings.htm", "Standings"), ("playoffstandings.htm", "Playoff standings"),
    ("schedule.htm", "Schedule"),
    ("leaders.htm", "Leaders"), ("playoffleaders.htm", "Playoff leaders"),
    ("teamleaders.htm", "Teams"),
    ("transactions.htm", "Transactions"), ("injuries.htm", "Injuries"),
    ("freeagents.htm", "Free agents"), ("waiverwire.htm", "Waivers"),
    ("potentialfreeagents.htm", "Upcoming FAs"),
    ("draft.htm", "Draft"), ("staff.htm", "Staff"), ("humancoaches.htm", "Coaches"),
    ("awards.htm", "Awards"), ("seasonawards.htm", "Season awards"),
    ("playoffs.htm", "Playoffs"), ("champs.htm", "Champs"),
]
NAV_BAR = (
    '<div class="cv-bar">'
    '{universe}'
    '<a class="cv-home" href="{home}" target="_top">{league}'
    '<small>{season}</small></a>'
    '<nav>{links}</nav>'
    '{published}'
    '</div>'
)

# The three league sites and the character site are one universe to a visitor and three
# unrelated framesets to a browser. Without this strip, clicking into Prep is a dead end:
# FBPB3's own menu knows nothing outside its own league, so there is no way back to the hub
# and no way across to College or Pro except the back button.
UNIVERSE = [("prep", "Prep"), ("college", "College"), ("pro", "Pro")]


# ---- cache busting ---------------------------------------------------------------------------
# GitHub Pages serves every page with `Cache-Control: max-age=600` and the URL of a page never
# changes when its contents do. So for ten minutes after a publish, a browser or the CDN in front
# of it will hand back yesterday's copy of a page that has already been replaced - measured:
# Age: 113 from cache-yyc1430031-YYC while the bare URL held the new bracket. Ctrl+F5 does not
# reliably help, because the stale copy can be at the edge rather than in the browser.
#
# A query string IS part of the cache key on Pages, so stamping every link this re-skin writes
# with the publish time gives each publish its own set of URLs and neither cache can answer from
# the last one. This is why the stamp goes on LINKS rather than on the pages themselves: a page
# cannot version its own address.
#
# WHAT THIS CANNOT FIX: the address somebody types or has bookmarked. `leagues/prep/playoffs.htm`
# with no query string is still subject to the ten minutes, and nothing written here can change
# that. The bar therefore also SHOWS the publish time, so the answer to "am I looking at the old
# one?" is on the page instead of being a guess.
STAMP = ""


def _published_label():
    """"published 14:32" from the stamp, or "" when there is none.

    Read back OUT of the stamp rather than calling now() a second time: two clocks read a
    moment apart can straddle a minute, and a bar that disagrees with the URLs on the same page
    is worse than no bar at all - it is the thing somebody checks to decide whether to trust
    what they are looking at.
    """
    try:
        return "published " + datetime.strptime(STAMP, "%Y%m%d%H%M%S").strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


# href="x.htm", href='x.htm' and bare href=x.htm - FBPB3 writes all three, and the unquoted form
# is the common one in its own generated links.
_HREF = re.compile(r'(href=)(["\']?)([^"\'>\s]+)(\2)', re.I)


def _stamp_links(html):
    """Put the publish stamp on every relative .htm/.html link in a finished page.

    Only page links. Images and the stylesheet are handled where they are written, and stamping
    an <img> here would also hit the ones `_drop_empty_images` deliberately left alone.
    """
    if not STAMP:
        return html

    def one(m):
        eq, q, href, _close = m.groups()
        base = href.split("#", 1)[0]
        if not base.lower().endswith((".htm", ".html")):
            return m.group(0)
        # Keep a fragment attached to the END of the URL: "a.htm#top" must become
        # "a.htm?v=1#top", not "a.htm#top?v=1", which addresses a query INSIDE the fragment and
        # is simply a different, non-existent anchor.
        frag = href[len(base):]
        return f"{eq}{q}{_v(base)}{frag}{q}"

    return _HREF.sub(one, html)


def _v(href):
    """`href` with the publish stamp on it, so a new publish cannot be served from a cache.

    Anchors and absolute URLs are left alone: `#top` addresses the current page, and an external
    URL is not ours to version. An href that already carries a query keeps it.
    """
    if not STAMP or not href or href.startswith(("#", "http://", "https://", "mailto:")):
        return href
    return f"{href}{'&' if '?' in href else '?'}v={STAMP}"


def _universe_strip(prefix, current_key):
    """Links out of this league: the hub, and the other two levels.

    `prefix` walks up to the league root, so the site root is two more levels above that -
    `site/leagues/<key>/` . Every link is target="_top" because these pages live inside a
    frameset and a plain link would load the hub into the 178px menu frame.
    """
    root = f"{prefix}../../"
    out = [f'<a class="cv-up" href="{_v(root + "index.html")}" target="_top">The Cheezeyverse</a>']
    for key, label in UNIVERSE:
        on = " class=on" if key == current_key else ""
        href = _v(f"{root}leagues/{key}/index.htm")
        out.append(f'<a href="{href}"{on} target="_top">{label}</a>')
    return f'<div class="cv-universe">{"".join(out)}</div>'



def _css_text():
    """The stylesheet, proven free of control characters before it is written.

    STYLESHEET is an ordinary Python string, so a CSS escape that starts with a digit is read
    as an OCTAL escape and silently becomes a 0x01 byte in the output. That happened: a badge
    rendered as the literal text "F9C0 A0" instead of a glyph, and nothing else anywhere
    showed a symptom. tests/test_no_control_chars.py cannot catch it, because that scans
    tracked source and this file is generated at publish time. So it is checked here, where it
    is made, on every single publish.
    """
    css = STYLESHEET.format(**PALETTE)
    bad = sorted({ord(c) for c in css if ord(c) < 0x20 and c not in "\t\n\r"})
    if bad:
        raise ValueError(
            "the stylesheet contains control character(s) "
            + ", ".join(f"0x{b:02X}" for b in bad)
            + " - almost certainly a CSS escape read as a Python octal escape. "
              "Use a raw string, or drop the escape.")
    return css


def _inject_link(html, prefix):
    """Point a page at the skin. Placed right after <html> - the !important rules do the rest."""
    link = f'<meta charset="iso-8859-1"><link rel="stylesheet" href="{_v(prefix + CSS_NAME)}">'
    if re.search(r"<html[^>]*>", html, re.I):
        return re.sub(r"(<html[^>]*>)", r"\1" + link, html, count=1, flags=re.I)
    return link + html


# FBPB3 writes the player photo as src="<images>/<Picname>" and our generated players have no
# Picname, so 352 pages per league carried an <img> pointing at the images DIRECTORY. Browsers
# render that as a broken-image box. There is no photo to supply, so the tag goes.
_EMPTY_IMG = re.compile(r"<img[^>]*src=[\"']?[^\"'\s>]*/[\"']?[\s>][^>]*>", re.I)


BODY_BG = r"""\sbackground=["']?[^"'\s>]*/["']?(?=[\s>])"""


def _drop_empty_images(html):
    def keep(m):
        tag = m.group(0)
        src = re.search(r"src=[\"']?([^\"'\s>]*)", tag, re.I)
        value = src.group(1) if src else ""
        return "" if (not value or value.endswith("/")) else tag
    html = re.sub(r"<img[^>]*>", keep, html, flags=re.I)
    # A cell whose only content was that image is now blank, and CSS `td:empty` will not match it
    # because the whitespace survives - so mark it here, where we can see it is really empty.
    # Includes cells that already carry a class: the photo sat in a team-coloured one, and an
    # empty coloured block is exactly what reads as a broken image to anyone looking at it.
    def blank(m):
        # Keep every attribute: colspan and width hold the table together, and dropping them
        # collapses the layout. Only the styling class is added.
        attrs = m.group(1)
        # A cell with a bgcolor is not blank, whatever is between its tags: it is an ability
        # swatch and the colour is the whole point. Tagging it cv-blank is what hid them.
        if "bgcolor" in attrs.lower():
            return f"<td{attrs}></td>"
        return (f"<td{attrs} class=cv-blank></td>" if "class=" not in attrs.lower()
                else f"<td{attrs}></td>")
    html = re.sub(r"<td([^>]*)>(?:\s|&nbsp;)*</td>", blank, html, flags=re.I)
    # `background=<dir>` on <body> is the same empty-filename bug as the images.
    html = re.sub(BODY_BG, "", html, flags=re.I)
    return html


def _nav_bar(league, season, prefix, current, key=None):
    links = "".join(
        f'<a href="{_v(prefix + href)}"{" class=on" if href == current else ""} '
        f'target="_top">{label}</a>'
        for href, label in NAV_LINKS)
    # The publish time, visible. Versioned links keep a CLICKED page fresh, but a typed or
    # bookmarked address cannot be versioned and stays cacheable for ten minutes - so the page
    # says which copy it is instead of leaving somebody to guess whether it is stale or broken.
    stamped = (f'<span class="cv-published" title="when this page was published. Links carry '
               f'this stamp so a click is never served from a cache; a typed or bookmarked '
               f'address can still be up to ten minutes old.">{_published_label()}</span>'
               if STAMP else "")
    return NAV_BAR.format(home=_v(prefix + "index.htm"), league=league, season=season,
                          links=links, published=stamped,
                          universe=_universe_strip(prefix, key))


# Runs before the bar is painted, so a framed page never flashes two navigations. Kept inline
# and tiny on purpose: these are 600+ static files per league and an external script would be
# 600 more requests.
# The swatch scale, worst to best. FBPB3 documents none of this; the order was established
# empirically by correlating every swatch on every roster page in all three leagues (780 rows)
# against the mean of that row's fourteen ability ratings. The ranges overlap - the game
# evidently grades relative to position rather than on a flat average - so this is the ORDER,
# not a set of thresholds. Purple has so far only ever appeared as a potential.
SWATCH_SCALE = [
    ("#B0040C", "poor"), ("#F2662A", "below average"), ("#EDBE30", "average"),
    ("#307B1A", "good"), ("#0052C3", "very good"), ("#9402B8", "elite"),
]


def _legend():
    dots = "".join(f'<i style="background:{c}" title="{w}"></i>' for c, w in SWATCH_SCALE)
    return ('<div class="cv-legend"><b>Ability</b>'
            f'<span>left = now, right = ceiling</span>{dots}'
            f'<span>{SWATCH_SCALE[0][1]} to {SWATCH_SCALE[-1][1]}</span></div>')


FRAME_TEST = ('<script>if(window.top!==window.self)'
              "document.documentElement.className+=' cv-framed';</script>")


PLAYER_LINK = re.compile(
    r'''<a\s+class=["']?linkmain["']?\s+href=(["']?[^>\s]*?players/player(\d+)\.htm["']?)>([^<]+)</a>''',
    re.I)


# The sixteen columns FBPB3 prints in a player page's Attributes table, in order, mapped to the
# codec's own names. 3pUsage and Fouling are not shown by the game.
ATTR_COLUMNS = [
    "InsideScoring", "JumpShot", "FtShot", "3pShot", "Handling", "Passing", "OReb", "DReb",
    "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "Quickness", "Strength",
    "Jumping", "Stamina",
]
# The Potential row only carries the twelve that have one, in the same left-to-right order.
POT_COLUMNS = [
    "InsideScoring", "JumpShot", "FtShot", "3pShot", "Handling", "Passing", "OReb", "DReb",
    "PostDefense", "PerimeterDefense", "Stealing", "Blocking",
]

ATTR_ROW = re.compile(
    r"(<tr[^>]*>\s*<td[^>]*>&nbsp;(Current|Potential):</td>)(.*?)(</tr>)", re.S | re.I)
CELL = re.compile(r"<td class=main[^>]*>.*?</td>", re.S | re.I)


def _live_attributes(html, live):
    """Replace the Attributes rows with what the SAVE says, for one of our characters.

    FBPB3's player pages do not print the live sheet. They print a season-start snapshot out of
    the ratings-history block, and for a character who claimed a dormant reserve slot that
    snapshot is THE RESERVE'S OWN RATINGS - the 3-12 junk the slot was built with. So every
    character's page showed his predecessor's numbers, Current all under ten and Potential
    straight F, for the whole of his first season, while the game itself played him off the real
    values. It is the single most visible thing on the site and it was wrong for everybody.

    CONVENTIONS forbids writing the history rows in the save, and it is right to: they are the
    game's own record and rewriting them is how a save gets quietly corrupted. So this is fixed
    where it is displayed rather than where it is stored.

    Potentials are printed as numbers rather than the game's letter grades, deliberately. A
    person who spent points on a ceiling should be able to see the ceiling move.
    """
    ratings = live.get("ratings") or {}
    potentials = live.get("potentials") or {}
    if not ratings:
        return html

    def row(m):
        head, kind, body, tail = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        cells = CELL.findall(body)
        want = ATTR_COLUMNS if kind == "current" else POT_COLUMNS
        if len(cells) < len(want):
            return m.group(0)          # not the table we think it is; leave it alone
        source = ratings if kind == "current" else potentials
        out = []
        for i, cell in enumerate(cells):
            if i < len(want) and want[i] in source:
                out.append(f'<td class=main width=40 align=center>{int(source[want[i]])}</td>')
            else:
                out.append(cell)
        return head + "".join(out) + tail

    return ATTR_ROW.sub(row, html, count=2)


def _mark_ours(html, ours):
    """Badge every link to a character's player page, so his owner can spot him on a roster.

    Matched on FBPB3's own player id rather than on the name: two players can share a name -
    the stock rosters have several - and a name match would badge the wrong man. The id comes
    from `league_player_ids`, recorded when the character claimed his slot.
    """
    if not ours:
        return html

    def swap(m):
        href, pid, name = m.group(1), int(m.group(2)), m.group(3)
        if pid not in ours:
            return m.group(0)
        who = ours[pid]
        label = who.get("name") if isinstance(who, dict) else who
        return f'<a class="linkmain cv-ours" href={href} title="{label}">{name}</a>'

    return PLAYER_LINK.sub(swap, html)


def _mark_rookies(html, rookies):
    """Badge every link to a first-year player, so a leaderboard says who is new.

    Runs AFTER `_mark_ours` and cannot fight with it: that pass rewrites a character's anchor to
    `class="linkmain cv-ours"`, which no longer matches PLAYER_LINK, so one of ours who is also a
    rookie keeps his gold badge. Gold means "yours" and outranks "new".

    Matched on FBPB3's own player id, like `_mark_ours`, because two players can share a name.
    """
    if not rookies:
        return html

    def swap(m):
        href, pid, name = m.group(1), int(m.group(2)), m.group(3)
        if pid not in rookies:
            return m.group(0)
        return f'<a class="linkmain cv-rookie" href={href} title="Rookie">{name}</a>'

    return PLAYER_LINK.sub(swap, html)


BOX_LINK = re.compile(r'<a\s+class=linkmain\s+href=([^>\s]*?boxes/box[\w-]+\.htm)>([^<]*)</a>',
                      re.I)


def _unlink_dead_boxes(html, page_dir, src_root):
    """Turn a link to a box score that was never written into plain text.

    FBPB3's schedule links every result to `boxes/boxN-N.htm` whether or not it exported the
    box scores - and ours does not export them, so all 52 links on the prep schedule alone are
    404s. A score that looks clickable and goes nowhere is worse than one that is plainly just
    a score: people try it twice and conclude the site is broken.

    Checked against the file actually being there, so the day box scores ARE turned on the
    links start working again with no change here.
    """
    if "boxes/box" not in html:
        return html

    def swap(m):
        href, text = m.group(1), m.group(2)
        target = (page_dir / href).resolve()
        try:
            target.relative_to(src_root)            # never look outside the export
        except ValueError:
            return m.group(0)
        if target.exists():
            return m.group(0)
        return f'<span class="cv-noscore" title="no box score was exported">{text}</span>'

    return BOX_LINK.sub(swap, html)


# The roster table's sixteen columns are NOT in the same order as the player page's. Its header
# runs Ins Jps Fts 3ps Hnd Pas Orb Drb Psd Prd Stl Blk Qkn Jmp Str Sta - JUMPING BEFORE STRENGTH,
# where the player page has Strength first. Verified against the codec on unchanged fillers.
# Reusing the other list here silently swaps two of a character's ratings.
ROSTER_ATTR_COLUMNS = [
    "InsideScoring", "JumpShot", "FtShot", "3pShot", "Handling", "Passing", "OReb", "DReb",
    "PostDefense", "PerimeterDefense", "Stealing", "Blocking", "Quickness", "Jumping",
    "Strength", "Stamina",
]

# FBPB3's roster markup defeats row-splitting: each ability swatch is a NESTED table carrying
# its own </tr>, so "<tr> ... </tr>" stops six cells early, and even splitting on <tr is fragile.
# So this does not try to isolate a row. It finds the player's own link, walks forward past his
# two swatches, and rewrites the sixteen numeric cells that follow - which is exactly the run of
# cells the Attributes table puts after them.
NUM_CELL = re.compile(r"<td class=main>\s*(\d+)\s*</font></td>", re.I)


# Where FBPB3 itself puts each colour, measured on 780 fillers whose pages still match the save,
# as the MEAN of the abilities (current) and the mean of the potentials (future):
#     current   red 21.1   orange 37.6   gold 40.7   green 55.8   blue 63.6
#     potential red 30.7   orange 44.5   gold 49.1   green 48.4   blue 65.8   purple 74.1
# The thresholds below are the midpoints between neighbours. They will not match the game
# exactly - its ranges overlap, so it evidently grades relative to position rather than on a
# flat average - but they put a player in the right neighbourhood.
#
# Two separate scales, because the potential one sits much higher: a ceiling of 75 is GOLD to
# FBPB3, and purple needs about 98. Colouring both from one scale, off the MAX rather than the
# mean it was derived from, painted every single character's ceiling purple - which flatters
# five fourteen-year-olds into elite prospects and makes the colour meaningless.
CURRENT_EDGES = (29, 39, 48, 60, 70)
POTENTIAL_EDGES = (37, 47, 58, 66, 74)


def _swatch_colour(value, edges=CURRENT_EDGES):
    """Which of the six ability colours a MEAN falls in, worst to best."""
    for edge, (colour, _) in zip(edges, SWATCH_SCALE):
        if value < edge:
            return colour
    return SWATCH_SCALE[-1][0]


def _live_roster_row(html, ours):
    """Put the live sheet into our characters' rows on a team's roster page.

    A friend opens his team's page as readily as his own, and there his player was a row of 5s
    beside teammates on 30s, with two red swatches - the same stale season-start snapshot that
    the player page carried.

    The ratings do not begin at the first number after the name: position, age, height and weight
    come first, and age and weight are numbers too. The two swatch tables sit between them and the
    ratings, so the anchor is the second `</table></td>` after the link.
    """
    if not ours:
        return html
    live_by_pid = {pid: v for pid, v in ours.items()
                   if isinstance(v, dict) and v.get("ratings")}
    if not live_by_pid:
        return html

    for pid, live in live_by_pid.items():
        ratings, pots = live["ratings"], live.get("potentials") or {}
        marker = f"players/player{pid}.htm"
        at = 0
        while True:
            at = html.find(marker, at)
            if at == -1:
                break
            at += len(marker)
            # Two swatches follow the name, then the ratings. If there are not two within a
            # short reach this is the plain Roster table rather than the Attributes one.
            first = html.find("</table></td>", at)
            second = html.find("</table></td>", first + 1) if first != -1 else -1
            if second == -1 or second - at > 900:
                continue
            after = second + len("</table></td>")
            cells = list(NUM_CELL.finditer(html, after))[:len(ROSTER_ATTR_COLUMNS)]
            if len(cells) < len(ROSTER_ATTR_COLUMNS) or cells[0].start() - after > 60:
                continue

            pieces, cursor = [html[:after]], after
            for column, cell in zip(ROSTER_ATTR_COLUMNS, cells):
                pieces.append(html[cursor:cell.start()])
                value = ratings.get(column)
                pieces.append(cell.group(0) if value is None
                              else f"<td class=main>{int(value)}</font></td>")
                cursor = cell.end()
            pieces.append(html[cursor:])
            new_html = "".join(pieces)

            # Recolour his two swatches, which were painted from the same stale numbers.
            # The MEAN, not the max - that is the basis the thresholds were measured on, and
            # a max is always the player's one good skill.
            have = [ratings[k] for k in ROSTER_ATTR_COLUMNS if k in ratings]
            now = sum(have) / len(have) if have else 0
            ceiling = sum(pots.values()) / len(pots) if pots else now
            head, rest = new_html[:at], new_html[at:]
            colours = iter((_swatch_colour(now, CURRENT_EDGES),
                            _swatch_colour(ceiling, POTENTIAL_EDGES)))
            rest = re.sub(r"bgcolor=#[0-9A-Fa-f]{6}",
                          lambda _: f"bgcolor={next(colours)}", rest, count=2)
            html = head + rest
            at = second
    return html


# Named STAT_* rather than CELL/ROW/TABLE: this module already defines a CELL regex above,
# for the roster pages, and a second one of the same name would quietly replace it for
# everything defined after this point.
STAT_CELL = re.compile(r"<t[dh]\b[^>]*>.*?</t[dh]>", re.S | re.I)
STAT_ROW = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S | re.I)
STAT_TABLE = re.compile(r"<table\b[^>]*>.*?</table>", re.S | re.I)


def _cell_text(tag):
    """The visible text of one cell, upper-cased, with FBPB3's padding nbsp stripped."""
    return re.sub(r"<[^>]+>", "", tag).replace('\xa0', " ").strip().upper()


def _drop_repeated_stl(html):
    """Remove FBPB3's duplicate STL column from the season-totals tables.

    The game prints the header "... REB AST STL TO STL BLK PF ..." - steals twice, two columns
    apart - on every player page. It is the game's own quirk, not a parse error, and
    seasonbonus has always known about it (TOTAL_COLUMNS carries an STL_repeat entry so that
    BLK is read from the seventeenth number rather than the sixteenth). But the PAGE was never
    touched, so every player's career table showed steals twice and nothing said why.

    Verified before removing anything: across 380 data rows in prep's player pages, the two
    columns are identical in 380 and differ in none. So the second is a duplicate and dropping
    it loses nothing.

    Done per table and per row on the raw cell tags rather than by rebuilding the row, so
    colours, alignment and the cache-stamped links inside a cell survive untouched.
    """
    def fix_table(match):
        table = match.group(0)
        rows = STAT_ROW.findall(table)
        if not rows:
            return table
        target = width = None
        for row in rows:
            cells = STAT_CELL.findall(row)
            if [_cell_text(c) for c in cells].count("STL") == 2:
                target = [i for i, c in enumerate(cells) if _cell_text(c) == "STL"][1]
                width = len(cells)
                break
        if target is None:
            return table
        def fix_row(rmatch):
            row = rmatch.group(0)
            # BY POSITION, not by searching for the cell's text. The two STL cells are
            # identical strings - that is the whole point - so `row.find(cell)` returns the
            # FIRST one and removes the wrong column, silently shifting every number after it
            # left by one. Caught by the unit check: the header came back as AST TO STL BLK.
            spans = [m.span() for m in STAT_CELL.finditer(row)]
            # Only rows of the SAME shape as the header. A totals table can carry a spanning
            # note or a blank spacer row, and dropping a cell from one of those would shift
            # everything after it by one column - which is the failure this is fixing.
            if len(spans) != width:
                return row
            lo, hi = spans[target]
            return row[:lo] + row[hi:]
        return STAT_ROW.sub(fix_row, table)
    return STAT_TABLE.sub(fix_table, html)


def _skin_page(html, league, season, prefix, current, key=None, ours=None,
               page_dir=None, src_root=None, player_id=None, rookies=None):
    """Put the nav bar just inside <body> so the page reads the same wherever it was opened."""
    html = _drop_repeated_stl(html)
    html = _mark_ours(html, ours)
    html = _mark_rookies(html, rookies)
    if player_id is not None and ours and player_id in ours:
        html = _live_attributes(html, ours[player_id])
    elif current.startswith("roster"):
        html = _live_roster_row(html, ours)
    if page_dir is not None and src_root is not None:
        html = _unlink_dead_boxes(html, page_dir, src_root)
    bar = FRAME_TEST + _nav_bar(league, season, prefix, current, key)
    if "bgcolor=#" in html.replace(" ", ""):        # a page that actually shows swatches
        bar += _legend()
    if re.search(r"<body[^>]*>", html, re.I):
        return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + bar, html, count=1, flags=re.I)
    return re.sub(r"(<html[^>]*>)", lambda m: m.group(1) + bar, html, count=1, flags=re.I)


def _skin_menu(html, league, season, key=None):
    html = re.sub(r"<body[^>]*>", '<body class="cv-menu">', html, count=1, flags=re.I)
    mark = MENU_MARK.format(league=league, season=season) + _universe_strip("", key)
    return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + mark, html, count=1, flags=re.I)


def _skin_index(html, league, season):
    """Replace FBPB3's frameset with a redirect, so the site has ONE navigation.

    THE BUG THIS FIXES. FBPB3 ships a two-frame site: index.htm holds a 178px menu frame and a
    data frame. A visitor who came through the league's front door therefore got the game's own
    menu down the left and this skin's bar hidden; a visitor who followed a deep link - from a
    character page, a shared URL, a bookmark, any of the 400 player pages - got the bar instead.
    Same site, two different navigations, decided by the route in rather than by anything the
    reader did, and the two did not even offer the same links.

    Keeping the frameset and fixing the disagreement would still leave two layouts. So the
    frameset goes: every page now stands alone and carries the bar, which is the one this skin
    controls, the one that survives being bookmarked, and the only one that works on a phone.
    Nothing is lost - NAV_LINKS now carries all eighteen of the menu's links, and no content
    page targets a frame (6,368 links across 398 pages, every one target=_top).

    menu.htm is still written, and is now simply unreferenced.
    """
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{league} {season}</title>'
            f'<meta http-equiv="refresh" content="0; url={_v("standings.htm")}">'
            f'<link rel="canonical" href="standings.htm"></head>'
            f'<body><p>Opening <a href="{_v("standings.htm")}">{league} standings</a>...</p>'
            f'<script>location.replace("{_v("standings.htm")}");</script></body></html>')


def restyle(src, dst, league="Cheezeyverse", season="", clean=True, key=None, ours=None,
            cache_path=None, rookies=None):
    """Copy an FBPB3 html output folder to `dst` wearing the Cheezeyverse skin.

    Returns the number of pages skinned. `src` is left untouched.
    """
    global STAMP
    # One stamp for this whole re-skin, taken once. Taken per page instead, pages published in
    # the same run would link to each other by different URLs and each first click would miss
    # the cache it was meant to be using.
    STAMP = datetime.now().strftime("%Y%m%d%H%M%S")
    src, dst = Path(src), Path(dst)
    cache_path = Path(cache_path) if cache_path else None
    if not (src / "index.htm").exists():
        raise FileNotFoundError(f"{src} does not look like an FBPB3 HTML Output folder (no index.htm)")
    # The game rewrites every output file, and the old publisher then transformed every one of
    # the 3,000+ pages and stamped every link again even when the bytes were unchanged. Keep a
    # content manifest outside site/ so unchanged pages retain their already-skinned output.
    # Hash this module into the context: a skin-code edit invalidates the cache automatically.
    context = hashlib.sha256(
        Path(__file__).read_bytes()
        + f"\0{league}\0{season}\0{key or dst.name}".encode("utf-8")
    ).hexdigest()
    old_cache = {}
    if cache_path and cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if payload.get("context") == context:
                old_cache = payload.get("files") or {}
        except (OSError, ValueError):
            pass
    incremental = bool(old_cache) and dst.exists()
    if clean and dst.exists() and not incremental:
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    pages = 0
    new_cache = {}
    ours = ours or {}
    membership = json.dumps({str(pid): who.get("name", "") for pid, who in ours.items()},
                            sort_keys=True, separators=(",", ":"))
    roster_data = json.dumps([ours, sorted(rookies or ())], sort_keys=True,
                             separators=(",", ":"), default=str)
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = path.read_bytes()
        extra = membership
        name = rel.name.lower()
        player = re.fullmatch(r"player(\d+)\.htm", name)
        if player and int(player.group(1)) in ours:
            extra += json.dumps(ours[int(player.group(1))], sort_keys=True,
                                separators=(",", ":"), default=str)
        if rel.parts and rel.parts[0].lower() == "rosters":
            extra += roster_data
        digest = hashlib.sha256(raw + extra.encode("utf-8")).hexdigest()
        rel_key = rel.as_posix()
        new_cache[rel_key] = digest
        # seasonawards also reads award pages beside itself, so its own source hash is not its
        # complete input. It is one page; always rebuilding it is cheaper and safer.
        if (incremental and name != "seasonawards.htm" and target.exists()
                and old_cache.get(rel_key) == digest):
            if path.suffix.lower() in (".htm", ".html"):
                pages += 1
            continue
        if path.suffix.lower() not in (".htm", ".html"):
            shutil.copy2(path, target)
            continue
        html = raw.decode("latin-1")
        prefix = "../" * len(rel.parent.parts)
        if name == "index.htm":
            html = _skin_index(html, league, season)
        html = _drop_empty_images(html)
        html = _inject_link(html, prefix)
        if name == "menu.htm":
            html = _skin_menu(html, league, season, key or dst.name)
        elif name != "index.htm":
            if name == "seasonawards.htm":
                html = add_all_stars(html, src, season)
            pid = None
            m = re.fullmatch(r"player(\d+)\.htm", name)
            if m:
                pid = int(m.group(1))
            html = _skin_page(html, league, season, prefix, name, key or dst.name, ours,
                              page_dir=path.parent, src_root=src.resolve(), player_id=pid,
                              rookies=rookies)
        # LAST, after every other rewrite. FBPB3 writes its own links - rosters, teams, the 400
        # player pages - and those are the ones somebody follows from a standings page. Stamping
        # only the bar would leave the bar fresh and everything it leads to cacheable, which is
        # the half-fix that looks like a fix. It runs at the end so it stamps the final hrefs,
        # including the ones _skin_page rewrote, and cannot be undone by a later pass.
        html = _stamp_links(html)
        target.write_text(html, encoding="latin-1", errors="replace")
        pages += 1

    # Remove only files previously owned by this transform. Generated JSON lives in the same
    # folder and is intentionally absent from the cache, so it cannot be swept away here.
    for missing in set(old_cache) - set(new_cache):
        (dst / Path(missing)).unlink(missing_ok=True)
    css = _css_text()
    css_path = dst / CSS_NAME
    if not css_path.exists() or css_path.read_text(encoding="utf-8") != css:
        css_path.write_text(css, encoding="utf-8")
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps({"context": context, "files": new_cache}, separators=(",", ":")),
                       encoding="utf-8")
        tmp.replace(cache_path)
    return pages
