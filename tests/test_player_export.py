"""The player export refuses rather than returning the wrong file.

`export_players` had no test at all, which is how it stayed broken: nothing else in the
commissioner calls it, so the first real use was also the first anyone knew. Every failure below
actually happened or was found in review of the two attempts at fixing it.

THE ASSERTION THIS FILE EXISTS FOR is `test_save_is_never_sent_on_faith`. PLAYER_FILE_SAVE and
EDITOR_EXIT ARE THE SAME PIXEL (917, 662). So on the one failure a retry exists for - EXPORT
swallowed while the grid is still filling - a retry that clicks SAVE anyway presses EXIT, drops
out of the League Editor, and spends its remaining attempts clicking blind on a screen the driver
has no model of. A retry that cannot survive the failure it was written for is worse than none.

`test_a_stale_export_is_not_accepted` is the other one worth keeping. The retry made it
reachable: attempt one can leave a partial CSV, and a write-wait that starts from nothing finds
that file already quiet and returns it. It would be copied into the fixture set and compared
row-by-row against a 530-player save, and read as a codec bug.

The game is faked. Driving the real one needs FBPB3, a licence and a desktop that renders, and
the point here is the decisions the method makes, which are all in Python.

    python tests/test_player_export.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commissioner.driver import fbpb3  # noqa: E402
from commissioner.driver.fbpb3 import (  # noqa: E402
    FBPB3, DriverError, EDITOR_EXPORT, PLAYER_FILE_SAVE, EDITOR_EXIT, SAVE_NAME_OK,
)

FAILS: list[str] = []


def check(name, got, want):
    if got == want:
        print(f"  ok    {name}: {got!r}")
    else:
        FAILS.append(f"{name}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {name}: got {got!r}, wanted {want!r}")


class FakeCombo:
    """The League Editor's Sort by list. `sticky` is a combo that drops the request in silence."""

    def __init__(self, sticky=False):
        self.items = ["Leagues", "Players", "Draft Pool", "Teams"]
        self.index = 2                      # starts on Draft Pool
        self.sticky = sticky

    def is_visible(self):
        return True

    def item_texts(self):
        return list(self.items)

    def select(self, value):
        if not self.sticky:
            self.index = self.items.index(value)

    def selected_index(self):
        return self.index


class FakeGame(FBPB3):
    """Just enough game to exercise the decisions. Records every click."""

    def __init__(self, target, *, export_opens=True, sticky_combo=False,
                 grid_settles=True, writes=None):
        self.clicks = []
        self.dismissed = 0
        self._target = target
        self._export_opens = export_opens
        self._sticky = sticky_combo
        self._grid_settles = grid_settles
        self._writes = writes if writes is not None else ["full"]
        self._left_editor = False
        self._attempt = -1
        self.main = SimpleNamespace(type_keys=lambda *a, **k: None,
                                    descendants=lambda **k: [FakeCombo(self._sticky)])
        self.app = SimpleNamespace(windows=lambda **k: [])

    # -- the surface export_players touches -----------------------------------------------
    def click(self, xy, wait=0, real=False):
        self.clicks.append(tuple(xy))
        if tuple(xy) == tuple(EDITOR_EXPORT):
            self._attempt += 1
            if self._export_opens:
                self._left_editor = True
        elif tuple(xy) == tuple(SAVE_NAME_OK):
            what = self._writes[min(self._attempt, len(self._writes) - 1)]
            if what == "full":
                self._target.write_text("a,b\n1,2\n", encoding="latin-1")
            elif what == "partial":
                self._target.write_text("a,b\n", encoding="latin-1")

    def dismiss_all(self, *a, **k):
        self.dismissed += 1
        self._left_editor = False           # backing out returns to the editor

    def _button_text(self, xy):
        return "" if self._left_editor else "EXPORT"

    def _wait_until_still(self, settle=1.0, timeout=60, poll=0.15, cheap=False):
        return self._grid_settles

    def _name_box_control(self):
        return SimpleNamespace(window_text=lambda: "", set_edit_text=lambda v: None)

    def _set_export_text(self, box, value):
        return value


def run(target, **kw):
    """Call the real export_players against a fake game. Returns (result, error, game)."""
    game = FakeGame(target, **kw)
    # _name_box scans real windows; hand it the fake box instead.
    src = fbpb3.FBPB3.export_players.__get__(game, FakeGame)
    import unittest.mock as mock
    (target.parent).mkdir(parents=True, exist_ok=True)
    # The name box lives on a real window scan, which a fake has none of. `windows` returning a
    # single form with a visible text box is the shape export_players looks for.
    box = SimpleNamespace(window_text=lambda: "fix", set_edit_text=lambda v: None,
                          is_visible=lambda: True)
    form = SimpleNamespace(class_name=lambda: "ThunderRT6FormDC",
                           descendants=lambda **k: [box])
    game.app = SimpleNamespace(windows=lambda **k: [form])
    with mock.patch.object(fbpb3, "DOCS", target.parent.parent):
        with mock.patch.object(FakeGame, "_wait_for",
                               staticmethod(lambda cond, timeout, poll=0.05: bool(cond()))):
            try:
                return src("fix"), None, game
            except DriverError as exc:
                return None, exc, game


def test_the_two_pixels_really_are_the_same():
    """The fact the whole retry design turns on. If this ever changes, revisit export_players."""
    print("SAVE and EXIT are the same pixel")
    check("PLAYER_FILE_SAVE == EDITOR_EXIT", tuple(PLAYER_FILE_SAVE), tuple(EDITOR_EXIT))


def test_save_is_never_sent_on_faith(tmp):
    """EXPORT swallowed: SAVE must NOT be clicked, because that pixel is EXIT."""
    print("a swallowed EXPORT never reaches SAVE")
    target = tmp / "PlayerFiles" / "fix.csv"
    _out, err, game = run(target, export_opens=False)
    check("it refuses", isinstance(err, DriverError), True)
    # (917,662) is SAVE and EXIT both. It may be pressed ONCE on the way out, to leave the
    # editor after giving up - what must never happen is pressing it mid-attempt, which would
    # exit the editor and send the remaining attempts blind.
    hits = [i for i, c in enumerate(game.clicks) if c == tuple(PLAYER_FILE_SAVE)]
    check("SAVE/EXIT never pressed mid-attempt",
          [i for i in hits if i != len(game.clicks) - 1], [])
    check("it said the click was swallowed", "swallowed" in str(err).lower(), True)


def test_a_grid_that_never_settles_refuses(tmp):
    """Timing out must not fall through into a click. It used to be indistinguishable."""
    print("a grid that never settles")
    target = tmp / "PlayerFiles" / "fix.csv"
    _out, err, game = run(target, grid_settles=False)
    check("it refuses", isinstance(err, DriverError), True)
    check("EXPORT was never clicked", tuple(EDITOR_EXPORT) in game.clicks, False)
    check("it named the list", "list" in str(err).lower(), True)


def test_a_dropped_dropdown_refuses(tmp):
    """A combo that keeps its previous view exports the Draft Pool under the Players name."""
    print("a Sort by that does not take")
    target = tmp / "PlayerFiles" / "fix.csv"
    _out, err, _game = run(target, sticky_combo=True)
    check("it refuses", isinstance(err, DriverError), True)
    check("it says the view did not change", "did not take" in str(err), True)


def test_a_stale_export_is_not_accepted(tmp):
    """The regression the retry made reachable: a partial file from a previous attempt."""
    print("a stale partial export")
    target = tmp / "PlayerFiles" / "fix.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("a,b\n", encoding="latin-1")          # left by an earlier run
    before = target.read_text(encoding="latin-1")
    out, err, _game = run(target, writes=["full"])
    check("it did not return the stale file", out is None or
          target.read_text(encoding="latin-1") != before, True)
    check("no error, because this attempt really wrote", err, None)


def main():
    with tempfile.TemporaryDirectory() as root:
        tmp = Path(root)
        test_the_two_pixels_really_are_the_same()
        test_save_is_never_sent_on_faith(tmp / "a")
        test_a_grid_that_never_settles_refuses(tmp / "b")
        test_a_dropped_dropdown_refuses(tmp / "c")
        test_a_stale_export_is_not_accepted(tmp / "d")
    print()
    for f in FAILS:
        print("  FAIL ", f)
    if FAILS:
        return 1
    print("OK  player export: SAVE is never sent while the League Editor is still up (it is the "
          "EXIT pixel), a grid that never settles and a dropdown that does not take both refuse "
          "instead of exporting the wrong thing, and a partial file from an earlier attempt is "
          "not returned as success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
