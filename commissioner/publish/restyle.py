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
from pathlib import Path

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
NAV_LINKS = [
    ("standings.htm", "Standings"), ("schedule.htm", "Schedule"),
    ("leaders.htm", "Leaders"), ("teamleaders.htm", "Teams"),
    ("transactions.htm", "Transactions"), ("injuries.htm", "Injuries"),
    ("freeagents.htm", "Free agents"), ("draft.htm", "Draft"),
    ("awards.htm", "Awards"), ("playoffs.htm", "Playoffs"), ("champs.htm", "Champs"),
]
NAV_BAR = (
    '<div class="cv-bar">'
    '{universe}'
    '<a class="cv-home" href="{prefix}index.htm" target="_top">{league}'
    '<small>{season}</small></a>'
    '<nav>{links}</nav>'
    '</div>'
)

# The three league sites and the character site are one universe to a visitor and three
# unrelated framesets to a browser. Without this strip, clicking into Prep is a dead end:
# FBPB3's own menu knows nothing outside its own league, so there is no way back to the hub
# and no way across to College or Pro except the back button.
UNIVERSE = [("prep", "Prep"), ("college", "College"), ("pro", "Pro")]


def _universe_strip(prefix, current_key):
    """Links out of this league: the hub, and the other two levels.

    `prefix` walks up to the league root, so the site root is two more levels above that -
    `site/leagues/<key>/` . Every link is target="_top" because these pages live inside a
    frameset and a plain link would load the hub into the 178px menu frame.
    """
    root = f"{prefix}../../"
    out = [f'<a class="cv-up" href="{root}index.html" target="_top">The Cheezeyverse</a>']
    for key, label in UNIVERSE:
        on = " class=on" if key == current_key else ""
        out.append(f'<a href="{root}leagues/{key}/index.htm"{on} target="_top">{label}</a>')
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
    link = f'<meta charset="iso-8859-1"><link rel="stylesheet" href="{prefix}{CSS_NAME}">'
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
        f'<a href="{prefix}{href}"{" class=on" if href == current else ""} target="_top">{label}</a>'
        for href, label in NAV_LINKS)
    return NAV_BAR.format(prefix=prefix, league=league, season=season, links=links,
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
    r'<a\s+class=linkmain\s+href=([^>\s]*?players/player(\d+)\.htm)>([^<]+)</a>', re.I)


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


def _swatch_colour(value):
    """Which of the six ability colours a number falls in, worst to best."""
    for edge, (colour, _) in zip((25, 35, 45, 55, 65), SWATCH_SCALE):
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
            best = max((ratings[k] for k in ROSTER_ATTR_COLUMNS if k in ratings), default=0)
            ceiling = max(pots.values()) if pots else best
            head, rest = new_html[:at], new_html[at:]
            colours = iter((_swatch_colour(best), _swatch_colour(ceiling)))
            rest = re.sub(r"bgcolor=#[0-9A-Fa-f]{6}",
                          lambda _: f"bgcolor={next(colours)}", rest, count=2)
            html = head + rest
            at = second
    return html


def _skin_page(html, league, season, prefix, current, key=None, ours=None,
               page_dir=None, src_root=None, player_id=None):
    """Put the nav bar just inside <body> so the page reads the same wherever it was opened."""
    html = _mark_ours(html, ours)
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
    html = re.sub(r"<title>.*?</title>", f"<title>{league} {season}</title>", html,
                  count=1, flags=re.I | re.S)
    return re.sub(r"cols\s*=\s*\d+", "cols=178", html, count=1, flags=re.I)


def restyle(src, dst, league="Cheezeyverse", season="", clean=True, key=None, ours=None):
    """Copy an FBPB3 html output folder to `dst` wearing the Cheezeyverse skin.

    Returns the number of pages skinned. `src` is left untouched.
    """
    src, dst = Path(src), Path(dst)
    if not (src / "index.htm").exists():
        raise FileNotFoundError(f"{src} does not look like an FBPB3 HTML Output folder (no index.htm)")
    if clean and dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    pages = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() not in (".htm", ".html"):
            shutil.copy2(path, target)
            continue
        html = path.read_text(encoding="latin-1")
        prefix = "../" * len(rel.parent.parts)
        name = rel.name.lower()
        if name == "index.htm":
            html = _skin_index(html, league, season)
        html = _drop_empty_images(html)
        html = _inject_link(html, prefix)
        if name == "menu.htm":
            html = _skin_menu(html, league, season, key or dst.name)
        elif name != "index.htm":
            pid = None
            m = re.fullmatch(r"player(\d+)\.htm", name)
            if m:
                pid = int(m.group(1))
            html = _skin_page(html, league, season, prefix, name, key or dst.name, ours,
                              page_dir=path.parent, src_root=src.resolve(), player_id=pid)
        target.write_text(html, encoding="latin-1", errors="replace")
        pages += 1

    (dst / CSS_NAME).write_text(_css_text(), encoding="utf-8")
    return pages
