/* =====================================================================================
 * Commissioner control panel - the browser half.
 *
 * Plain JS, no build step, no framework. The server renders the page once (so the leagues
 * are readable with JS off); this file keeps it live:
 *
 *   - starts a sim and attaches an EventSource to /api/sim/stream
 *   - on load, if a sim is already running, re-attaches to it instead of showing an idle
 *     button, so a refresh mid-sim loses nothing
 *   - polls nothing that a stream can tell it
 *
 * All server data arrives through the #boot JSON block, never through inlined template
 * expressions, so this file stands alone and `node --check` means something.
 * ===================================================================================== */
'use strict';

var BOOT = {};
try {
  BOOT = JSON.parse(document.getElementById('boot').textContent) || {};
} catch (err) {
  BOOT = {};
}

var state = {
  run: BOOT.run || null,
  busy: !!BOOT.busy,
  source: null,     // the live EventSource
  lastSeq: 0,
  autoApprove: null,
  offseasonOK: !!(BOOT.offseason && BOOT.offseason.ok),
  plan: BOOT.plan || null,
  osBusy: false,        // a dry run is in flight: it holds the same lock a sim does
  refusedSeason: null,  // the season a refusal was about, so Force asks for that one
  pace: BOOT.pace || null   // fitted from real runs; null when the log cannot say yet
};

var $ = function (id) { return document.getElementById(id); };

function el(tag, cls, text) {
  var node = document.createElement(tag);
  if (cls) { node.className = cls; }
  if (text !== undefined && text !== null) { node.textContent = String(text); }
  return node;
}

var toastTimer = null;
function toast(message, bad) {
  var box = $('toast');
  box.textContent = message;
  box.className = bad ? 'bad' : '';
  box.hidden = false;
  if (toastTimer) { clearTimeout(toastTimer); }
  toastTimer = setTimeout(function () { box.hidden = true; }, bad ? 9000 : 4000);
}

function banner(message) {
  var box = $('js-banner');
  if (!message) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = message;
}

function api(path, options) {
  return fetch(path, options).then(function (resp) {
    return resp.json().catch(function () {
      return { ok: false, error: 'HTTP ' + resp.status + ' with no JSON body' };
    }).then(function (data) {
      data._status = resp.status;
      return data;
    });
  });
}

function postJSON(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
}

/* ---------------------------------------------------------------------- the log ---- */
function clearLog(note) {
  var log = $('log');
  log.innerHTML = '';
  if (note) { log.appendChild(el('div', 'empty', note)); }
  setProgress(0, false);
}

function setProgress(pct, isError) {
  var bar = $('progress');
  bar.className = 'progress' + (isError ? ' err' : '');
  bar.firstElementChild.style.width = Math.max(0, Math.min(100, pct || 0)) + '%';
}

function appendEvent(ev) {
  var log = $('log');
  var placeholder = log.querySelector('.empty');
  if (placeholder) { log.removeChild(placeholder); }

  var kind = ev.kind || 'step';
  if (kind === 'idle') {
    log.appendChild(el('div', 'row meta', ev.message || 'idle'));
    return;
  }
  if (kind === 'open') {
    if (ev.backlog) {
      log.appendChild(el('div', 'row meta',
        'reattached to run ' + ev.run + ' (' + ev.backlog + ' earlier events)'));
    }
    return;
  }

  var row = el('div', 'row step-' + String(ev.step || 'info'));
  row.appendChild(el('span', 't', ev.clock || ''));
  row.appendChild(el('span', 's', ev.step || 'info'));
  row.appendChild(el('span', 'lg', ev.league || ''));
  row.appendChild(el('span', 'm', ev.message || ''));
  row.appendChild(el('span', 'took', '+' + (ev.took === undefined ? '0.0' : ev.took) + 's'));
  log.appendChild(row);

  if (ev.traceback) {
    var pre = el('pre', null, ev.traceback);
    log.appendChild(pre);
  }
  if (ev.pct !== null && ev.pct !== undefined) {
    setProgress(ev.pct, ev.step === 'error');
  }
  if (ev.step === 'error') {
    setProgress(100, true);
    // Loud, and it stays: an error must not scroll away silently.
    toast('Step failed: ' + (ev.message || 'see the log'), true);
  }
  if (ev.step === 'failed') {
    // One character the offseason could not move. The run carries on by design, so this must
    // not claim the bar is finished - but it does get said out loud, twice.
    toast(ev.message || 'A character could not be moved.', true);
  }
  if (ev.step === 'refused') {
    toast(ev.message || 'Refused.', true);
  }
  log.scrollTop = log.scrollHeight;
}

/* ---------------------------------------------------------------- the SSE stream ---- */
function attach(runId) {
  detach();
  var url = '/api/sim/stream?after=' + state.lastSeq + (runId ? '&run=' + encodeURIComponent(runId) : '');
  var source = new EventSource(url);
  state.source = source;

  source.onmessage = function (message) {
    var ev;
    try { ev = JSON.parse(message.data); } catch (err) { return; }
    if (ev.seq) { state.lastSeq = ev.seq; }
    appendEvent(ev);
    if (ev.kind === 'end') {
      detach();
      finishRun(ev);
    }
    if (ev.kind === 'idle') { detach(); }
  };
  source.onerror = function () {
    // EventSource reconnects on its own; ?after= means the replay starts where we stopped.
    // Only say something if the run is over and it still cannot connect.
    if (!state.busy) { detach(); }
  };
}

function detach() {
  if (state.source) { state.source.close(); state.source = null; }
}

function finishRun(ev) {
  state.busy = false;
  setButtons();
  var okay = ev.status === 'ok';
  var refused = !!ev.refused || ev.status === 'refused';
  $('run-chip').textContent = refused ? 'refused' : (okay ? 'finished' : 'ERROR');
  $('run-chip').className = 'chip ' + (okay ? 'ok' : (refused ? 'warn' : 'bad'));
  $('run-line').textContent = ev.message || '';
  if (!okay && !refused) {
    banner('The last run stopped with an error: ' + (ev.error || ev.message || ''));
  }
  var wasOffseason = ev.kind_of_run === 'offseason' ||
    (state.run && state.run.kind === 'offseason');
  if (wasOffseason) { loadOffseasonResult(); }
  refreshState();
  refreshHistory();
  refreshPending();
}

/* ------------------------------------------------------------------- starting up ---- */
function pickedLeagues() {
  var boxes = document.querySelectorAll('.league-pick');
  var keys = [];
  var selectable = 0;
  for (var i = 0; i < boxes.length; i++) {
    // A league whose final is decided is not one of "all of them" any more. Sending null once
    // one is finished would ask the server for every league including that one, and it refuses
    // the WHOLE run over it - so a finished prep would block college and pro from simming at
    // all, which reads as the panel being broken rather than as a rule.
    if (boxes[i].disabled) { continue; }
    selectable += 1;
    if (boxes[i].checked) { keys.push(boxes[i].value); }
  }
  return keys.length === selectable && selectable === boxes.length ? null : keys;
}

/* The tightest ticked league: {key, left} for the one with the fewest regular-season days
 * remaining, or null when nothing ticked has a known count. Derived from the SELECTED boxes,
 * not from all of them - untick college and pro and the answer becomes prep's number, which is
 * the whole reason the count lives per league rather than in one box. */
function tightestLeague() {
  var boxes = document.querySelectorAll('.league-pick');
  var best = null;
  for (var i = 0; i < boxes.length; i++) {
    if (!boxes[i].checked || boxes[i].disabled) { continue; }
    var raw = boxes[i].getAttribute('data-left');
    if (raw === null || raw === '') { continue; }   // no opinion: never guess one
    var left = parseInt(raw, 10);
    if (isNaN(left)) { continue; }
    // `left` is the GUARD'S number and decides when the playoff control appears; `clicks` is
    // what reaches the end of the season and decides what the box suggests. They are different
    // - 8 and 10 in prep - and conflating them is what put nine and ten days in a dead zone.
    var clicks = parseInt(boxes[i].getAttribute('data-clicks'), 10);
    if (best === null || left < best.left) {
      best = { key: boxes[i].value, left: left, clicks: isNaN(clicks) ? left : clicks };
    }
  }
  return best;
}

/* Show the playoff control only when the days asked for would actually cross somebody's season
 * end, and name the league it would cross. Also nudges the day box down to what fits inside the
 * regular season, so the common case - sim right up to the end - needs no arithmetic. */
function refreshSeasonEnd() {
  var group = $('season-end-group');
  if (!group) { return; }
  var tight = tightestLeague();
  var asked = parseInt($('days-chunk').value, 10) || 0;
  var crossing = tight !== null && asked > tight.left;
  group.hidden = !crossing;
  if (!crossing) {
    $('allow-season-end').checked = false;   // never leave it armed once it stops applying
    return;
  }
  var label = $('season-end-label');
  if (label) {
    label.title = tight.left === 0
      ? tight.key + ' has no regular-season days left: every remaining day is the playoffs.'
      : tight.key + ' has ' + tight.left + ' regular-season day(s) left, so ' + asked
        + ' would cross into its playoffs.';
  }

  /* NOTHING STOPS AFTER ROUND ONE. There is no such mechanism in the driver - the number typed
   * here IS the control, and the only hard stops are the champion refusal and the sim halting
   * when the calendar stops moving. So the panel has to say what number buys what, per league,
   * because a single game and a best-of-five are not the same ask: prep and college decide round
   * one in ONE playoff day, pro needs five game days spread over about nine calendar ones. */
  var hint = $('season-end-hint');
  if (hint) {
    var box = document.querySelector('.league-pick[value="' + tight.key + '"]');
    var round1 = box ? parseInt(box.getAttribute('data-round-one'), 10) : NaN;
    hint.textContent = isNaN(round1) ? ''
      : tight.clicks + ' finishes ' + tight.key + '’s season, ' + (tight.clicks + round1)
        + ' plays its first round';
  }
}

/* The day box should suggest what actually fits. 21 was a fixed default chosen when every league
 * had room; at the season boundary it is simply a number that gets refused.
 *
 * ONLY WHILE UNTOUCHED, and in BOTH directions. An earlier version only ever lowered the number,
 * which quietly downgraded the ask: loading with prep ticked set the box to 2, and unticking prep
 * to sim pro - which had ten days of room - left it sitting at 2. The person would have got a
 * two-day sim they did not ask for and no indication why. Once they type, the number is theirs
 * and nothing here overwrites it; a value that crosses a boundary surfaces the playoff control
 * instead of being silently trimmed. */
var daysTouched = false;

function suggestDays() {
  var tight = tightestLeague();
  var box = $('days-chunk');
  // the CLICK count, not the guard's under-estimate: the guard's number stops short of the end
  if (box && !daysTouched && tight !== null && tight.clicks > 0) {
    box.value = tight.clicks;
  }
  refreshSeasonEnd();
}

/* How long a run will take, in words. Most of a sim is fixed work done once per league, so this
 * is NOT days x a per-day figure - see sim_pace() in app.py, which fits both terms from the runs
 * that have actually happened.
 *
 * Rounded hard and prefixed "about", because the fit's own worst error on its samples is tens of
 * percent. A number like "16.7 min" would claim a precision this does not have; the point is
 * only to tell somebody whether they are starting a coffee or an evening. */
function paceText(leagues, days) {
  var p = state.pace;
  if (!p || !p.fixed || !p.per_day || !days) { return ''; }
  var secs = p.fixed * leagues + p.per_day * leagues * days;
  var mins = secs / 60;
  if (mins < 1.5) { return 'about a minute'; }
  if (mins < 60) { return 'about ' + Math.round(mins) + ' min'; }
  var hours = mins / 60;
  return 'about ' + (hours < 3 ? (Math.round(hours * 2) / 2) : Math.round(hours)) + ' hr';
}

/* The estimate beside each button, kept current as leagues and days change. Shown per BUTTON
 * because the two ask for different numbers of days, and a single figure next to one of them
 * would be read as belonging to both. */
function refreshPace() {
  var boxes = document.querySelectorAll('.league-pick');
  var picked = 0;
  for (var i = 0; i < boxes.length; i++) {
    if (boxes[i].checked && !boxes[i].disabled) { picked += 1; }
  }
  var pairs = [['pace-week', 'days-week', 7], ['pace-chunk', 'days-chunk', 21]];
  for (var j = 0; j < pairs.length; j++) {
    var out = $(pairs[j][0]);
    if (!out) { continue; }
    var days = parseInt($(pairs[j][1]).value, 10) || pairs[j][2];
    var text = picked ? paceText(picked, days) : '';
    out.textContent = text;
    out.title = text && state.pace
      ? 'Fitted from ' + state.pace.samples + ' real runs. Most of a sim is fixed work per '
        + 'league, so this is not days times a rate. Past runs have landed within '
        + Math.round(state.pace.spread * 100) + '% of this curve.'
      : '';
  }
}

function startSim(days) {
  if (state.busy) { toast('A sim is already running.', true); return; }
  var leagues = pickedLeagues();
  if (leagues !== null && leagues.length === 0) { toast('Pick at least one league.', true); return; }
  var dry = $('dry-run').checked;
  var crossBox = $('allow-season-end');
  // Only send it when the control is actually showing. A checkbox left ticked from an earlier
  // arrangement of leagues must not silently grant permission to cross a different league's
  // boundary than the one it was ticked for.
  var cross = !!(crossBox && crossBox.checked && !$('season-end-group').hidden);

  setBusy(true, 'starting...');
  clearLog(null);
  state.lastSeq = 0;
  postJSON('/api/sim/start', { days: days, leagues: leagues, dry_run: dry,
                               allow_season_end: cross }).then(function (data) {
    if (!data.ok) {
      setBusy(false, 'refused');
      banner(data.error || 'The sim was refused.');
      toast(data.error || 'The sim was refused.', true);
      if (data.busy && data.run) { state.busy = true; setButtons(); attach(data.run.id); }
      return;
    }
    banner('');
    state.run = data.run;
    setBusy(true, describeRun(data.run));
    attach(data.run.id);
  }).catch(function (err) {
    setBusy(false, 'failed to start');
    toast('Could not reach the panel: ' + err, true);
  });
}

function describeRun(run) {
  if (!run) { return ''; }
  if (run.kind === 'calendar' && run.calendar_plan) {
    return 'Season target: ' + run.calendar_plan.percent + '% · ' + run.calendar_plan.leagues.filter(function(r) { return r.days > 0; }).map(function(r) { return r.key + ': ' + r.days + ' days'; }).join(', ');
  }
  if (run.kind === 'offseason') {
    return (run.dry_run ? 'DRY RUN - ' : '') + 'offseason for season ' +
      (run.season === null || run.season === undefined ? "(the store's current season)" : run.season) +
      (run.force ? ' - FORCED' : '') +
      ', started ' + (run.started_at || '').replace('T', ' ');
  }
  return (run.dry_run ? 'DRY RUN - ' : '') +
    (run.leagues ? run.leagues.join(', ') : 'all leagues') +
    ', ' + run.days + ' day' + (run.days === 1 ? '' : 's') +
    (run.allow_season_end ? ', allowed into the playoffs' : '') +
    ', started ' + (run.started_at || '').replace('T', ' ');
}

function setBusy(busy, line) {
  state.busy = busy;
  setButtons();
  $('run-chip').className = 'chip' + (busy ? ' busy' : '');
  $('run-chip').innerHTML = '';
  if (busy) { $('run-chip').appendChild(el('span', 'live-dot')); }
  $('run-chip').appendChild(document.createTextNode(busy ? 'running' : 'idle'));
  $('busy-chip').hidden = !busy;
  if (line !== undefined) { $('run-line').textContent = line; }
}

function setButtons() {
  var locked = state.busy || state.osBusy;
  $('btn-week').disabled = locked;
  $('btn-chunk').disabled = locked;
  // A dry run reads league.dat and an offseason writes it, so both are locked out by a sim
  // exactly as a sim is locked out by them. The server refuses either way; this is only so the
  // button looks like what it will do.
  $('btn-os-preview').disabled = locked || !state.offseasonOK;
  $('btn-os-run').disabled = locked || !state.offseasonOK || !(state.plan && state.plan.transition && state.plan.transition.ready);
  if (window.refreshCalendarButtons) { window.refreshCalendarButtons(); }
  var force = $('btn-os-force');
  if (force) { force.disabled = locked || !state.offseasonOK || !$('os-force-ack').checked; }
}

/* --------------------------------------------------------------------- refreshers ---- */
function refreshState() {
  api('/api/state').then(function (data) {
    if (!data.ok) { return; }
    renderLeagues(data.universe);
    if (data.plan) { renderPlan(data.plan); }
    if (data.busy && !state.source) {
      // a sim started somewhere else (another tab): follow it
      state.busy = true;
      setBusy(true, describeRun(data.run));
      attach(data.run ? data.run.id : null);
    }
  }).catch(function () { /* the header just stays as it was */ });
}

function renderLeagues(universe) {
  if (!universe || !universe.leagues) { return; }
  for (var i = 0; i < universe.leagues.length; i++) {
    var lg = universe.leagues[i];
    var card = document.querySelector('.league[data-key="' + lg.key + '"]');
    if (!card) { continue; }
    var dds = card.querySelectorAll('dd');
    var stage = card.querySelector('.stage');
    if (stage && lg.stage) {
      stage.innerHTML = '';
      stage.appendChild(el('b', null, lg.stage));
      var tail = [];
      if (lg.season) { tail.push('season ' + lg.season); }
      if (lg.day !== undefined && lg.day !== null) { tail.push('day ' + lg.day); }
      if (tail.length) { stage.appendChild(document.createTextNode(' · ' + tail.join(' · '))); }
    }
    if (window.renderCalendarDates) { window.renderCalendarDates(); }
    if (dds[0] && lg.games_played !== undefined && lg.games_played !== null) {
      dds[0].textContent = lg.games_played;
    }
    if (dds[1] && lg.characters !== undefined && lg.characters !== null) {
      dds[1].textContent = lg.characters;
    }
    if (dds[2] && lg.reserve_free !== undefined && lg.reserve_free !== null) {
      dds[2].textContent = lg.reserve_free + ' / ' + lg.reserve_total;
      dds[2].className = lg.reserve_free === 0 ? 'low' : '';
    }
  }
  var store = universe.store || {};
  $('store-chip').textContent = 'store: ' + (store.kind || 'unknown') +
    (store.configured ? ' ok' : ' (not configured)');
  $('game-chip').textContent = universe.game_running === true ? 'FBPB3 running'
    : (universe.game_running === false ? 'FBPB3 closed' : 'FBPB3 unknown');
}

/* ------------------------------------------------------------------- the offseason ---- */
/* Three things happen here and they are deliberately different weights:
 *
 *   Dry run   - one request, answers with everything it *would* do, writes nothing.
 *   Run       - a confirm, then a background run streaming into the same log as a sim.
 *   Force     - only ever offered after a refusal, behind its own checkbox and its own
 *               confirm, because it is for a run that did not finish and nothing else.
 */
function renderPlan(plan) {
  if (!plan) { return; }
  state.plan = plan;
  if (window.renderSeasonReadiness) { window.renderSeasonReadiness(plan); }
  $('os-season-chip').textContent = plan.season ? ('season ' + plan.season + ' → ' + (Number(plan.season) + 1)) : 'season unknown';
  $('os-last-chip').textContent = plan.last_offseason
    ? ('last run: ' + plan.last_offseason) : 'never run';
  setButtons();
  if (!plan.available) { return; }

  var host = $('os-plan');
  host.innerHTML = '';
  var cols = el('div', 'os-cols');
  cols.appendChild(planColumn('Up to college', plan.to_college,
    'nobody has finished his age-17 season'));
  cols.appendChild(planColumn('Into the draft', plan.to_draft,
    'nobody has declared or run out of eligibility'));
  var staying = el('div', 'os-col');
  var head = el('h3', null, 'Staying ');
  head.appendChild(el('span', 'chip', plan.staying === null ? '?' : plan.staying));
  staying.appendChild(head);
  staying.appendChild(el('div', 'empty-note',
    (plan.characters === null ? '?' : plan.characters) + ' characters in the store. Everyone ' +
    'active is paid the lump sum; a college year that is seen through also pays the ' +
    'development bonus.'));
  cols.appendChild(staying);
  host.appendChild(cols);
}

function planColumn(title, rows, emptyNote) {
  var col = el('div', 'os-col');
  var head = el('h3', null, title + ' ');
  head.appendChild(el('span', 'chip', (rows || []).length));
  col.appendChild(head);
  if (!rows || !rows.length) { col.appendChild(el('div', 'empty-note', emptyNote)); return col; }
  rows.forEach(function (who) {
    var row = el('div', 'os-row');
    row.appendChild(el('b', null, who.name));
    row.appendChild(document.createTextNode(' '));
    row.appendChild(el('span', 'muted', [who.position, who.team].filter(Boolean).join(' ')));
    if (who.conversion) {
      row.appendChild(document.createTextNode(' '));
      row.appendChild(el('span', 'chip' + (who.conversion.early_years ? ' warn' : ''),
        conversionText(who.conversion)));
    }
    col.appendChild(row);
  });
  return col;
}

function conversionText(conv) {
  if (!conv) { return ''; }
  return 'carries ' + conv.percent + '%' +
    (conv.early_years ? ', ' + conv.early_years + 'y early' : '');
}

function previewOffseason() {
  if (state.busy || state.osBusy) { toast('Something is already using the saves.', true); return; }
  state.osBusy = true;
  setButtons();
  var host = $('os-result');
  host.innerHTML = '';
  host.appendChild(el('div', 'empty-note',
    'Dry run in progress: reading the three saves. Nothing is being written.'));
  postJSON('/api/offseason/preview', {}).then(function (data) {
    state.osBusy = false;
    setButtons();
    if (!data.ok) {
      if (data.refused) { renderRefusal(data.error, null); return; }
      renderOsProblem(data.error || 'The dry run failed.', data.log);
      toast(data.error || 'The dry run failed.', true);
      return;
    }
    if (data.plan) { renderPlan(data.plan); }
    renderOffseasonResult(data.result, data.log, true);
  }).catch(function (err) {
    state.osBusy = false;
    setButtons();
    renderOsProblem('Could not reach the panel: ' + err, null);
  });
}

function startOffseason(force) {
  if (state.busy || state.osBusy) { toast('Something is already using the saves.', true); return; }
  var season = force ? state.refusedSeason : null;
  var plan = state.plan || {};
  var label = season || plan.season || "the store's current season";
  var question = force
    ? ('FORCE the ' + label + ' offseason?\n\n' +
       'Only do this if that run started and did not finish. Everything the offseason does is ' +
       'cumulative: if it did finish, this grows everybody a second time, pays the lump sum a ' +
       'second time and banks another college year for everyone still in college.\n\n' +
       'The saves are copied into backups/ first. There is no other undo.')
    : ('Run the offseason for season ' + label + '?\n\n' +
       'This writes to all three saves: everyone grows, prep players who have finished their ' +
       'age-17 season move to college, declared players are drafted onto pro rosters, every ' +
       'reserve slot left behind is refilled, and every active character is paid.\n\n' +
       'The finished season is archived first. All three game saves then roll into the next season and are verified before completion.');
  if (!window.confirm(question)) { return; }

  setBusy(true, 'starting the offseason...');
  clearLog(null);
  state.lastSeq = 0;
  $('os-result').innerHTML = '';
  postJSON('/api/offseason/start', { confirm: true, force: !!force, season: season })
    .then(function (data) {
      if (!data.ok) {
        setBusy(false, 'refused');
        banner(data.error || 'The offseason was refused.');
        toast(data.error || 'The offseason was refused.', true);
        if (data.busy && data.run) { state.busy = true; setButtons(); attach(data.run.id); }
        return;
      }
      banner('');
      state.run = data.run;
      $('os-force').hidden = true;
      setBusy(true, describeRun(data.run));
      attach(data.run.id);
    }).catch(function (err) {
      setBusy(false, 'failed to start');
      toast('Could not reach the panel: ' + err, true);
    });
}

function loadOffseasonResult() {
  api('/api/offseason/result').then(function (data) {
    if (!data.run) { return; }
    state.refusedSeason = data.run.season === undefined ? null : data.run.season;
    if (data.refused || data.run.status === 'refused') { renderRefusal(data.error, data.run); return; }
    if (data.result) { renderOffseasonResult(data.result, null, false); return; }
    if (data.run.status === 'error') { renderOsProblem(data.error, null); }
  }).catch(function () { /* the panel keeps whatever it was showing */ });
}

function renderRefusal(message, run) {
  var host = $('os-result');
  host.innerHTML = '';
  var box = el('div', 'os-refused');
  box.appendChild(el('strong', null, 'Season transition paused'));
  box.appendChild(el('div', null, message || ''));
  box.appendChild(el('div', null,
    'Read the reason above before trying again. An interrupted transition must be reconciled; it cannot be forced.'));
  host.appendChild(box);
  if (run) { state.refusedSeason = run.season === undefined ? null : run.season; }
  $('os-force').hidden = true;
  setButtons();
}

function renderOsProblem(message, lines) {
  var host = $('os-result');
  host.innerHTML = '';
  var box = el('div', 'os-trouble');
  box.appendChild(el('strong', null, 'The offseason did not run'));
  box.appendChild(el('p', null, message || ''));
  host.appendChild(box);
  if (lines && lines.length) { host.appendChild(logBlock(lines)); }
}

function logBlock(lines) {
  var section = el('div', 'os-section');
  section.appendChild(el('h3', null, 'What it logged'));
  section.appendChild(el('div', 'os-lines', lines.join('\n')));
  return section;
}

function renderOffseasonResult(result, lines, wasDry) {
  var host = $('os-result');
  host.innerHTML = '';
  if (!result) { host.appendChild(el('div', 'empty-note', 'No result came back.')); return; }
  var dry = result.dry_run === undefined ? !!wasDry : !!result.dry_run;

  var head = el('div', 'os-headline');
  head.appendChild(el('span', 'chip' + (dry ? ' warn' : ' ok'), dry ? 'DRY RUN' : 'RAN FOR REAL'));
  head.appendChild(el('span', null, 'season ' + (result.season === null ? '?' : result.season)));
  head.appendChild(el('span', 'muted',
    (dry ? 'would grow ' : 'grew ') + result.grown + ' - ' +
    (dry ? 'would promote ' : 'promoted ') + (result.promoted || []).length + ' - ' +
    (dry ? 'would draft ' : 'drafted ') + (result.drafted || []).length + ' - ' +
    (dry ? 'would retire ' : 'retired ') + (result.retired || []).length));
  if (dry) {
    head.appendChild(el('span', 'chip', 'nothing was written'));
  }
  host.appendChild(head);
  if (result.next_season) { host.appendChild(el('p', 'season-readiness ready', 'Season ' + result.next_season + ' is ready. All three game saves were rolled forward and verified.')); }
  if (result.publish_error) { host.appendChild(el('p', 'os-trouble', 'The new season is saved, but publishing needs a retry: ' + result.publish_error)); }

  // Failures first and loudest: everything below this is only true if this box is empty.
  host.appendChild(troubleBlock(result, dry));

  host.appendChild(promotionsBlock(result, dry));
  host.appendChild(draftBlock(result, dry));
  host.appendChild(retiredBlock(result, dry));
  host.appendChild(growthBlock(result, lines, dry));
  host.appendChild(pointsBlock(result, dry));
  if (result.other) {
    // run_offseason grew a stage this panel does not draw yet. Say so rather than quietly
    // showing a result that is missing part of what happened.
    var extra = [];
    Object.keys(result.other).forEach(function (key) {
      extra.push(key + ': ' + result.other[key]);
    });
    var note = el('div', 'os-section');
    note.appendChild(el('div', 'empty-note',
      'run_offseason also reported ' + extra.join(', ') +
      ', which this panel has no table for yet.'));
    host.appendChild(note);
  }
  if (lines && lines.length) { host.appendChild(logBlock(lines)); }
}

function retiredBlock(result, dry) {
  var rows = result.retired || [];
  var section = el('div', 'os-section');
  section.appendChild(el('h3', null, dry ? 'Careers that would end' : 'Careers that ended'));
  if (!rows.length) {
    section.appendChild(el('div', 'empty-note', 'nobody retired'));
    return section;
  }
  var t = table(['Character', 'Level', 'Why', 'Reserve slot']);
  var body = t.tBodies[0];
  rows.forEach(function (row) {
    var who = row.character || {};
    var tr = document.createElement('tr');
    tr.appendChild(el('td', null, who.name || '?'));
    tr.appendChild(el('td', null, row.league || who.league || ''));
    tr.appendChild(el('td', null, row.reason || ''));
    if (dry) {
      tr.appendChild(el('td', null, ''));
    } else if (row.slot_refilled) {
      tr.appendChild(el('td', null, 'handed back'));
    } else {
      // A slot that never comes back lowers the ceiling on concurrent characters by one,
      // permanently, and nothing else on the page would ever mention it.
      tr.appendChild(el('td', 'bad-cell', 'NOT handed back - run tools/protect_rosters.py'));
    }
    body.appendChild(tr);
  });
  section.appendChild(t);
  return section;
}

function troubleBlock(result, dry) {
  var failed = result.failed || [];
  var badPicks = (result.drafted || []).filter(function (p) { return p.error; });
  if (!failed.length && !badPicks.length) {
    var fine = el('div', 'os-section');
    fine.appendChild(el('div', 'os-safe',
      'No failures: every character was found in the save' + (dry ? ' (dry run).' : '.')));
    return fine;
  }
  var box = el('div', 'os-trouble');
  box.appendChild(el('strong', null,
    (failed.length + badPicks.length) + ' character(s) the offseason could not move'));
  box.appendChild(el('p', null, dry
    ? ('The dry run could not find these people in the save, so a real run would leave them ' +
       'behind too. Fix this before running it for real.')
    : ('The rest of the offseason ran - one character the codec cannot find must not abort a ' +
       'run that has already moved other people. But the save and the store now disagree about ' +
       'these people: the store still says where each one was, and the save does not have him ' +
       'where he should be. Sort this out before the next sim.')));
  var t = table(['Character', 'Stage', 'What went wrong']);
  var body = t.tBodies[0];
  failed.forEach(function (row) {
    var tr = document.createElement('tr');
    tr.appendChild(el('td', null, (row.character || {}).name || '?'));
    tr.appendChild(el('td', null, (row.stage || '') + (row.league ? ' (' + row.league + ')' : '')));
    tr.appendChild(el('td', 'err', row.error || ''));
    body.appendChild(tr);
  });
  badPicks.forEach(function (p) {
    var tr = document.createElement('tr');
    tr.appendChild(el('td', null, (p.character || {}).name || '?'));
    tr.appendChild(el('td', null, 'draft pick #' + p.pick + ' (' + p.team + ')'));
    tr.appendChild(el('td', 'err', p.error || ''));
    body.appendChild(tr);
  });
  box.appendChild(t);
  return box;
}

function promotionsBlock(result, dry) {
  var rows = result.promoted || [];
  var section = el('div', 'os-section');
  section.appendChild(el('h3', null, dry ? 'Would be promoted' : 'Promoted'));
  if (!rows.length) {
    section.appendChild(el('div', 'empty-note', 'nobody moved up a level'));
    return section;
  }
  var t = table(['Character', 'Pos', 'Move', 'Team', 'Carries', 'Slot taken']);
  var body = t.tBodies[0];
  rows.forEach(function (row) {
    var who = row.character || {};
    var tr = document.createElement('tr');
    tr.appendChild(el('td', null, who.name || '?'));
    tr.appendChild(el('td', null, who.position || ''));
    tr.appendChild(el('td', null, (who.league || '?') + ' → ' + (row.to || '?')));
    tr.appendChild(el('td', null, row.team || ''));
    tr.appendChild(conversionCell(row.conversion));
    tr.appendChild(el('td', null, slotText(row.slot)));
    body.appendChild(tr);
  });
  section.appendChild(t);
  return section;
}

function draftBlock(result, dry) {
  var rows = result.drafted || [];
  var section = el('div', 'os-section');
  section.appendChild(el('h3', null, dry ? 'Draft board (would be)' : 'Draft board'));
  if (!rows.length) {
    section.appendChild(el('div', 'empty-note', 'nobody entered the draft'));
    return section;
  }
  section.appendChild(el('div', 'empty-note',
    'Pick order is reverse standings, read from the published pro standings; the app runs this ' +
    'draft, not FBPB3.'));
  var t = table(['#', 'Rd', 'Team', 'Character', 'Pos', 'Carries', 'Years early', 'Slot']);
  var body = t.tBodies[0];
  rows.forEach(function (p) {
    var who = p.character || {};
    var conv = p.conversion || {};
    var tr = document.createElement('tr');
    tr.appendChild(el('td', 'num', p.pick === null ? '' : p.pick));
    tr.appendChild(el('td', 'num', p.round === null ? '' : p.round));
    tr.appendChild(el('td', null, p.team || ''));
    tr.appendChild(el('td', null, who.name || '?'));
    tr.appendChild(el('td', null, who.position || ''));
    tr.appendChild(conversionCell(p.conversion));
    tr.appendChild(el('td', 'num', conv.early_years === undefined ? '' : conv.early_years));
    if (p.error) {
      tr.appendChild(el('td', 'bad-cell', 'FAILED: ' + p.error));
    } else {
      tr.appendChild(el('td', null, slotText(p.slot)));
    }
    body.appendChild(tr);
  });
  section.appendChild(t);
  return section;
}

function conversionCell(conv) {
  if (!conv) { return el('td', null, ''); }
  var cell = el('td', 'num', conv.percent + '%');
  if (conv.projected) {
    cell.title = 'worked out from conversion_for(): a dry run never promotes anybody, so this ' +
      'is the multiplier the real run would use';
    cell.textContent = conv.percent + '%*';
  }
  return cell;
}

function slotText(slot) {
  if (!slot) { return ''; }
  return [slot.name, slot.team].filter(Boolean).join(' @ ');
}

function growthBlock(result, lines, dry) {
  var section = el('div', 'os-section');
  section.appendChild(el('h3', null, dry ? 'Growth (would be)' : 'Growth'));
  section.appendChild(el('div', 'empty-note',
    result.grown + ' character(s) ' + (dry ? 'would grow' : 'grew') + ' this offseason. ' +
    'run_offseason reports growth as a count, so the names below are read back out of its log.'));
  var grew = (lines || []).filter(function (line) { return line.indexOf(' grew ') !== -1; });
  if (grew.length) { section.appendChild(el('div', 'os-lines', grew.join('\n'))); }
  else if (!lines) {
    section.appendChild(el('div', 'empty-note', 'The names are in the live log above.'));
  }
  return section;
}

function pointsBlock(result, dry) {
  var section = el('div', 'os-section');
  section.appendChild(el('h3', null, 'Points'));
  if (dry || result.paid === null || result.paid === undefined) {
    section.appendChild(el('div', 'empty-note',
      'A dry run pays nobody, so run_offseason reports no totals for it.'));
    return section;
  }
  section.appendChild(el('div', null,
    'Paid the offseason lump sum to ' + result.paid + ' character(s); ' +
    (result.developed || 0) + ' also took the college development bonus for a season seen ' +
    'through.'));
  return section;
}

/* ------------------------------------------------------------------ the approvals ---- */
function refreshPending() {
  api('/api/pending').then(function (data) {
    if (!data.ok) {
      $('pending-characters').innerHTML = '';
      $('pending-characters').appendChild(el('div', 'empty-note', data.error || 'unavailable'));
      $('pending-requests').innerHTML = '';
      $('pending-requests').appendChild(el('div', 'empty-note', data.error || 'unavailable'));
      return;
    }
    state.autoApprove = data.auto_approve;
    $('auto-approve').checked = !!data.auto_approve;
    $('auto-approve').disabled = false;
    renderCharacters(data.characters || []);
    renderRequests(data.requests || []);
  }).catch(function (err) { toast('pending_work failed: ' + err, true); });
}

function pick(value) {
  var box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'pick-request';
  box.value = value;
  return box;
}

function firstOf(row, keys, fallback) {
  for (var i = 0; i < keys.length; i++) {
    var value = row[keys[i]];
    if (value !== undefined && value !== null && value !== '') { return value; }
  }
  return fallback === undefined ? '' : fallback;
}

function table(headers) {
  var t = document.createElement('table');
  var thead = document.createElement('thead');
  var tr = document.createElement('tr');
  for (var i = 0; i < headers.length; i++) { tr.appendChild(el('th', null, headers[i])); }
  thead.appendChild(tr);
  t.appendChild(thead);
  t.appendChild(document.createElement('tbody'));
  return t;
}

function renderCharacters(rows) {
  var host = $('pending-characters');
  host.innerHTML = '';
  if (!rows.length) { host.appendChild(el('div', 'empty-note', 'nobody waiting')); return; }
  var t = table(['Character', 'Owner', 'League', 'Pos', 'Waiting since']);
  var body = t.tBodies[0];
  rows.forEach(function (row) {
    var tr = document.createElement('tr');
    var name = firstOf(row, ['name'], '') ||
      ((row.first_name || '') + ' ' + (row.last_name || '')).trim() || row.id || '?';
    tr.appendChild(el('td', null, name));
    tr.appendChild(el('td', null, firstOf(row, ['owner_name', 'owner', 'discord_username'], '?')));
    tr.appendChild(el('td', null, firstOf(row, ['league'], '?')));
    tr.appendChild(el('td', null, firstOf(row, ['position'], '')));
    tr.appendChild(el('td', null, String(firstOf(row, ['created_at', 'requested_at'], '')).slice(0, 16)));
    body.appendChild(tr);
  });
  host.appendChild(t);
  host.appendChild(el('div', 'empty-note',
    'A character takes a free reserve slot the next time its league sims.'));
}

function renderRequests(rows) {
  var host = $('pending-requests');
  host.innerHTML = '';
  if (!rows.length) { host.appendChild(el('div', 'empty-note', 'no pending requests')); return; }
  var t = table(['', 'Character', 'Rating', 'Delta', 'Cost', 'Asked']);
  var body = t.tBodies[0];
  rows.forEach(function (row) {
    var tr = document.createElement('tr');
    var cell = el('td', 'pick');
    cell.appendChild(pick(firstOf(row, ['id', 'request_id'], '')));
    tr.appendChild(cell);
    var who = row.character || {};
    tr.appendChild(el('td', null,
      firstOf(row, ['character_name'], '') ||
      ((who.first_name || '') + ' ' + (who.last_name || '')).trim() ||
      firstOf(row, ['character'], '?')));
    tr.appendChild(el('td', null, firstOf(row, ['rating', 'field'], '?')));
    tr.appendChild(el('td', 'num', firstOf(row, ['delta'], '')));
    tr.appendChild(el('td', 'num', firstOf(row, ['cost'], '')));
    tr.appendChild(el('td', null, String(firstOf(row, ['requested_at', 'created_at'], '')).slice(0, 16)));
    body.appendChild(tr);
  });
  host.appendChild(t);
}

function selectedRequests() {
  var boxes = document.querySelectorAll('.pick-request:checked');
  var ids = [];
  for (var i = 0; i < boxes.length; i++) { ids.push(boxes[i].value); }
  return ids;
}

/* --------------------------------------------------------------------- the history ---- */
function refreshHistory() {
  api('/api/history?limit=20').then(function (data) {
    var host = $('history');
    host.innerHTML = '';
    var rows = data.runs || [];
    if (data.error) { host.appendChild(el('div', 'empty-note', data.error)); }
    if (!rows.length && (data.this_session || []).length) {
      rows = data.this_session;
      host.appendChild(el('div', 'empty-note', 'runs from this session:'));
    }
    if (!rows.length) {
      if (!data.error) { host.appendChild(el('div', 'empty-note', 'no runs recorded yet')); }
      return;
    }
    var t = table(['When', 'Leagues', 'Days', 'Took', 'Result']);
    var body = t.tBodies[0];
    rows.forEach(function (row) {
      var tr = document.createElement('tr');
      tr.appendChild(el('td', null,
        String(firstOf(row, ['at', 'started_at', 'finished_at', 'when'], '')).replace('T', ' ')));
      if (row.kind === 'offseason') {
        // An offseason has no leagues and no day count: it is one season, all three saves.
        tr.appendChild(el('td', null, 'offseason ' +
          (row.season === null || row.season === undefined ? '' : row.season) +
          (row.force ? ' (forced)' : '')));
        tr.appendChild(el('td', 'num', ''));
      } else {
        var leagues = row.leagues;
        tr.appendChild(el('td', null,
          Array.isArray(leagues) ? leagues.join(', ') : (leagues || 'all')));
        tr.appendChild(el('td', 'num', row.days_by_league ? Object.keys(row.days_by_league).map(function(key) { return key + ': ' + row.days_by_league[key]; }).join(' · ') : firstOf(row, ['days'], '')));
      }
      tr.appendChild(el('td', 'num', firstOf(row, ['elapsed', 'seconds', 'took'], '')));
      var okay = row.ok === undefined ? (row.status === 'ok') : !!row.ok;
      var wasRefused = row.status === 'refused';
      var cell = el('td');
      var chip = el('span', 'chip ' + (okay ? 'ok' : (wasRefused ? 'warn' : 'bad')),
        okay ? 'ok' : (wasRefused ? 'refused' : (row.error || row.status || 'error')));
      cell.appendChild(chip);
      tr.appendChild(cell);
      body.appendChild(tr);
    });
    host.appendChild(t);
  }).catch(function (err) { toast('run_history failed: ' + err, true); });
}

/* ----------------------------------------------------------------------- creation ---- */
function createCharacter() {
  var payload = {
    first_name: $('cc-first').value.trim(),
    last_name: $('cc-last').value.trim(),
    owner: $('cc-owner').value.trim(),
    league: $('cc-league').value,
    position: $('cc-position').value,
    height_inches: parseInt($('cc-height').value, 10),
    dob: $('cc-dob').value.trim()
  };
  // Blank is allowed and is the usual case: commissioner.growth.weight_for_save() then gives
  // him the weight his height suggests. The website's slider is where a weight normally
  // comes from - this box exists so a character made here is not stuck with a filler's.
  var weight = parseInt($('cc-weight').value, 10);
  if (weight) { payload.weight_lbs = weight; }
  if (!payload.first_name || !payload.last_name) {
    toast('A character needs both names.', true);
    return;
  }
  postJSON('/api/character', { character: payload }).then(function (data) {
    if (!data.ok) { toast(data.error || 'create_character failed', true); return; }
    $('create-note').textContent = 'created';
    toast('Character created.');
    refreshPending();
  });
}

/* --------------------------------------------------------------------------- wire ---- */
function wire() {
  $('btn-week').addEventListener('click', function () {
    startSim(parseInt($('days-week').value, 10) || 7);
  });
  $('btn-chunk').addEventListener('click', function () {
    startSim(parseInt($('days-chunk').value, 10) || 21);
  });
  $('btn-clear').addEventListener('click', function () {
    if (state.busy) { toast('Not while a sim is running - the log is the only record.', true); return; }
    clearLog('Cleared.');
  });

  // The season boundary moves with BOTH the leagues ticked and the number typed, so both have
  // to recompute it. Suggest on a league change (the set of boundaries changed), only refresh
  // on typing (never fight somebody mid-keystroke over the number they are entering).
  var picks = document.querySelectorAll('.league-pick');
  for (var i = 0; i < picks.length; i++) {
    picks[i].addEventListener('change', function () { suggestDays(); refreshPace(); });
  }
  $('days-chunk').addEventListener('input', function () {
    daysTouched = true;          // from here the number is theirs, not ours
    refreshSeasonEnd();
    refreshPace();
  });
  $('days-week').addEventListener('input', refreshPace);
  suggestDays();
  refreshPace();
  $('btn-refresh-pending').addEventListener('click', refreshPending);
  $('btn-refresh-history').addEventListener('click', refreshHistory);

  $('btn-approve').addEventListener('click', function () {
    var ids = selectedRequests();
    if (!ids.length) { toast('Nothing selected.', true); return; }
    postJSON('/api/approve', { ids: ids }).then(function (data) {
      if (!data.ok) { toast(data.error || 'approve failed', true); return; }
      toast('Approved ' + (data.approved === undefined ? ids.length : data.approved) + '.');
      refreshPending();
    });
  });

  $('btn-reject').addEventListener('click', function () {
    var ids = selectedRequests();
    if (ids.length !== 1) { toast('Reject takes exactly one request at a time.', true); return; }
    postJSON('/api/reject', { id: ids[0], note: $('reject-note').value }).then(function (data) {
      if (!data.ok) { toast(data.error || 'reject failed', true); return; }
      $('reject-note').value = '';
      toast('Rejected.');
      refreshPending();
    });
  });

  $('auto-approve').addEventListener('change', function () {
    var wanted = $('auto-approve').checked;
    postJSON('/api/auto-approve', { enabled: wanted }).then(function (data) {
      if (!data.ok) {
        $('auto-approve').checked = !!state.autoApprove;   // put it back; nothing changed
        $('auto-note').textContent = data.error || 'could not change the setting';
        toast('Auto-approve unchanged.', true);
        return;
      }
      state.autoApprove = data.auto_approve;
      $('auto-note').textContent = '';
    });
  });

  $('btn-create').addEventListener('click', createCharacter);

  $('btn-os-preview').addEventListener('click', previewOffseason);
  $('btn-os-run').addEventListener('click', function () { startOffseason(false); });
  // Two deliberate actions before a force, plus the confirm inside startOffseason: ticking the
  // box is the first, pressing the button is the second.
  $('os-force-ack').addEventListener('change', setButtons);
  $('btn-os-force').addEventListener('click', function () { startOffseason(true); });
}

function boot() {
  wire();
  if (BOOT.busy && BOOT.run) {
    // A sim was already running when this page loaded: reattach to its log from the start
    // rather than pretending the panel is idle.
    setBusy(true, describeRun(BOOT.run));
    clearLog(null);
    attach(BOOT.run.id);
  } else if (BOOT.run) {
    var finished = BOOT.run.status === 'ok';
    var refused = BOOT.run.status === 'refused';
    $('run-chip').textContent = finished ? 'finished' : BOOT.run.status;
    $('run-chip').className = 'chip ' + (finished ? 'ok' : (refused ? 'warn' : 'bad'));
    $('run-line').textContent = describeRun(BOOT.run);
    clearLog(null);
    attach(BOOT.run.id);   // replays the finished run's log, then closes
  } else {
    setBusy(false, 'Nothing has run yet.');
  }
  setButtons();
  refreshPending();
  refreshHistory();
  // A refusal (or a finished offseason) has to survive a refresh: the panel asks the server
  // what the last offseason did rather than relying on the page it was served with.
  loadOffseasonResult();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
