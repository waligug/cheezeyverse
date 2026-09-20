'use strict';
(function () {
  var view = { data:null, league:'prep', team:'', month:null, selected:null, plan:null, request:0 };
  function dateText(value) { return new Date(value + 'T12:00:00Z').toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'}); }
  function league() { return view.data && view.data.leagues.find(function (l) { return l.key === view.league; }); }
  function busy() { return state.busy || state.osBusy; }
  window.renderCalendarDates = function () {
    if (!view.data) { return; }
    view.data.leagues.forEach(function(lg) {
      var card = document.querySelector('.league[data-key="' + lg.key + '"]');
      if (!card) { return; }
      var stage = card.querySelector('.stage');
      stage.textContent = dateText(lg.current_date) + ' · season ' + lg.season + ' · day ' + lg.day;
    });
  };
  window.refreshCalendarButtons = function () {
    if (!$('calendar-start')) { return; }
    $('calendar-start').disabled = busy() || !view.plan || !view.plan.leagues.some(function (r) { return r.days > 0; });
    $('calendar-end').disabled = busy() || !view.data;
    $('calendar-refresh').disabled = busy();
    var playoffs = state.plan && state.plan.transition && state.plan.transition.leagues.some(function(r) { return !r.champion && r.regular_remaining === 0; });
    $('btn-playoffs').disabled = busy() || !playoffs;
  };
  window.renderSeasonReadiness = function (plan) {
    var host = $('season-readiness'), transition = plan && plan.transition;
    if (!host) { return; }
    host.className = 'season-readiness' + (transition && transition.ready ? ' ready' : '');
    host.textContent = !transition ? 'Checking the three season states…' : transition.ready
      ? 'All three champions are decided. Ready to develop players and start the next season.'
      : (transition.reasons || []).join(' ');
  };
  function showError(message) {
    $('calendar-preview').textContent = message;
    view.plan = null;
    refreshCalendarButtons();
  }
  function loadCalendar() {
    if (busy()) { return; }
    var request = ++view.request;
    view.plan = null;
    refreshCalendarButtons();
    api('/api/calendar').then(function (data) {
      if (request !== view.request) { return; }
      if (!data.ok) { showError(data.error || 'Calendar unavailable.'); return; }
      view.data = data;
      renderCalendarDates();
      if (!view.month) { view.month = league().current_date.slice(0,7); }
      view.selected = null;
      populateTeams(); render();
      $('calendar-preview').textContent = 'Choose a day to see the exact plan for all three leagues.';
      $('calendar-target').textContent = 'Pick a date on the calendar';
      refreshCalendarButtons();
    }).catch(function (err) { showError('Could not load the calendar: ' + err); });
  }
  function populateTeams() {
    var select = $('calendar-team'); select.innerHTML = '';
    select.appendChild(new Option('All teams', ''));
    league().teams.forEach(function (team) { select.appendChild(new Option(team, team)); });
    if (league().teams.indexOf(view.team) < 0) { view.team = ''; }
    select.value = view.team;
  }
  function render() {
    var lg = league(); if (!lg) { return; }
    var parts = view.month.split('-').map(Number), first = new Date(Date.UTC(parts[0], parts[1]-1, 1));
    $('calendar-month').textContent = first.toLocaleDateString(undefined, {month:'long',year:'numeric',timeZone:'UTC'});
    var host = $('calendar-grid'); host.innerHTML = '';
    for (var i=0; i<first.getUTCDay(); i++) { host.appendChild(el('div','calendar-blank')); }
    var count = new Date(Date.UTC(parts[0],parts[1],0)).getUTCDate();
    for (var n=1; n<=count; n++) {
      var iso = view.month + '-' + String(n).padStart(2,'0');
      var games = lg.games.filter(function (g) { return g.date === iso && (!view.team || g.teams.indexOf(view.team)>=0); });
      var button = el('button','calendar-day' + (iso===lg.current_date ? ' today':'') + (iso===view.selected ? ' selected':''));
      button.type='button'; button.dataset.date=iso;
      button.disabled=busy() || iso<lg.current_date || iso>lg.last || iso<lg.first;
      button.setAttribute('aria-label',dateText(iso) + ': ' + games.length + ' games' + (iso===lg.current_date ? ', current date':''));
      button.setAttribute('aria-pressed',String(iso===view.selected));
      button.appendChild(el('b',null,n + (iso===lg.current_date ? ' ●':'')));
      var text = games.length ? games.length + (games.length===1 ? ' game':' games') : 'Rest day';
      if (view.team && games.length) { text = games.map(function(g) { return g.numbers[view.team] ? 'Game '+g.numbers[view.team] : g.phase; }).join(', '); }
      if (games.length && games.every(function(g) { return g.played; })) { text='✓ '+text; }
      button.appendChild(el('small',null,text));
      button.title=games.map(function(g) { return g.label; }).join('\n') || 'No games scheduled';
      button.addEventListener('click',function(event) { selectDate(event.currentTarget.dataset.date); });
      host.appendChild(button);
    }
    while (host.children.length%7) { host.appendChild(el('div','calendar-blank')); }
  }
  function selectDate(iso) {
    var request = ++view.request;
    view.selected=iso; view.plan=null; render(); refreshCalendarButtons();
    $('calendar-target').textContent='Play through '+dateText(iso);
    $('calendar-preview').textContent='Calculating the other leagues…';
    var games=$('calendar-games'); games.innerHTML='';
    league().games.filter(function(g) {return g.date===iso && (!view.team || g.teams.indexOf(view.team)>=0);}).forEach(function(g) { games.appendChild(el('p',null,g.label)); });
    postJSON('/api/calendar/plan',{reference:view.league,target:iso}).then(function(data) {
      if(request!==view.request) {return;}
      if(!data.ok) {showError(data.error);return;}
      view.plan=data.plan;
      var host=$('calendar-preview');host.innerHTML='';
      host.appendChild(el('p',null,'Target: '+data.plan.percent+'% of the regular season'));
      data.plan.leagues.forEach(function(row) {
        var item=el('div','calendar-plan-row');
        item.appendChild(el('strong',null,row.key));
        item.appendChild(el('span',null,row.days ? dateText(row.from)+' → '+dateText(row.through) : 'Already at or ahead of this target'));
        if(row.days) {item.appendChild(el('span',null,row.days+' days · '+row.games+' scheduled games'));}
        host.appendChild(item);
      });
      refreshCalendarButtons();
    }).catch(function(err) {if(request===view.request) {showError(String(err));}});
  }
  function startCalendar() {
    if(!view.plan || busy()) {return;}
    var plan=view.plan;
    setBusy(true,'Starting the calendar plan…');clearLog(null);state.lastSeq=0;
    postJSON('/api/calendar/start',{reference:plan.reference,target:plan.target,token:plan.token}).then(function(data) {
      if(!data.ok) {setBusy(false,'Plan not started');showError(data.error);return;}
      state.run=data.run;view.plan=null;setBusy(true,'Playing through the selected target');attach(data.run.id);
    }).catch(function(err) {setBusy(false,'Could not start');showError(String(err));});
  }
  function init() {
    $('calendar-refresh').addEventListener('click',loadCalendar);
    $('calendar-league').addEventListener('change',function() {if(!view.data) {return;} view.league=this.value;view.plan=null;view.selected=null;view.month=league().current_date.slice(0,7);populateTeams();render();showError('Choose a target date.');});
    $('calendar-team').addEventListener('change',function() {view.team=this.value;render();});
    [['calendar-prev',-1],['calendar-next',1]].forEach(function(pair) {$(pair[0]).addEventListener('click',function() {
      if(!view.month) {return;} var p=view.month.split('-').map(Number);view.month=new Date(Date.UTC(p[0],p[1]-1+pair[1],1)).toISOString().slice(0,7);render();
    });});
    $('calendar-end').addEventListener('click',function() {if(league()) {view.month=league().last.slice(0,7);selectDate(league().last);}});
    $('calendar-start').addEventListener('click',startCalendar);
    $('btn-playoffs').addEventListener('click',function() {
      var rows=(state.plan && state.plan.transition && state.plan.transition.leagues)||[];
      document.querySelectorAll('.league-pick').forEach(function(pick) {
        var row=rows.find(function(r){return r.key===pick.value;});pick.checked=!!row&&!row.champion&&row.regular_remaining===0;pick.disabled=!!row&&!!row.champion;
      });
      $('allow-season-end').checked=true;startSim(7);
    });
    renderSeasonReadiness(state.plan);loadCalendar();
    var wasBusy=busy();setInterval(function(){var now=busy();if(wasBusy&&!now) {loadCalendar();}wasBusy=now;refreshCalendarButtons();},1500);
  }
  if(document.readyState==='loading') {document.addEventListener('DOMContentLoaded',init);} else {init();}
})();
