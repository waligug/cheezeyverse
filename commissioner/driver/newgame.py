"""Drive FBPB3's New Game wizard: create one save holding one Cheezeyverse league.

Every control on both wizard screens is a real Win32 control (`ThunderRT6ComboBox`,
`ThunderRT6TextBox`), so this addresses them by their window-relative position and uses
pywinauto's own select/type rather than coordinate clicks. Only three things need a real mouse
click: the league row in the Available Leagues grid, the ACTIVATE button and CONTINUE, which are
owner-drawn.

Screen 1 - NEW GAME
    Save Name (422,142)  First Season (422,166)  League Password (422,190)
    Attribute Style (422,214)  Coaching (422,238)  Scouting (422,262)
    Finances (806,142)  Historical Modifiers (806,167)  Import Randomization (806,191)
    Randomize Historical Names (806,214)  Autosave (806,238)
    Available Leagues grid: first row y=461, row height 18
    ACTIVATE (275,662)  CANCEL (804,662)  CONTINUE (917,662)

Screen 2 - LEAGUE SETUP
    Prestige (411,190)  Region (411,238)  Team Locations (411,262)  Starting Stage (411,286)
    League Type (411,379)
    Initial Player Source (795,142)  Yearly Player Source (795,166)
    CONTINUE (917,662)

Prestige runs **Global > Continental > National > Regional** (Global is the strongest league),
which is why the league CSV's numeric Prestige 1..4 maps onto that list in order.
"""
from __future__ import annotations

import time

from .fbpb3 import DOCS, DriverError, FBPB3

PRESTIGE = ["Global", "Continental", "National", "Regional"]

# screen 1
SAVE_NAME = (422, 142)
FIRST_SEASON = (422, 166)
LEAGUE_PASSWORD = (422, 190)
ATTRIBUTE_STYLE = (422, 214)
COACHING = (422, 238)
SCOUTING = (422, 262)
FINANCES = (806, 142)
HISTORICAL_MODIFIERS = (806, 167)
AUTOSAVE = (806, 238)
LEAGUE_ROW_X, LEAGUE_ROW_Y, LEAGUE_ROW_H = 400, 461, 18
ACTIVATE = (275, 662)
CONTINUE = (917, 662)
BEGIN_CAREER = (328, 663)

# screen 2
PRESTIGE_COMBO = (411, 190)
TEAM_LOCATIONS = (411, 262)
STARTING_STAGE = (411, 286)
LEAGUE_TYPE = (411, 379)
INITIAL_SOURCE = (795, 142)
YEARLY_SOURCE = (795, 166)


class NewGame:
    def __init__(self, game: FBPB3):
        self.g = game

    # ---- control access --------------------------------------------------------------------
    def _at(self, rel, timeout=25):
        """Find a control by its window-relative position, WAITING for it to exist.

        A screen does not appear the instant CONTINUE is clicked, and how long it takes depends on
        what else the machine is doing. Reading a control immediately after the click worked on the
        first New Game of a session and failed on the third, which is how a rebuild deleted the
        Prep save and then could not recreate it. Poll instead of assuming.
        """
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                r0 = self.g.main.rectangle()
                for c in self.g.main.descendants():
                    try:
                        r = c.rectangle()
                        if (r.left - r0.left, r.top - r0.top) == rel:
                            return c
                    except Exception:
                        continue
            except Exception as exc:
                last = exc
            time.sleep(0.5)
        raise DriverError(f"no control at window-relative {rel} after {timeout}s"
                          + (f" ({last})" if last else ""))

    def combo(self, rel, value):
        """Delegates to FBPB3.combo. This used to be its own copy of the same three steps, and
        the copies had already drifted apart - different settle times, and only one of them
        knew that selected_text() reports the LAST option when nothing is selected, so a select
        that never took could verify as successful. One implementation, one place to fix it."""
        return self.g.combo(rel, value)

    def text(self, rel, value):
        t = self._at(rel)
        with self.g._foreground():
            t.set_focus()
            t.type_keys("^a{BACKSPACE}", set_foreground=True)
            if value:
                t.type_keys(value, with_spaces=True, set_foreground=True)
        time.sleep(0.3)
        got = t.window_text()
        if got != value:
            raise DriverError(f"text box {rel} holds {got!r} instead of {value!r}")
        return got

    # ---- the wizard ------------------------------------------------------------------------
    def create(self, save_name, first_season, league_row, prestige, roster_file,
               starting_stage="Preseason", scouting="Off", finances="Finances Off",
               autosave="Never", attribute_style="1-100", coaching="On",
               yearly_source="Fictional", team_locations="National"):
        """Run both wizard screens. `league_row` is the 0-based row of the league in the grid."""
        g = self.g
        g.click(BEGIN_CAREER, 3)

        self.text(SAVE_NAME, save_name)
        self.text(FIRST_SEASON, str(first_season))
        self.text(LEAGUE_PASSWORD, "")
        self.combo(ATTRIBUTE_STYLE, attribute_style)
        self.combo(COACHING, coaching)
        self.combo(SCOUTING, scouting)
        self.combo(FINANCES, finances)
        self.combo(HISTORICAL_MODIFIERS, "Off")
        self.combo(AUTOSAVE, autosave)

        g.click((LEAGUE_ROW_X, LEAGUE_ROW_Y + league_row * LEAGUE_ROW_H), 1)
        g.click(ACTIVATE, 2)
        g.click(CONTINUE, 5)

        self.combo(PRESTIGE_COMBO, prestige)
        self.combo(TEAM_LOCATIONS, team_locations)
        self.combo(STARTING_STAGE, starting_stage)
        self.combo(LEAGUE_TYPE, "Standalone")
        self.combo(INITIAL_SOURCE, roster_file)
        self.combo(YEARLY_SOURCE, yearly_source)

        g.click(CONTINUE, 8)
        g.save_game(path=DOCS / "leaguedata" / save_name / "league.dat")
        return save_name


def league_rows(docs):
    """Rows of the Available Leagues grid: the 3 historical leagues, then the league files by name."""
    files = sorted(p.name for p in (docs / "LeagueFiles").glob("*.csv"))
    return ["US Basketball League", "US Development League", "US Basketball Association"] + files
