'use strict';
(() => {
  const root = document.getElementById('radio-cooperative');
  if (!root || !document.getElementById('coop-start')) return;
  const el = id => document.getElementById(id);
  const slot = el('coop-slot'), timeout = el('coop-timeout'), ack = el('coop-higher-ack');
  const start = el('coop-start'), stop = el('coop-stop'), recover = el('coop-recover');
  const phase = el('coop-phase'), output = el('coop-output');
  let current = null, busy = false, poll = null;
  const active = state => state && ['preflight', 'running'].includes(state.phase);
  const canLaunch = () => !busy && !active(current) && current?.phase !== 'unknown' && slot.value && ack.checked;
  function buttons() {
    start.disabled = !canLaunch();
    stop.disabled = busy || !active(current) || current.cancel_requested;
    recover.hidden = !current || current.phase !== 'unknown';
    recover.disabled = busy;
  }
  function render(job) {
    current = job;
    const state = job.phase || 'idle';
    phase.textContent = ({
      preflight: 'Verifying radio…', running: 'Module running',
      cancelled: 'Confirmed cancelled', finished: 'Completed',
      unknown: 'Execution unknown — inspect radio', failed: 'Run refused',
      idle: 'No module running'
    })[state] || state;
    const lines = [job.message || ''];
    if (Array.isArray(job.reports) && job.reports.length)
      lines.push('', ...job.reports);
    if (job.result) lines.push('', 'Result:', JSON.stringify(job.result, null, 2));
    if (job.error) lines.push('', 'Error: ' + job.error);
    if (job.cancel_requested) lines.push('', job.cancel_acknowledged ?
      'The radio acknowledged the stop request.' :
      'Stop requested. Awaiting acknowledgement and module cleanup.');
    output.textContent = lines.join('\n').trim();
    buttons();
  }
  async function call(action, values = {}) {
    const res = await fetch('/radio/coop/' + action, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...values, token: root.dataset.token})
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Live module request failed.');
    return data;
  }
  function schedule() {
    clearTimeout(poll);
    if (active(current)) poll = setTimeout(refresh, 350);
  }
  async function refresh() {
    if (!current || !current.id) return;
    try { render(await call('status', {id: current.id})); }
    catch (e) {
      phase.textContent = 'Status unavailable; do not retry launch';
      output.textContent = e.message;
    }
    schedule();
  }
  [slot, ack].forEach(node => node.addEventListener('change', buttons));
  start.onclick = async () => {
    const selected = Number(slot.value), seconds = Number(timeout.value);
    if (!slot.value || !ack.checked || !Number.isInteger(seconds) ||
        seconds < 1 || seconds > 120) {
      output.textContent = 'Choose an installed module and a deadline between 1–120 seconds.';
      return;
    }
    if (!window.confirm('Run slot ' + selected +
          ' as unrestricted native code? It may transmit or modify hardware. ' +
          'Only a cooperative API-25 module can honor Stop.')) return;
    busy = true; buttons();
    phase.textContent = 'Submitting launch…';
    try {
      render(await call('start', {slot:selected, timeout:seconds,
                                 higher_api_ack:true, confirm:'run'}));
      schedule();
    } catch(e) {
      phase.textContent = 'Launch request uncertain — inspecting server job';
      output.textContent = e.message;
      try { render(await call('current')); schedule(); }
      catch (_) { phase.textContent = 'Connection lost — inspect radio'; }
    } finally { busy = false; buttons(); }
  };
  stop.onclick = async () => {
    if (!active(current)) return;
    busy = true; buttons();
    try { render(await call('cancel', {id:current.id})); }
    catch (e) { output.textContent = e.message + '\nStop delivery unknown; check status.'; }
    finally { busy = false; buttons(); schedule(); }
  };
  recover.onclick = async () => {
    if (!current || current.phase !== 'unknown' || !window.confirm(
      'Recheck idle RX over the programming cable? This does not prove the previous module returned normally.')) return;
    busy = true; buttons();
    try { render(await call('recover', {confirm:'recheck'})); }
    catch (e) { output.textContent = e.message + '\nRadio execution remains uncertain.'; }
    finally { busy = false; buttons(); }
  };
  // Reconnect to the last server-side job after a page reload. Do not
  // silently launch or retry native execution under any circumstances.
  call('current').then(job => { render(job); schedule(); })
    .catch(e => { phase.textContent = 'Job status unavailable'; output.textContent = e.message; });
})();
