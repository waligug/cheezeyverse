"""Season Awards publishing: dated selections, safe links, badges, and future empty seasons."""
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commissioner.publish.restyle import restyle


def main():
    with tempfile.TemporaryDirectory(prefix="all-star-page-") as tmp:
        src, dst = Path(tmp) / "export", Path(tmp) / "site"
        (src / "players").mkdir(parents=True)
        original = ('<html><head><title>Season Awards</title></head><body>'
                    '<table width=800><tr><td class=newheader>Season Awards</td></tr></table>'
                    '<table><tr><td>Most Valuable Player</td><td>Existing Winner</td></tr></table>'
                    '</body></html>')
        (src / "seasonawards.htm").write_text(original, encoding="latin-1")
        (src / "index.htm").write_text('<html><head></head><body></body></html>')
        for pid, name, honours in (
                (1, "Zoé &amp; Sons", [(2027, "All-Star"), (2027, "All-Star")]),
                (2, "Past Star", [(2026, "All-Star")]),
                (3, "MVP Only", [(2027, "All-Star Game MVP")]),
                (4, "Amy &lt;Test&gt;", [(2027, "All-Star")])):
            # Match the game's actual rows: the year cell carries the &nbsp; separator.
            awards = ''.join(f'<tr><td>&nbsp;{year}</td><td>CVP {award}</td></tr>'
                             for year, award in honours)
            page = ('<html><head></head><body><table><tr>'
                    f'<td>{name}&nbsp;</td></tr><tr><td>#7 SF | 6-4, 200lbs | Tulips | '
                    f'Experience: 1 year</td></tr></table><table>{awards}</table></body></html>')
            (src / "players" / f"player{pid}.htm").write_text(page, encoding="latin-1")
        # clean=False also proves an unrelated JSON export survives this HTML-only refresh.
        dst.mkdir()
        (dst / "stats.json").write_text('{"season":2027}')
        restyle(src, dst, season="Season 2027", key="prep", clean=False,
                ours={1: {"name": "Zoé & Sons", "ratings": {}, "potentials": {}}})
        page = (dst / "seasonawards.htm").read_text(encoding="latin-1")
        section = re.search(r'<section class="cv-all-stars".*?</section>', page, re.S).group()
        assert "2027 All-Stars" in section and "2 selected" in section
        assert "Past Star" not in section and "MVP Only" not in section
        assert section.count('<li>') == 2, "a repeated honour duplicated a selection"
        assert 'Amy &lt;Test&gt;' in section and '<Test>' not in section
        assert 'Zo&#233; &amp; Sons' in section, "accented names depend on the host's encoding"
        assert section.index('Amy') < section.index('Zo&#233;'), "selections are not sorted by name"
        assert re.search(r'href="\./players/player1.htm\?v=\d+"', section), section
        assert 'cv-ours' in section, "our player lost his badge"
        assert '1st team +3' in section and '2nd team +2' in section and '3rd team +1' in section
        assert page.index('Season Awards</td>') < page.index('id="all-stars"') < page.index('Existing Winner')
        assert (src / "seasonawards.htm").read_text(encoding="latin-1") == original
        assert (dst / "stats.json").read_text() == '{"season":2027}'
        assert '.cv-star-grid' in (dst / "cheezey.css").read_text(encoding="utf-8")
        restyle(src, dst, season="Season 2028", key="prep", clean=False)
        page = (dst / "seasonawards.htm").read_text(encoding="latin-1")
        assert '2028 All-Stars' in page and '0 selected' in page
        assert 'have not been published yet' in page and 'Zoé' not in page
        assert 'Existing Winner' in page
    print("OK  All-Star awards page: season, duplicate honours, MVP exclusion, links, badges, escaping, empty state")


if __name__ == "__main__":
    main()
