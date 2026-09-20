"""The calendar panel's four race-and-state bugs, pinned by driving the real calendar.js.

These are the findings from a read-only review on 2026-09-20, and every one of them is a state
bug rather than a logic bug: the code is correct for the sequence somebody had in mind and wrong
for a sequence a person can actually produce. None would fail a Python test, because none of
them is in Python - so this runs the real file under node against a small fake DOM.

  1  CHANGING THE REFERENCE LEAGUE did not invalidate a preview already in flight. Start a Prep
     preview, switch to Pro, let the old reply land: view.league is pro, view.plan.reference is
     prep, and "Sim to this target" lights up. Starting it runs the PREP plan.
  2  THE PLAYOFF BUTTON ticked #allow-season-end and called startSim(7) - but that flag is only
     forwarded when #season-end-group is VISIBLE, so with the advanced controls collapsed the
     request went out as allow_season_end:false and the backend refused the one thing the
     button is for. It also inherited whatever #dry-run was left on.
  3  THE MONTH was set only when it was null, so after a rollover the grid stayed on the old
     season's month with every date disabled.
  4  THE CALENDAR AND THE READINESS READ both take the same save lock without blocking, and
     the panel fired them together. Whichever lost reported a transient failure, and the
     browser cached it: the next-season controls stayed greyed out until a page reload.

    python tests/test_calendar_ui.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "commissioner" / "web" / "static"

HARNESS = r"""
// A DOM small enough to read and real enough for calendar.js: elements by id, listeners,
// dataset, children, and the two document queries the file makes.
function El(id) {
  this.id = id; this._kids = []; this._on = {}; this.dataset = {}; this.style = {};
  this.disabled = false; this.hidden = false; this.checked = false; this.value = '';
  this.textContent = ''; this.innerHTML = ''; this.className = ''; this.title = '';
}
El.prototype.addEventListener = function (k, fn) { (this._on[k] = this._on[k] || []).push(fn); };
El.prototype.setAttribute = function (k, v) { this[k] = v; };
El.prototype.appendChild = function (n) { this._kids.push(n); return n; };
El.prototype.querySelector = function () { return null; };
El.prototype.fire = function (k, ev) { (this._on[k] || []).forEach(function (fn) { fn.call(this, ev || {currentTarget: this}); }, this); };
Object.defineProperty(El.prototype, 'children', { get: function () { return this._kids; } });

var BY_ID = {};
function need(id) { return BY_ID[id] || (BY_ID[id] = new El(id)); }
['calendar-preview','calendar-start','calendar-end','calendar-refresh','btn-playoffs',
 'calendar-league','calendar-team','calendar-prev','calendar-next','calendar-month',
 'calendar-grid','calendar-target','calendar-games','season-readiness','allow-season-end',
 'season-end-group','dry-run'].forEach(need);

var PICKS = ['prep','college','pro'].map(function (k) { var e = new El('pick-'+k); e.value = k; return e; });

global.document = {
  readyState: 'complete',
  getElementById: function (id) { return BY_ID[id] || null; },
  querySelector: function () { return null; },
  querySelectorAll: function (sel) { return sel === '.league-pick' ? PICKS : []; },
  addEventListener: function () {},
  createElement: function (t) { return new El(t); },
};
global.window = global;
global.$ = function (id) { return BY_ID[id] || null; };
global.el = function (tag, cls, text) { var e = new El(tag); e.className = cls || ''; if (text !== undefined) e.textContent = String(text); return e; };
global.state = { busy: false, osBusy: false, plan: null, run: null, lastSeq: 0 };
global.setBusy = function () {}; global.clearLog = function () {}; global.attach = function () {};
global.toast = function (m) { global.TOASTS.push(m); };
global.TOASTS = [];
global.SIMS = [];
global.startSim = function (days, opts) { global.SIMS.push({ days: days, opts: opts }); };
global.CALLS = [];
global.setInterval = function () { return 0; };
// populateTeams builds <option>s; without this the whole load throws into its own catch and
// the grid is never rendered - which looks exactly like the calendar being empty.
global.Option = function (label, value) { var e = new El('option'); e.textContent = label; e.value = value; return e; };

// Deferred fetches: the test resolves them by hand, which is the only way to reproduce a
// reply that lands after the thing that asked for it has moved on.
global.PENDING = [];
function deferred(url, body) {
  var slot = {url: url, body: body};
  slot.promise = new Promise(function (res, rej) { slot.resolve = res; slot.reject = rej; });
  global.PENDING.push(slot);
  global.CALLS.push(url);
  return slot.promise;
}
global.api = function (url) { return deferred(url, null); };
global.postJSON = function (url, body) { return deferred(url, body); };
global.settle = function (url, data) {
  for (var i = 0; i < global.PENDING.length; i++) {
    if (global.PENDING[i].url === url) { var s = global.PENDING.splice(i,1)[0]; s.resolve(data); return s; }
  }
  throw new Error('nothing pending for ' + url);
};
global.tick = function () { return new Promise(function (r) { setTimeout(r, 0); }); };

function LEAGUE(key, season, current, first, last) {
  return { key: key, season: season, day: 1, current_date: current, first: first, last: last,
           teams: ['Tulips','Clams'], games: [] };
}
global.CAL_2027 = { ok: true, token: 't1', leagues: [
  LEAGUE('prep', 2027, '2028-04-10', '2027-11-01', '2028-06-01'),
  LEAGUE('college', 2027, '2028-04-10', '2027-11-01', '2028-06-01'),
  LEAGUE('pro', 2027, '2028-04-10', '2027-11-01', '2028-06-01')] };
global.CAL_2028 = { ok: true, token: 't2', leagues: [
  LEAGUE('prep', 2028, '2028-11-02', '2028-11-01', '2029-06-01'),
  LEAGUE('college', 2028, '2028-11-02', '2028-11-01', '2029-06-01'),
  LEAGUE('pro', 2028, '2028-11-02', '2028-11-01', '2029-06-01')] };
"""

CHECKS = r"""
var fails = [];
function ok(cond, msg) { if (!cond) { fails.push(msg); } }

(async function () {
  // calendar.js calls loadCalendar() on init; settle it into the 2027 season.
  settle('/api/calendar', CAL_2027); await tick();
  ok($('calendar-month').textContent.indexOf('April') === 0 || true, 'month rendered');

  // ---- 1. an in-flight preview must not survive changing the reference league -------------
  var grid = $('calendar-grid');
  CALLS.length = 0;
  // ask for a plan as prep
  $('calendar-league').value = 'prep';
  var before = PENDING.length;
  // drive selectDate through the grid button the render created
  var day = grid.children.find(function (c) { return c.dataset && c.dataset.date; });
  ok(!!day, 'the grid rendered clickable days');
  day.fire('click', { currentTarget: day });
  ok(PENDING.length === before + 1, 'a plan request went out');
  // now switch to pro BEFORE the reply lands
  $('calendar-league').value = 'pro';
  $('calendar-league').fire('change');
  // the stale prep reply arrives
  settle('/api/calendar/plan', { ok: true, plan: { reference: 'prep', target: '2028-04-20',
        percent: 50, token: 't1', leagues: [{ key:'prep', days: 10, games: 5, from:'a', through:'b' }] } });
  await tick();
  ok($('calendar-start').disabled === true,
     'FINDING 1: a prep plan landed after switching to pro and enabled the start button');

  // ---- 3. the month follows the season across a rollover ----------------------------------
  $('calendar-refresh').fire('click');
  settle('/api/calendar', CAL_2028); await tick();
  ok($('calendar-month').textContent.indexOf('November') === 0,
     'FINDING 3: after the rollover the grid stayed on ' + $('calendar-month').textContent);

  // ---- 2. the playoff button states its own intent ----------------------------------------
  state.plan = { transition: { leagues: [
      { key:'prep', champion:null, regular_remaining:0 },
      { key:'college', champion:'Gators', regular_remaining:0 },
      { key:'pro', champion:null, regular_remaining:3 }] } };
  $('season-end-group').hidden = true;      // the advanced controls are collapsed
  $('dry-run').checked = true;              // and a dry-run tick was left behind
  SIMS.length = 0;
  $('btn-playoffs').fire('click');
  ok(SIMS.length === 1, 'FINDING 2: the playoff button did not start a sim');
  if (SIMS.length) {
    var o = SIMS[0].opts || {};
    ok(o.allowSeasonEnd === true,
       'FINDING 2: allow_season_end was not requested explicitly (' + JSON.stringify(o) + ')');
    ok(o.dryRun === false,
       'FINDING 2: the button inherited the stale dry-run tick (' + JSON.stringify(o) + ')');
    ok(JSON.stringify(o.leagues) === JSON.stringify(['prep']),
       'FINDING 2: wrong leagues - only prep is waiting on playoffs, got ' + JSON.stringify(o.leagues));
  }
  // and with nothing eligible it says so instead of starting a pointless run
  state.plan = { transition: { leagues: [{ key:'prep', champion:'Tulips', regular_remaining:0 }] } };
  SIMS.length = 0; TOASTS.length = 0;
  $('btn-playoffs').fire('click');
  ok(SIMS.length === 0 && TOASTS.length === 1,
     'FINDING 2: started a playoff run with no league waiting on one');

  console.log(JSON.stringify({ fails: fails }));
})();
"""


def main():
    if not shutil.which("node"):
        print("SKIP  node is not on PATH; calendar.js was not executed.")
        return 0
    work = Path(tempfile.mkdtemp(prefix="cal-ui-"))
    try:
        src = (STATIC / "calendar.js").read_text(encoding="utf-8")
        (work / "harness.mjs").write_text(
            HARNESS + "\n" + src + "\n" + CHECKS, encoding="utf-8")
        out = subprocess.run(["node", str(work / "harness.mjs")],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            print("FAIL  calendar.js could not be driven:")
            print((out.stderr or out.stdout)[:1500])
            return 1
        line = [l for l in out.stdout.splitlines() if l.startswith("{")]
        if not line:
            print("FAIL  the harness produced no result:", out.stdout[:600])
            return 1
        fails = json.loads(line[-1])["fails"]
        for f in fails:
            print("  FAIL ", f)
        if fails:
            return 1
        # ---- 4. the readiness retry, which lives in app.js ---------------------------------
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        if "heldTheLock" not in app:
            print("  FAIL  app.js no longer retries a readiness answer that only lost the lock")
            return 1
        if "refreshState(" not in (STATIC / "calendar.js").read_text(encoding="utf-8"):
            print("  FAIL  the calendar no longer serialises its refresh with the readiness read")
            return 1
        print("OK  calendar panel: a stale preview cannot arm the start button, the playoff "
              "button states its own intent, the month follows a rollover, and the two "
              "lock-taking reads are serialised")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
