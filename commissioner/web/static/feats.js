/* Optional, on-demand scan. No work is scheduled from the sim pipeline. */
(() => {
  const scan = document.getElementById('feats-scan');
  const send = document.getElementById('feats-send');
  const note = document.getElementById('feats-note');
  const results = document.getElementById('feats-results');
  let token = null;
  scan.onclick = async () => {
    scan.disabled = true; send.disabled = true; token = null;
    note.textContent = 'Scanning exported box scores...'; results.replaceChildren();
    try {
      const data = await postJSON('/api/feats/scan', {scope: document.getElementById('feats-scope').value});
      if (!data.ok) throw new Error(data.error);
      token = data.token;
      const fresh = data.events.filter(e => !e.sent).length;
      note.textContent = `${data.events.length} performances; ${fresh} not posted. Season ${data.season}, through ${new Date(data.through).toLocaleString()}.`
        + (data.configured ? '' : ' Set DISCORD_FEATS_WEBHOOK_URL in .env to enable posting.');
      for (const event of data.events) {
        const p = document.createElement('p');
        p.textContent = `${event.sent ? 'Posted | ' : ''}${event.name} | ${event.league} | day ${event.day}${event.playoff ? ' (playoffs)' : ''} vs ${event.opponent}: ${event.feats.join('; ')}. ${event.line}`;
        results.append(p);
      }
      send.disabled = !fresh || !data.configured;
    } catch (error) { note.textContent = error.message; }
    finally { scan.disabled = false; }
  };
  send.onclick = async () => {
    send.disabled = true; scan.disabled = true;
    note.textContent = 'Posting new feats to #statistical-feats...';
    try {
      const data = await postJSON('/api/feats/send', {token});
      if (!data.ok) throw new Error(data.error);
      note.textContent = `Posted ${data.sent} new feats to #statistical-feats. Repeat scans skip delivered performances.`;
    } catch (error) { note.textContent = error.message; send.disabled = false; }
    finally { scan.disabled = false; }
  };
})();
