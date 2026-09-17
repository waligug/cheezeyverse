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

/* team pages keep their own team colour on the big banner, but the chrome matches the league */
td.teamheader {{ color: #FFF8E6 !important; letter-spacing: -.5px !important; }}
td.teamheader2 {{ color: #FFF8E6 !important; }}
"""

MENU_MARK = ('<div class="cv-wordmark">{league}<small>{season}</small></div>')


def _css_text():
    return STYLESHEET.format(**PALETTE)


def _inject_link(html, prefix):
    """Point a page at the skin. Placed right after <html> - the !important rules do the rest."""
    link = f'<meta charset="iso-8859-1"><link rel="stylesheet" href="{prefix}{CSS_NAME}">'
    if re.search(r"<html[^>]*>", html, re.I):
        return re.sub(r"(<html[^>]*>)", r"\1" + link, html, count=1, flags=re.I)
    return link + html


def _skin_menu(html, league, season):
    html = re.sub(r"<body[^>]*>", '<body class="cv-menu">', html, count=1, flags=re.I)
    mark = MENU_MARK.format(league=league, season=season)
    return re.sub(r"(<body[^>]*>)", r"\1" + mark, html, count=1, flags=re.I)


def _skin_index(html, league, season):
    html = re.sub(r"<title>.*?</title>", f"<title>{league} {season}</title>", html,
                  count=1, flags=re.I | re.S)
    return re.sub(r"cols\s*=\s*\d+", "cols=178", html, count=1, flags=re.I)


def restyle(src, dst, league="Cheezeyverse", season="", clean=True):
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
        if rel.name.lower() == "index.htm":
            html = _skin_index(html, league, season)
        html = _inject_link(html, prefix)
        if rel.name.lower() == "menu.htm":
            html = _skin_menu(html, league, season)
        target.write_text(html, encoding="latin-1", errors="replace")
        pages += 1

    (dst / CSS_NAME).write_text(_css_text(), encoding="utf-8")
    return pages
