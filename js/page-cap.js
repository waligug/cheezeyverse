/* -------------------------------------------------------------------------------- the cap
 * What every pro team is paying, one team at a time, drawn the way a cap sheet is actually read:
 * a single column stacked from the floor, each contract a block whose HEIGHT is the money, with
 * the salary cap and the luxury tax as dashed lines across it. Switch teams and the scale does
 * not move, so two columns can be compared by eye.
 *
 * WHY THE BIGGEST CONTRACT SITS AT THE TOP. The stack is built cheapest-first from $0, so the
 * column's total height IS the payroll and the man being paid most is the widest band at the top
 * of it. That is the whole point of the picture: you can see at a glance whether a team's money
 * is in one contract or spread across fifteen.
 *
 * EVERYTHING COMES FROM cap.json, written at publish time by publish._write_cap out of the save
 * itself. Nothing here parses the game's HTML - `page-index.js` states that rule and it holds
 * here too: a second implementation in JavaScript is how the two start disagreeing. This module
 * does no arithmetic on money beyond adding a roster up and scaling it to pixels.
 * ---------------------------------------------------------------------------------------- */

import { $, el, clear, freshJSON, money, svg, note, renderChrome, renderFooter } from './ui.js';

const LEAGUE = 'pro';

/* The reference does not colour a column by team - it gives every contract its own colour so the
 * blocks read as separate people. Cycled by index, drawn from the site's own palette plus enough
 * neighbours to keep fifteen adjacent blocks distinguishable. */
const BLOCKS = [
  '#D9901A', '#2C5F8A', '#96261F', '#2F6B37', '#7A4B00',
  '#C0392B', '#1D5C8A', '#8A5A00', '#4B7B3F', '#B5651D',
];

let TEAM = null;
let DATA = null;
let STATS = {};          // name -> the player's line from stats.json, when he has one

/** His season, per game, or null when he has not played one. */
function lineFor(name) {
  const row = STATS[name];
  if (!row || !row.G) return null;
  const per = (n) => (n / row.G).toFixed(1);
  return {
    games: row.G, pts: per(row.PTS), reb: per(row.REB), ast: per(row.AST),
    stl: per(row.STL), blk: per(row.BLK), ts: row.ts, page: row.page, rank: row.rank || {},
  };
}

/* ONE CARD, MOVED - not one per block. Fifteen blocks a team and twenty teams would otherwise
 * mean three hundred nodes built on every switch, and the card has to outlive the SVG anyway
 * because it is positioned against the page rather than the chart. */
let CARD = null;

function card() {
  if (!CARD) {
    CARD = el('div', { class: 'cv-capcard', hidden: true });
    document.body.append(CARD);
  }
  return CARD;
}

function showCard(event, man) {
  const box = card();
  clear(box);
  const line = lineFor(man.name);
  box.append(el('div', { class: 'cv-capcard-name' }, [
    man.name,
    man.ours ? el('span', { class: 'cv-capcard-ours' }, 'one of ours') : null,
  ]));
  box.append(el('div', { class: 'cv-capcard-money' }, [
    money(man.salary),
    el('span', { class: 'cv-muted' }, man.years === 1 ? ' · final year'
      : ` · ${man.years} years`),
    man.position ? el('span', { class: 'cv-muted' }, ` · ${man.position}`) : null,
  ]));
  if (line) {
    box.append(el('div', { class: 'cv-capcard-stats' }, [
      ['PTS', line.pts], ['REB', line.reb], ['AST', line.ast],
      ['STL', line.stl], ['BLK', line.blk],
    ].map(([label, v]) => el('span', {}, [
      el('b', {}, v), el('i', {}, label),
    ]))));
    box.append(el('div', { class: 'cv-capcard-foot' },
      `${line.games} games · ${Math.round(line.ts * 1000) / 10}% true shooting`));
  } else {
    // NOT AN ERROR. The draft pool and anybody signed since the last export have no line yet,
    // and saying so is better than showing five zeroes as though he played and did nothing.
    box.append(el('div', { class: 'cv-capcard-foot' }, 'No games in the published season yet.'));
  }
  box.hidden = false;
  moveCard(event);
}

function moveCard(event) {
  const box = card();
  if (box.hidden) return;
  const pad = 14;
  const w = box.offsetWidth || 240;
  const h = box.offsetHeight || 90;
  // Flip rather than overflow: near the right or bottom edge the card would otherwise widen the
  // page and produce a horizontal scrollbar on a phone.
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + w > window.innerWidth - 8) x = event.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = event.clientY - h - pad;
  box.style.left = `${Math.max(8, x)}px`;
  box.style.top = `${Math.max(8, y)}px`;
}

function hideCard() {
  if (CARD) CARD.hidden = true;
}

/** Round a ceiling up to a clean step so the gridlines land on round money. */
function ceiling(value, step) {
  return Math.max(step, Math.ceil(value / step) * step);
}

/* -------------------------------------------------------------------------------- the column */

function board(team, data) {
  const men = [...team.players].sort((a, b) => a.salary - b.salary);   // cheapest at the floor
  const lines = [
    data.cap ? { at: data.cap, label: 'Salary cap' } : null,
    data.luxury_tax ? { at: data.luxury_tax, label: 'Luxury tax' } : null,
  ].filter(Boolean);

  // ONE SCALE FOR EVERY TEAM. Recomputing it per team would silently rescale the picture on each
  // click and make two columns look alike when they are not - which is the one thing this chart
  // exists to show. So the top is the whole league's worst case, not this team's.
  const biggest = Math.max(
    ...data.teams.map((t) => t.payroll),
    ...lines.map((l) => l.at),
    1,
  );
  const top = ceiling(biggest * 1.08, 10_000_000);

  const W = 760;
  const H = 520;
  const padL = 72;
  const padR = 18;
  const padT = 12;
  const padB = 26;
  const y = (v) => padT + (1 - v / top) * (H - padT - padB);
  const colX = padL + 40;
  const colW = W - padR - colX - 40;

  const root = svg('svg', {
    class: 'cv-chart',
    viewBox: `0 0 ${W} ${H}`,
    preserveAspectRatio: 'xMidYMid meet',
    role: 'img',
    'aria-label': `${team.city} ${team.nickname} payroll ${money(team.payroll)} across `
      + `${men.length} contracts, against a salary cap of ${money(data.cap)}`,
  });

  // THE GOLD, defined once and referenced by every block that belongs to a real person. A flat
  // fill would read as just another colour in a column that already has ten; a gradient with a
  // bright diagonal band reads as metal, which is the point - you should be able to find your
  // own player without looking for his name.
  root.append(svg('defs', {}, [svg('filter', {
    id: 'cv-oursglow', x: '-25%', y: '-25%', width: '150%', height: '150%',
  }, svg('feDropShadow', {
    dx: 0, dy: 0, stdDeviation: 3, 'flood-color': '#F2B705', 'flood-opacity': '.95',
  })), svg('linearGradient', {
    id: 'cv-gold', x1: '0%', y1: '0%', x2: '100%', y2: '100%',
  }, [
    svg('stop', { offset: '0%', 'stop-color': '#B8860B' }),
    svg('stop', { offset: '38%', 'stop-color': '#F2B705' }),
    svg('stop', { offset: '50%', 'stop-color': '#FFF3B0' }),
    svg('stop', { offset: '62%', 'stop-color': '#F2B705' }),
    svg('stop', { offset: '100%', 'stop-color': '#B8860B' }),
  ])]));

  // gridlines every $10M, labelled down the left
  for (let v = 0; v <= top; v += 10_000_000) {
    root.append(svg('line', {
      x1: padL, x2: W - padR, y1: y(v), y2: y(v), class: 'cv-chart-grid',
    }));
    root.append(svg('text', {
      x: padL - 8, y: y(v) + 4, class: 'cv-chart-label', 'text-anchor': 'end',
    }, `$${Math.round(v / 1_000_000)}M`));
  }

  // the stack
  let running = 0;
  men.forEach((man, i) => {
    const h = Math.max(1, y(running) - y(running + man.salary));
    const boxY = y(running + man.salary);
    const over = data.luxury_tax && running + man.salary > data.luxury_tax;
    const rect = svg('rect', {
      x: colX, y: boxY, width: colW, height: h, rx: 2,
      class: `cv-chart-bar${man.ours ? ' is-ours' : ''}${over ? ' is-over' : ''}`,
      fill: man.ours ? 'url(#cv-gold)' : BLOCKS[i % BLOCKS.length],
      tabindex: '0',
      role: 'img',
      'aria-label': `${man.name}, ${money(man.salary)}`,
    }, svg('title', {}, `${man.name} - ${money(man.salary)}`));
    // Pointer AND keyboard, because a block is focusable and a tooltip nobody can reach by tab
    // is a tooltip half the people reading this cannot use.
    rect.addEventListener('mouseenter', (e) => showCard(e, man));
    rect.addEventListener('mousemove', moveCard);
    rect.addEventListener('mouseleave', hideCard);
    rect.addEventListener('focus', (e) => {
      const r = rect.getBoundingClientRect();
      showCard({ clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }, man);
    });
    rect.addEventListener('blur', hideCard);
    root.append(rect);

    // A LABEL ONLY WHERE ONE FITS. Fifteen names in a column this tall means most blocks are a
    // few pixels high; printing into them would overlap into an unreadable smear. Anything too
    // short to hold text stays a coloured band and keeps its hover title.
    if (h >= 16) {
      root.append(svg('text', {
        x: colX + 10, y: boxY + h / 2 + 4, class: 'cv-chart-boxlabel',
      }, man.name));
      root.append(svg('text', {
        x: colX + colW - 10, y: boxY + h / 2 + 4,
        class: 'cv-chart-boxlabel', 'text-anchor': 'end',
      }, money(man.salary)));
    }
    running += man.salary;
  });

  // The dashed reference lines, drawn last so they sit over the blocks. The label goes ABOVE
  // the line and is right-anchored INSIDE the plot, which is the idiom page-career.js already
  // uses - putting it out in the right margin clipped "Luxury tax $77,000,000" mid-number.
  for (const line of lines) {
    // A cream underlay first, so the dashed line and its label stay legible where they cross a
    // dark contract. Without it "Luxury tax" is dark-on-dark exactly where it matters most.
    root.append(svg('line', {
      x1: padL, x2: W - padR, y1: y(line.at), y2: y(line.at), class: 'cv-chart-refline-halo',
    }));
    root.append(svg('line', {
      x1: padL, x2: W - padR, y1: y(line.at), y2: y(line.at), class: 'cv-chart-refline',
    }));
    root.append(svg('text', {
      x: W - padR - 2, y: y(line.at) - 6, class: 'cv-chart-reflabel', 'text-anchor': 'end',
    }, `${line.label} ${money(line.at)}`));
  }

  root.append(svg('line', {
    x1: padL, x2: W - padR, y1: H - padB, y2: H - padB, class: 'cv-chart-axis',
  }));
  return root;
}

/* -------------------------------------------------------------------------------- the pieces */

function renderTabs() {
  const strip = $('#teamtabs');
  clear(strip);
  for (const team of DATA.teams) {
    const on = team.abbrev === TEAM;
    const ours = team.players.some((m) => m.ours);
    strip.append(el('button', {
      type: 'button',
      class: `cv-tab${on ? ' is-on' : ''}${ours ? ' has-ours' : ''}`,
      'aria-pressed': on ? 'true' : 'false',
      title: ours ? `${team.city} ${team.nickname} - one of ours plays here`
        : `${team.city} ${team.nickname}`,
      onclick: () => { TEAM = team.abbrev; render(); },
    }, team.abbrev));
  }
}

function renderBoard() {
  const team = DATA.teams.find((t) => t.abbrev === TEAM) || DATA.teams[0];
  $('#team-name').textContent = `${team.city} ${team.nickname}`;
  const room = DATA.cap - team.payroll;
  $('#team-total').textContent = `${money(team.payroll)} committed · `
    + (room >= 0 ? `${money(room)} under the cap` : `${money(-room)} over the cap`);
  const box = $('#board');
  clear(box);
  box.append(board(team, DATA));
}

function renderTable() {
  const box = $('#table');
  clear(box);
  const rows = [...DATA.teams].sort((a, b) => b.payroll - a.payroll);
  const body = el('tbody', {}, rows.map((t) => {
    const over = DATA.luxury_tax && t.payroll > DATA.luxury_tax;
    return el('tr', { class: t.players.some((m) => m.ours) ? 'cv-ours' : null }, [
      el('td', {}, el('button', {
        type: 'button', class: 'cv-btn cv-small cv-ghost',
        onclick: () => { TEAM = t.abbrev; render(); },
      }, t.abbrev)),
      el('td', {}, `${t.city} ${t.nickname}`),
      el('td', { class: 'cv-right cv-num' }, money(t.payroll)),
      el('td', { class: 'cv-right cv-num' }, money(DATA.cap - t.payroll)),
      // is-down is the RED one. Over the tax is not a good thing, and is-up is green.
      el('td', { class: over ? 'cv-right cv-num cv-delta is-down' : 'cv-right cv-muted' },
        over ? `+${money(t.payroll - DATA.luxury_tax)}` : '--'),
    ]);
  }));
  box.append(el('div', { class: 'cv-scroll' }, el('table', { class: 'cv-table' }, [
    el('thead', {}, el('tr', {}, [
      el('th', {}, 'Team'), el('th', {}, ''),
      el('th', { class: 'cv-right' }, 'Payroll'),
      el('th', { class: 'cv-right' }, 'Cap room'),
      el('th', { class: 'cv-right' }, 'Tax'),
    ])),
    body,
  ])));
  const total = DATA.teams.reduce((n, t) => n + t.payroll, 0);
  $('#league-total').textContent = `${money(total)} across ${DATA.teams.length} teams`;
}

function render() {
  hideCard();          // a card left open would describe a block the new team does not have
  renderTabs();
  renderBoard();
  renderTable();
}

/* -------------------------------------------------------------------------------- boot */

async function load() {
  // Both at once. stats.json is optional - it is what puts a scoring line on the hover card,
  // and a missing one costs the card its stats rather than costing the page its chart.
  const [data, stats] = await Promise.all([
    freshJSON(`leagues/${LEAGUE}/cap.json`),
    freshJSON(`leagues/${LEAGUE}/stats.json`),
  ]);
  for (const row of (stats && stats.players) || []) {
    if (row && row.name) STATS[row.name] = row;
  }
  if (!data || !Array.isArray(data.teams) || !data.teams.length) {
    // freshJSON resolves to null for a 404 as well as for a network failure, so "not published
    // yet" is the honest reading rather than an error.
    clear($('#board'));
    $('#notices').append(note(null, 'The pros have not published their money yet. The panel '
      + 'fills this in after the next Sim Week.'));
    return;
  }
  DATA = data;
  TEAM = data.teams[0].abbrev;
  $('#intro').textContent = `Every contract on a pro roster in ${data.season}, stacked from the `
    + 'floor. The dashed lines are the salary cap and the luxury tax.';
  if (!data.scale) {
    // EVERY CONTRACT IS THE SAME NUMBER. Say so, rather than letting fifteen identical blocks
    // imply a league where everybody happens to earn alike.
    $('#notices').append(note(null, 'Every contract in the league is still the league default. '
      + 'Real salaries arrive the first time free agency runs, at the next offseason.'));
  }
  render();
}

renderChrome({ active: 'cap.html' });
renderFooter();
load();
