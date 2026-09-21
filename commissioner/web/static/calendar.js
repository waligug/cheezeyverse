'use strict';
(function () {
  var view = { data:null, league:'prep', team:'', month:null, selected:null, plan:null, request:0 };
  function dateText(value) { return new Date(value + 'T12:00:00Z').toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'}); }
  function league() { return view.data && view.data.leagues.find(function (l) { return l.key === view.league; }); }
  function busy() { return state.busy || state.osBusy; }
  window.renderCalendarDates = function () {
    if (!view.data) { return; }
    if (window.renderLeagueDetails) window.renderLeagueDetails(view.data, state.plan, busy());
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
    var playoffs = playoffTargets().length > 0;
    $('btn-playoffs').disabled = busy() || !playoffs;
    if ($('btn-playoffs-all')) {
      $('btn-playoffs-all').disabled = busy() || !playoffs;
      $('btn-playoffs-all').textContent = chain.on ? 'Playing the playoffs…'
                                                   : 'Play the whole playoffs';
    }
    if ($('playoff-note')) {
      $('playoff-note').textContent = !playoffs
        ? 'No league is waiting on playoffs right now.'
        : chain.on ? ('Round ' + (chain.rounds + 1) + '. Keeps going until every champion is '
                      + 'decided; stops by itself if a round fails or moves nothing.')
        : 'Runs seven days at a time until every league has a champion.';
    }
  };
  window.renderSeasonReadiness = function (plan) {
    var host = $('season-readiness'), transition = plan && plan.transition;
    if (window.renderOffseasonGuide) window.renderOffseasonGuide(plan);
    if (view.data && window.renderLeagueDetails) window.renderLeagueDetails(view.data, plan, busy());
    if (!host) { return; }
    host.className = 'season-readiness' + (transition && transition.ready ? ' ready' : '');
    host.textContent = !transition ? 'Checking the three season states…' : transition.ready
      ? 'All three champions are decided. Ready to develop players and start the next season.'
      : (transition.reasons || []).join(' ');
  };
  // ONE DEFINITION OF "waiting on playoffs", used by both buttons and by the chain, so a
  // button cannot light up for a league the run would refuse. A league qualifies when its
  // regular season is done and nobody has won yet.
  function playoffTargets() {
    var rows = (state.plan && state.plan.transition && state.plan.transition.leagues) || [];
    return rows.filter(function (r) { return !r.champion && r.regular_remaining === 0; })
               .map(function (r) { return r.key; });
  }
  // Where each league's calendar stands, so the chain can tell a round that played basketball
  // from a round that achieved nothing.
  function datePositions() {
    return ((view.data && view.data.leagues) || []).map(function (l) {
      return l.key + ':' + l.current_date;
    }).join('|');
  }
  function startPlayoffRound(keys) {
    // The checkboxes still move, because somebody watching the panel should see which leagues
    // are going - but they are a REFLECTION of the request, not its source.
    var rows = (state.plan && state.plan.transition && state.plan.transition.leagues) || [];
    document.querySelectorAll('.league-pick').forEach(function (pick) {
      var row = rows.find(function (r) { return r.key === pick.value; });
      pick.checked = keys.indexOf(pick.value) >= 0;
      pick.disabled = !!row && !!row.champion;
    });
    chain.before = datePositions();
    startSim(7, {leagues: keys, dryRun: false, allowSeasonEnd: true});
  }

  // THE WHOLE PLAYOFFS, one seven-day run at a time.
  //
  // There is no single "sim the playoffs" call to make: FBPB3's schedule export holds regular
  // season dates only, so nothing downstream knows when the playoffs end - the only way to find
  // out is to play forward and look. So this chains the run that already exists and stops the
  // moment it should, which means three separate stop conditions, because a loop that starts
  // sims by itself has to be able to stop itself:
  //
  //   * EVERY CHAMPION DECIDED. The reason it was started; the ordinary ending.
  //   * A ROUND THAT MOVED NOTHING. If no league's calendar advanced, playing another seven
  //     days will not advance it either - that is a game sitting on a screen the driver cannot
  //     get past, and chaining into it would just keep failing in a way nobody is watching.
  //   * A ROUND THAT FAILED, or a cap of ten rounds - seventy days, far past any postseason.
  //
  // It also stops the instant anything else takes the lock, and on any refusal, so it can never
  // fight the offseason for the saves.
  var chain = { on: false, rounds: 0, max: 10, before: '' };

  function stopChain(message, bad) {
    if (!chain.on) { return; }
    chain.on = false;
    refreshCalendarButtons();
    if (message) { toast(message, !!bad); }
  }

  function continueChain() {
    if (!chain.on) { return; }
    if (state.lastRunOk === false) {
      stopChain('The playoff run stopped, so the rest of the playoffs were not started.', true);
      return;
    }
    var left = playoffTargets();
    if (!left.length) {
      stopChain('Every playoff is finished - all three champions are decided.');
      return;
    }
    if (datePositions() === chain.before) {
      stopChain('That round did not move any league forward, so the playoffs were not chained '
                + 'any further. Look at the log before trying again.', true);
      return;
    }
    if (++chain.rounds >= chain.max) {
      stopChain('Stopped after ' + chain.max + ' playoff rounds without finishing. Press it '
                + 'again if that is genuinely how long these playoffs are.', true);
      return;
    }
    startPlayoffRound(left);
  }

  function showError(message) {
    $('calendar-preview').textContent = message;
    view.plan = null;
    refreshCalendarButtons();
  }
  function loadCalendar() {
    if (busy()) { return Promise.resolve(); }
    var request = ++view.request;
    view.plan = null;
    refreshCalendarButtons();
    return api('/api/calendar').then(function (data) {
      if (request !== view.request) { return; }
      if (!data.ok) { showError(data.error || 'Calendar unavailable.'); return; }
      view.data = data;
      renderCalendarDates();
      // MOVE THE GRID WHEN THE SEASON MOVES. Setting the month only when it is null is right
      // for an ordinary refresh - somebody browsing ahead to March should stay in March - and
      // wrong across a rollover: the new season starts in a different month, so the grid sat
      // on the old one with every date disabled, showing a calendar nobody could click.
      // Jumping only when the SEASON changes keeps deliberate browsing intact.
      var now = league();
      if (now && (view.season !== now.season || view.month < now.first.slice(0,7)
                  || view.month > now.last.slice(0,7))) {
        view.month = now.current_date.slice(0,7);
      }
      view.season = now ? now.season : null;
      if (!view.month && now) { view.month = now.current_date.slice(0,7); }
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
    // ++view.request, not just view.plan=null. Clearing the plan does not stop the reply that
    // is already in the air: switching from Prep to Pro while a Prep preview was in flight let
    // the old reply land, so view.league was pro, view.plan.reference was prep, "Sim to this
    // target" lit up, and starting it would have run the PREP plan. The generation counter is
    // what selectDate already checks; every action that invalidates a plan has to bump it.
    $('calendar-league').addEventListener('change',function() {if(!view.data) {return;} ++view.request;view.league=this.value;view.plan=null;view.selected=null;view.month=league().current_date.slice(0,7);view.season=league().season;populateTeams();render();showError('Choose a target date.');});
    $('calendar-team').addEventListener('change',function() {view.team=this.value;render();});
    [['calendar-prev',-1],['calendar-next',1]].forEach(function(pair) {$(pair[0]).addEventListener('click',function() {
      if(!view.month) {return;} var p=view.month.split('-').map(Number);view.month=new Date(Date.UTC(p[0],p[1]-1+pair[1],1)).toISOString().slice(0,7);render();
    });});
    $('calendar-end').addEventListener('click',function() {if(league()) {view.month=league().last.slice(0,7);selectDate(league().last);}});
    $('calendar-start').addEventListener('click',startCalendar);
    // THE PLAYOFF BUTTON SAYS WHAT IT WANTS, rather than ticking hidden boxes and hoping.
    // It used to set #allow-season-end and call startSim(7) - but startSim only forwards that
    // flag when #season-end-group is VISIBLE, so with the advanced controls collapsed the
    // request went out as allow_season_end:false and the backend refused the very thing the
    // button exists to do. It also inherited whatever #dry-run happened to be left on, so a
    // forgotten tick turned "play the playoffs" into a run that played nothing.
    $('btn-playoffs').addEventListener('click',function() {
      var eligible=playoffTargets();
      if(!eligible.length) {toast('No league is waiting on playoffs.',true);return;}
      chain.on=false;                       // one round, deliberately: do not chain
      startPlayoffRound(eligible);
    });
    // ASKS FIRST. Every other button here starts one run you can watch finish; this one starts
    // runs until a season ends, and it was added the same afternoon a mis-click started a sim
    // that had to be killed and restored. A sentence and a click is a cheap price for that.
    $('btn-playoffs-all').addEventListener('click',function() {
      var eligible=playoffTargets();
      if(!eligible.length) {toast('No league is waiting on playoffs.',true);return;}
      var names=eligible.join(', ');
      if(!window.confirm('Play the whole playoffs for ' + names + '?\n\nThis runs seven days '
          + 'at a time and keeps going until every champion is decided. It stops on its own if '
          + 'a round fails or moves nothing.')) {return;}
      chain.on=true; chain.rounds=0;
      startPlayoffRound(eligible);
    });
    renderSeasonReadiness(state.plan);loadCalendar();
    // SERIALISED, not fired together. Both /api/calendar and the readiness inside /api/state
    // take the same save lock without blocking, so issuing them at once means one of them
    // loses and reports a transient failure - either a calendar stuck in an error state or a
    // season panel that believes a save is running. Chaining them costs one round trip and
    // removes the race entirely; refreshState retries once on its own if it still lost.
    var wasBusy=busy();setInterval(function(){
      var now=busy();
      if(wasBusy&&!now) {
        // The chain decides AFTER both reads land, never before: it needs this run's champions
        // and this run's dates, and asking earlier reads the previous round's answer and starts
        // a round that was not wanted.
        Promise.resolve(loadCalendar())
          .then(function(){ return window.refreshState ? refreshState() : null; })
          .then(continueChain, function(){ stopChain('Lost contact with the panel, so the rest '
                                                     + 'of the playoffs were not started.', true); });
      }
      wasBusy=now;refreshCalendarButtons();
    },1500);
  }
  if(document.readyState==='loading') {document.addEventListener('DOMContentLoaded',init);} else {init();}
})();
