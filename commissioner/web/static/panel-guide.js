/* Read-only views over verified calendar, readiness and durable run history. */
'use strict';
(function () {
  var previewSeason = null;
  var lastVerified = null;
  window.recordVerifiedOffseason = function (result) {
    if (result && !result.dry_run && result.next_season && (result.rollover || []).length === 3) {
      lastVerified = result; window.renderOffseasonGuide(state.plan);
    }
  };
  function node(tag, text, cls) {
    var n = document.createElement(tag); n.textContent = text || ''; if (cls) n.className = cls; return n;
  }
  function jump(id) {
    var target = document.getElementById(id);
    if (target) { target.scrollIntoView({behavior: 'smooth', block: 'center'}); target.focus(); }
  }
  function action(text, id) {
    var button = node('button', text, 'plain'); button.type = 'button';
    button.onclick = function () { jump(id); }; return button;
  }
  window.clearOffseasonPreview = function () { previewSeason = null; };
  window.recordOffseasonPreview = function (data) {
    var result = data.result || {};
    previewSeason = data.ok && !result.refused && !(result.errors || []).length
      && !result.error && !(result.failed || []).length
      && !(result.drafted || []).some(function (p) { return !!p.error; }) ? (data.plan || state.plan || {}).season : null;
    window.renderOffseasonGuide(state.plan);
  };
  window.renderOffseasonGuide = function (plan) {
    var host = document.getElementById('offseason-guide'); if (!host) return;
    host.replaceChildren();
    var transition = (plan || {}).transition || {};
    var ready = !!transition.ready;
    var checked = ready && previewSeason === plan.season;
    var steps = [
      ['Finish the season', ready, ready ? 'All three champions are decided.' :
        ((transition.reasons || []).join(' ') || (plan || {}).error || 'Season readiness is unavailable.'),
        'Open calendar and playoff controls', 'calendar-end'],
      ['Preview offseason changes', checked, checked ? 'Preview completed for this season. Review the report below for moves and warnings.' :
        'Run a dry run to review growth, promotions, draft entries and any missing players before making changes.',
        'Review dry-run controls', 'btn-os-preview'],
      ['Run the season transition', false,
        'The transition archives the finished season, develops players, handles promotions and the draft, then verifies all three new saves.',
        'Review transition controls', 'btn-os-run']
    ];
    var list = node('ol', '', 'guided-steps');
    steps.forEach(function (s, index) {
      var active = index === 0 ? !ready : index === 1 ? ready && !checked : checked;
      var item = node('li', '', s[1] ? 'complete' : active ? 'current' : 'waiting');
      item.append(node('strong', (s[1] ? 'Done: ' : active ? 'Next: ' : 'Then: ') + s[0]), node('p', s[2]));
      if (active) item.append(action(s[3], s[4]));
      list.append(item);
    });
    host.append(list);
    if (lastVerified) host.append(node('p', 'Verified transition to season ' + lastVerified.next_season + ': all three saves rolled forward and exported.' + (lastVerified.publish_error ? ' Publishing needs attention: ' + lastVerified.publish_error : ''), 'muted'));
    if (plan && plan.last_offseason != null) host.append(node('p',
      'Last recorded offseason: ' + plan.last_offseason + '. Current season: ' + plan.season + '.', 'muted'));
  };
  window.renderLeagueDetails = function (data, plan, busy) {
    var readiness = ((plan || {}).transition || {}).leagues || [];
    (data.leagues || []).forEach(function (lg) {
      var host = document.querySelector('.league[data-key="' + lg.key + '"] .league-detail');
      if (!host) return;
      host.replaceChildren();
      var remaining = Math.max(0, lg.total - lg.played);
      var phase = readiness.find(function (r) { return r.key === lg.key; }) || {};
      var playoffGames = lg.games.filter(function (g) { return g.phase === 'Playoffs'; });
      var done = playoffGames.filter(function (g) { return g.played; }).length;
      var next = playoffGames.filter(function (g) { return !g.played; }).map(function (g) { return g.date; }).sort()[0];
      var label = phase.champion ? 'Season complete' : remaining ? 'Regular season' : 'Playoffs';
      host.append(node('strong', label), node('p', lg.played + ' / ' + lg.total + ' regular-season games played; ' + remaining + ' remaining.'));
      if (phase.champion) host.append(node('p', 'Champion: ' + phase.champion));
      else if (!remaining) host.append(node('p', done + ' playoff games played.' + (next ? ' Next scheduled games: ' + next + '.' : ' Next playoff schedule not available.')));
      host.append(node('p', busy ? 'Simulation running; showing the last verified calendar.' :
        phase.champion ? 'Next: wait for all leagues to finish, then preview the offseason.' :
        remaining ? 'Next: choose a calendar target or finish the regular season.' : 'Next: play a playoff round or finish the playoffs.', 'muted'));
    });
  };
  window.renderSimRecap = function (rows) {
    var host = document.getElementById('sim-recap'); if (!host) return;
    host.replaceChildren();
    var run = rows.find(function (r) { return r.kind !== 'offseason' && !r.dry_run; });
    if (!run) { host.append(node('p', 'No completed simulation has been recorded.')); return; }
    host.append(node('strong', (run.ok ? 'Saved successfully' : 'Stopped with an error') +
      ' · season ' + (run.season || '?') + ' · ' + (run.seconds == null ? '?' : Math.round(run.seconds)) + ' seconds'));
    host.append(node('p', run.at ? new Date(run.at).toLocaleString() : 'Completion time unavailable', 'muted'));
    (run.errors || []).forEach(function (error) { host.append(node('p', error, 'bad')); });
    var summary = run.summary;
    if (!summary) {
      host.append(node('p', 'This older run did not record a detailed recap. Future runs include dates, points and rating changes.', 'muted'));
    } else {
      Object.entries(summary.dates || {}).forEach(function (pair) {
        var d = pair[1]; host.append(node('p', pair[0] + ': ' + (d.from || 'unknown start') + ' → ' +
          (d.to || 'end not verified') + (d.games_played == null ? '' : ' · ' + d.games_played + ' games played')));
      });
      (summary.points || []).forEach(function (p) { host.append(node('p', p.league + ': ' + p.per_player + ' points each to ' + p.players + ' players.')); });
      host.append(node('h3', 'Purchased and applied changes'));
      (summary.changes || []).forEach(function (line) { host.append(node('p', line)); });
      if (!(summary.changes || []).length) host.append(node('p', 'No rating changes recorded in the apply log.', 'muted'));
      host.append(node('h3', 'Growth during simulation'));
      (summary.growth || []).forEach(function (p) {
        host.append(node('p', p.name + ': ' + Object.entries(p.ratings).map(function (r) { return r[0] + ' ' + r[1][0] + ' → ' + r[1][1]; }).join(', ')));
      });
      if (!(summary.growth || []).length) host.append(node('p', 'No growth recorded above the protected pre-sim ratings.', 'muted'));
      (summary.publishing || []).forEach(function (line) { host.append(node('p', line, 'muted')); });
    }
    host.append(node('p', (run.applied || 0) + ' upgrades applied · ' + (run.activated || 0) + ' players activated · ' + (run.snapshots || 0) + ' snapshots recorded.'));
  };
  document.addEventListener('DOMContentLoaded', function () {
    window.renderOffseasonGuide(state.plan);
    document.getElementById('summary-feats').onclick = function () {
      document.getElementById('feats-scope').value = 'last';
      jump('statistical-feats');
      var scan = document.getElementById('feats-scan'); if (!scan.disabled) scan.click();
    };
  });
})();
