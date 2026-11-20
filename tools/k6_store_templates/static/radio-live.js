(() => {
  const status = document.getElementById('live-status');
  const tune = document.getElementById('live-tune');
  if (!status || !tune) return;
  const result = document.getElementById('live-result');
  async function run(action, payload = {}) {
    status.disabled = tune.disabled = true;
    result.textContent = 'Reading live radio…';
    try {
      const response = await fetch('/radio/live/' + action, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({...payload, token:status.dataset.token})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Live request failed');
      result.textContent = data.vfos.map((v,i) =>
        (i === data.master ? '▶ ' : '  ') + ['A','B'][i] + '  ' +
        (v.hz / 1e6).toFixed(5) + ' MHz  ' + v.mode + (v.memory ? ' MEM' : ' VFO')
      ).join('\n') + '\nBattery ' + data.battery_volts.toFixed(2) +
      ' V · RSSI ' + data.rssi_dbm + ' dBm · ' + (data.transmitting ? 'TX' : 'RX');
    } catch(e) { result.textContent = e.message; }
    finally { status.disabled = tune.disabled = false; }
  }
  status.onclick = () => run('status');
  tune.onclick = () => {
    const raw = document.getElementById('live-frequency').value;
    if (!raw) {result.textContent = 'Enter a frequency.'; return;}
    run('tune', {hz: Math.round(Number(raw)*1e6)});
  };
})();
