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
  autoApprove: null
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
  $('run-chip').textContent = okay ? 'finished' : 'ERROR';
  $('run-chip').className = 'chip ' + (okay ? 'ok' : 'bad');
  $('run-line').textContent = ev.message || '';
  if (!okay) { banner('The last run stopped with an error: ' + (ev.error || ev.message || '')); }
  refreshState();
  refreshHistory();
  refreshPending();
}

/* ------------------------------------------------------------------- starting up ---- */
function pickedLeagues() {
  var boxes = document.querySelectorAll('.league-pick');
  var keys = [];
  for (var i = 0; i < boxes.length; i++) {
    if (boxes[i].checked) { keys.push(boxes[i].value); }
  }
  return keys.length === boxes.length ? null : keys;   // null = all of them
}

function startSim(days) {
  if (state.busy) { toast('A sim is already running.', true); return; }
  var leagues = pickedLeagues();
  if (leagues !== null && leagues.length === 0) { toast('Pick at least one league.', true); return; }
  var dry = $('dry-run').checked;

  setBusy(true, 'starting...');
  clearLog(null);
  state.lastSeq = 0;
  postJSON('/api/sim/start', { days: days, leagues: leagues, dry_run: dry }).then(function (data) {
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
  return (run.dry_run ? 'DRY RUN - ' : '') +
    (run.leagues ? run.leagues.join(', ') : 'all leagues') +
    ', ' + run.days + ' day' + (run.days === 1 ? '' : 's') +
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
  $('btn-week').disabled = state.busy;
  $('btn-chunk').disabled = state.busy;
}

/* --------------------------------------------------------------------- refreshers ---- */
function refreshState() {
  api('/api/state').then(function (data) {
    if (!data.ok) { return; }
    renderLeagues(data.universe);
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
      var leagues = row.leagues;
      tr.appendChild(el('td', null,
        Array.isArray(leagues) ? leagues.join(', ') : (leagues || 'all')));
      tr.appendChild(el('td', 'num', firstOf(row, ['days'], '')));
      tr.appendChild(el('td', 'num', firstOf(row, ['elapsed', 'seconds', 'took'], '')));
      var okay = row.ok === undefined ? (row.status === 'ok') : !!row.ok;
      var cell = el('td');
      var chip = el('span', 'chip ' + (okay ? 'ok' : 'bad'),
        okay ? 'ok' : (row.error || row.status || 'error'));
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
    startSim(parseInt($('days-chunk').value, 10) || 35);
  });
  $('btn-clear').addEventListener('click', function () {
    if (state.busy) { toast('Not while a sim is running - the log is the only record.', true); return; }
    clearLog('Cleared.');
  });
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
}

function boot() {
  wire();
  if (!BOOT.simweek || !BOOT.simweek.ok) {
    $('btn-refresh-pending').disabled = false;   // it will report the same failure, clearly
  }
  if (BOOT.busy && BOOT.run) {
    // A sim was already running when this page loaded: reattach to its log from the start
    // rather than pretending the panel is idle.
    setBusy(true, describeRun(BOOT.run));
    clearLog(null);
    attach(BOOT.run.id);
  } else if (BOOT.run) {
    $('run-chip').textContent = BOOT.run.status === 'ok' ? 'finished' : BOOT.run.status;
    $('run-chip').className = 'chip ' + (BOOT.run.status === 'ok' ? 'ok' : 'bad');
    $('run-line').textContent = describeRun(BOOT.run);
    clearLog(null);
    attach(BOOT.run.id);   // replays the finished run's log, then closes
  } else {
    setBusy(false, 'Nothing has run yet.');
  }
  refreshPending();
  refreshHistory();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
