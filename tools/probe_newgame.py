"""Launch FBPB3 and dump a screen: screenshot + every visible control with class, text, rect."""
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from commissioner.driver.fbpb3 import FBPB3  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "tmp" / "probe"
OUT.mkdir(parents=True, exist_ok=True)


def dump(g, name, combos=True):
    r0 = g.main.rectangle()
    g.screenshot(str(OUT / f"{name}.png"))
    lines = []
    for c in g.main.descendants():
        try:
            if not c.is_visible():
                continue
            r = c.rectangle()
            rel = (r.left - r0.left, r.top - r0.top, r.width(), r.height())
            txt = (c.window_text() or "")[:60]
            lines.append(f"{c.class_name():28} {str(rel):26} {txt!r}")
        except Exception:
            continue
    (OUT / f"{name}.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"--- {name}: {len(lines)} controls, shot {name}.png")
    if combos:
        for c in g.main.descendants(class_name="ThunderRT6ComboBox"):
            try:
                r = c.rectangle()
                rel = (r.left - r0.left, r.top - r0.top)
                print(f"  COMBO at {rel}: sel={c.selected_text()!r} items={c.item_texts()}")
            except Exception as e:
                print(f"  COMBO dump failed: {type(e).__name__} {e}")
    return lines


if __name__ == "__main__":
    g = FBPB3().launch()
    time.sleep(2)
    dump(g, sys.argv[1] if len(sys.argv) > 1 else "title")
