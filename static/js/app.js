/* ============================================================
   Greenhouse Control Dashboard
============================================================ */

const API = '';          // same origin
let charts = {};
let pollTimer = null;
let manualOpen = false;

// Ring buffer (max 10 readings) per metric key for trend computation
const trendHistory = {};

function pushTrend(key, value) {
  if (!trendHistory[key]) trendHistory[key] = [];
  trendHistory[key].push(value);
  if (trendHistory[key].length > 10) trendHistory[key].shift();
}

function renderTrend(elemId, key, threshold) {
  const h = trendHistory[key];
  const el = document.getElementById(elemId);
  if (!el || !h || h.length < 2) return;
  const delta = h[h.length - 1] - h[0];
  const absDelta = Math.abs(delta);
  if (absDelta < threshold) {
    el.textContent = '→';
    el.className = 'trend-badge trend-stable';
  } else if (delta > 0) {
    el.textContent = `▲ +${absDelta.toFixed(1)}`;
    el.className = 'trend-badge trend-up';
  } else {
    el.textContent = `▼ ${delta.toFixed(1)}`;
    el.className = 'trend-badge trend-down';
  }
}

// ----------------------------------------------------------------
// Clock
// ----------------------------------------------------------------
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleDateString('de-DE') + '  ' +
    now.toLocaleTimeString('de-DE');
}
setInterval(updateClock, 1000);
updateClock();

// ----------------------------------------------------------------
// Toast notifications
// ----------------------------------------------------------------
function showToast(msg, duration = 2500) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add('hidden'), duration);
}

// ----------------------------------------------------------------
// Gauge (SVG arc)
// ----------------------------------------------------------------
function arcPath(cx, cy, r, startDeg, endDeg) {
  const toRad = d => (d - 90) * Math.PI / 180;
  const x1 = cx + r * Math.cos(toRad(startDeg));
  const y1 = cy + r * Math.sin(toRad(startDeg));
  const x2 = cx + r * Math.cos(toRad(endDeg));
  const y2 = cy + r * Math.sin(toRad(endDeg));
  const large = (endDeg - startDeg) > 180 ? 1 : 0;
  return `M ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`;
}

const GAUGE_START = 135;
const GAUGE_TOTAL = 270;

function initGauge() {
  document.getElementById('gauge-bg-path').setAttribute(
    'd', arcPath(100, 100, 76, GAUGE_START, GAUGE_START + GAUGE_TOTAL)
  );
}

function setGauge(fraction) {
  fraction = Math.max(0, Math.min(1, fraction));
  const pct = Math.round(fraction * 100);
  const end = GAUGE_START + fraction * GAUGE_TOTAL;

  const fillEl = document.getElementById('gauge-fill-path');
  if (fraction < 0.001) {
    fillEl.setAttribute('d', '');
  } else {
    fillEl.setAttribute('d', arcPath(100, 100, 76, GAUGE_START, end));
  }

  // Colour: blue → yellow → red
  let colour = '#58a6ff';
  if (fraction > 0.8) colour = '#f85149';
  else if (fraction > 0.6) colour = '#e3b341';
  fillEl.style.stroke = colour;

  document.getElementById('gauge-text').textContent = pct + '%';
}

// ----------------------------------------------------------------
// Status dots
// ----------------------------------------------------------------
function setDot(id, state) {
  const el = document.getElementById(id);
  el.className = 'status-dot ' + state;
}

// ----------------------------------------------------------------
// Format helpers
// ----------------------------------------------------------------
function fmtVal(v, decimals = 1) {
  return v == null ? '--' : Number(v).toFixed(decimals);
}

function fmtAge(ts) {
  if (!ts) return '--';
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60)  return `vor ${diff}s`;
  if (diff < 3600) return `vor ${Math.floor(diff/60)}min`;
  return `vor ${Math.floor(diff/3600)}h`;
}

// ----------------------------------------------------------------
// Sensor update
// ----------------------------------------------------------------
async function fetchSensors() {
  try {
    const r = await fetch(`${API}/api/sensors/current`);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();

    const inside  = d.inside;
    const outside = d.outside;
    const hasBle  = inside || outside;
    setDot('dot-ble', hasBle ? 'ok' : 'warn');

    updateSensorCard('in-temp',  inside,  'temperature');
    updateSensorCard('in-hum',   inside,  'humidity');
    updateSensorCard('out-temp', outside, 'temperature');
    updateSensorCard('out-hum',  outside, 'humidity');

    // Push trend history
    if (inside?.temperature != null)  pushTrend('in-temp',  inside.temperature);
    if (inside?.humidity    != null)  pushTrend('in-hum',   inside.humidity);
    if (outside?.temperature != null) pushTrend('out-temp', outside.temperature);
    if (outside?.humidity    != null) pushTrend('out-hum',  outside.humidity);

    // Render trend badges (temp: 0.3°C threshold, humidity: 1%)
    renderTrend('trend-in-temp',  'in-temp',  0.3);
    renderTrend('trend-in-hum',   'in-hum',   1.0);
    renderTrend('trend-out-temp', 'out-temp', 0.3);
    renderTrend('trend-out-hum',  'out-hum',  1.0);

    if (inside)  document.getElementById('in-temp-ts').textContent  = fmtAge(inside.timestamp);
    if (outside) document.getElementById('out-temp-ts').textContent = fmtAge(outside.timestamp);
    if (inside)  document.getElementById('in-hum-ts').textContent   = fmtAge(inside.timestamp);
    if (outside) document.getElementById('out-hum-ts').textContent  = fmtAge(outside.timestamp);

    if (inside?.battery != null)
      document.getElementById('in-temp-bat').textContent = `Bat: ${inside.battery}%`;
    if (outside?.battery != null)
      document.getElementById('out-temp-bat').textContent = `Bat: ${outside.battery}%`;

  } catch (e) {
    setDot('dot-ble', 'error');
  }
}

function updateSensorCard(prefix, data, field) {
  const el = document.getElementById(prefix);
  el.textContent = data ? fmtVal(data[field]) : '--';
}

// ----------------------------------------------------------------
// Fan status
// ----------------------------------------------------------------
async function fetchFan() {
  try {
    const r = await fetch(`${API}/api/fans/status`);
    if (!r.ok) return;
    const d = await r.json();

    setGauge(d.speed);
    pushTrend('fan-speed', d.speed_percent);

    // Trend label next to gauge sub-text
    const trendH = trendHistory['fan-speed'];
    let trendLabel = '';
    if (trendH && trendH.length >= 2) {
      const delta = trendH[trendH.length - 1] - trendH[0];
      if (Math.abs(delta) >= 5) trendLabel = delta > 0 ? ' ▲' : ' ▼';
    }
    document.getElementById('gauge-sub').textContent =
      (d.manual_override ? 'Manuell' : 'Auto') + trendLabel;

    const autoBtn   = document.getElementById('btn-auto');
    const manualBtn = document.getElementById('btn-manual');
    if (d.manual_override) {
      autoBtn.style.background = '';
      manualBtn.style.background = 'var(--accent3)';
    } else {
      autoBtn.style.background = 'var(--accent3)';
      manualBtn.style.background = '';
    }
  } catch(e) {}
}

// ----------------------------------------------------------------
// Fan controls
// ----------------------------------------------------------------
async function setFanAuto() {
  await fetch(`${API}/api/fans/auto`, { method: 'POST' });
  document.getElementById('manual-slider-wrap').classList.add('hidden');
  manualOpen = false;
  showToast('Automatikbetrieb aktiv');
  fetchFan();
}

function openManual() {
  manualOpen = !manualOpen;
  const wrap = document.getElementById('manual-slider-wrap');
  if (manualOpen) {
    wrap.classList.remove('hidden');
  } else {
    wrap.classList.add('hidden');
  }
}

async function setManualSpeed(speed) {
  await fetch(`${API}/api/fans/manual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speed: parseFloat(speed) })
  });
  fetchFan();
}

// ----------------------------------------------------------------
// Control settings (targets, mode, fan limits)
// ----------------------------------------------------------------
async function loadControlSettings() {
  try {
    const r = await fetch(`${API}/api/settings`);
    const s = await r.json();

    document.getElementById('target-temp').value   = s.target_temperature  ?? 25;
    document.getElementById('target-hum').value    = s.target_humidity     ?? 65;
    document.getElementById('control-mode').value  = s.control_mode        ?? 'combined';
    document.getElementById('temp-range').value    = s.temp_control_range   ?? 5;
    document.getElementById('hum-range').value     = s.humidity_control_range ?? 20;
    document.getElementById('fan-min').value       = s.fan_min_speed        ?? 0.2;
    document.getElementById('fan-max').value       = s.fan_max_speed        ?? 1.0;
  } catch(e) {}
}

async function saveControlSettings() {
  const body = {
    target_temperature:      parseFloat(document.getElementById('target-temp').value),
    target_humidity:         parseFloat(document.getElementById('target-hum').value),
    control_mode:            document.getElementById('control-mode').value,
    temp_control_range:      parseFloat(document.getElementById('temp-range').value),
    humidity_control_range:  parseFloat(document.getElementById('hum-range').value),
    fan_min_speed:           parseFloat(document.getElementById('fan-min').value),
    fan_max_speed:           parseFloat(document.getElementById('fan-max').value),
  };
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  showToast('Einstellungen gespeichert');
}

// ----------------------------------------------------------------
// Charts
// ----------------------------------------------------------------
const CHART_DEFAULTS = {
  type: 'line',
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    plugins: { legend: { labels: { color: '#a5d6a7', boxWidth: 12 } } },
    scales: {
      x: {
        ticks: { color: '#6a8f6a', maxTicksLimit: 8, maxRotation: 0 },
        grid:  { color: '#1e2d1e' },
      },
      y: {
        ticks: { color: '#6a8f6a' },
        grid:  { color: '#1e2d1e' },
      }
    }
  }
};

function makeChart(canvasId, label1, label2, colour1, colour2) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  return new Chart(ctx, {
    ...CHART_DEFAULTS,
    data: {
      labels: [],
      datasets: [
        { label: label1, data: [], borderColor: colour1, backgroundColor: colour1 + '22',
          borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3 },
        { label: label2, data: [], borderColor: colour2, backgroundColor: colour2 + '22',
          borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3 },
      ]
    },
    options: { ...CHART_DEFAULTS.options, plugins: { ...CHART_DEFAULTS.options.plugins } }
  });
}

function initCharts() {
  charts.temp = makeChart('chart-temp', 'Innen (°C)', 'Außen (°C)', '#ef5350', '#42a5f5');
  charts.hum  = makeChart('chart-hum',  'Innen (%)',  'Außen (%)',  '#4caf50', '#81c784');

  // Fan chart (single dataset)
  const ctx3 = document.getElementById('chart-fan').getContext('2d');
  charts.fan = new Chart(ctx3, {
    ...CHART_DEFAULTS,
    data: {
      labels: [],
      datasets: [{
        label: 'Lüfter (%)', data: [],
        borderColor: '#ffb300', backgroundColor: '#ffb30022',
        borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3
      }]
    }
  });
}

function tsToLabel(ts) {
  const d = new Date(ts);
  return d.getHours().toString().padStart(2,'0') + ':' +
         d.getMinutes().toString().padStart(2,'0');
}

async function loadHistory() {
  const hours = parseInt(document.getElementById('chart-hours').value);
  try {
    const [sR, fR] = await Promise.all([
      fetch(`${API}/api/sensors/history?hours=${hours}`),
      fetch(`${API}/api/fans/history?hours=${hours}`)
    ]);
    const sData = await sR.json();
    const fData = await fR.json();

    const inside  = sData.inside  || [];
    const outside = sData.outside || [];

    const tLabels = inside.map(r => tsToLabel(r.timestamp));
    charts.temp.data.labels         = tLabels;
    charts.temp.data.datasets[0].data = inside.map(r => r.temperature);
    charts.temp.data.datasets[1].data = (() => {
      // Align outside to inside timestamps (simple approach)
      const oMap = {};
      outside.forEach(r => { oMap[tsToLabel(r.timestamp)] = r.temperature; });
      return tLabels.map(l => oMap[l] ?? null);
    })();
    charts.temp.update();

    charts.hum.data.labels          = tLabels;
    charts.hum.data.datasets[0].data  = inside.map(r => r.humidity);
    charts.hum.data.datasets[1].data  = (() => {
      const oMap = {};
      outside.forEach(r => { oMap[tsToLabel(r.timestamp)] = r.humidity; });
      return tLabels.map(l => oMap[l] ?? null);
    })();
    charts.hum.update();

    const events = fData.events || [];
    charts.fan.data.labels            = events.map(e => tsToLabel(e.timestamp));
    charts.fan.data.datasets[0].data  = events.map(e => Math.round(e.speed * 100));
    charts.fan.update();

  } catch(e) {
    console.error('History load error:', e);
  }
}

// ----------------------------------------------------------------
// Timelapse
// ----------------------------------------------------------------
async function fetchTimelapse() {
  try {
    const r = await fetch(`${API}/api/timelapse/status`);
    const d = await r.json();

    const dot  = document.getElementById('tl-status-dot');
    const text = document.getElementById('tl-status-text');
    const frames = document.getElementById('tl-frame-count');

    if (d.active) {
      dot.className  = 'status-dot ok';
      text.textContent = `Aufnahme: ${d.session || ''}`;
      frames.textContent = `${d.frame_count} Bilder`;
      document.getElementById('btn-tl-start').disabled = true;
      document.getElementById('btn-tl-stop').disabled  = false;
    } else {
      dot.className  = 'status-dot';
      text.textContent = 'Inaktiv';
      frames.textContent = '';
      document.getElementById('btn-tl-start').disabled = false;
      document.getElementById('btn-tl-stop').disabled  = true;
    }

    // Update timelapse form from settings
    document.getElementById('tl-interval').value = d.interval ?? 300;
    document.getElementById('tl-fps').value      = d.fps      ?? 25;
    document.getElementById('tl-cam-idx').value  = d.camera_index ?? 0;

    // Camera dot
    setDot('dot-cam', d.camera_available ? 'ok' : 'warn');
  } catch(e) {}
}

async function loadSessions() {
  try {
    const r = await fetch(`${API}/api/timelapse/sessions`);
    const d = await r.json();
    renderSessions(d.sessions || []);
  } catch(e) {}
}

function renderSessions(sessions) {
  const list = document.getElementById('session-list');
  if (!sessions.length) {
    list.innerHTML = '<div style="color:var(--text3);font-size:.75rem">Keine Aufnahmen</div>';
    return;
  }
  list.innerHTML = sessions.map(s => `
    <div class="session-item">
      <span class="session-name">${s.name}${s.active ? ' ●' : ''}</span>
      <span class="session-info">${s.frame_count} Bilder</span>
      <div class="session-actions">
        ${s.has_video
          ? `<a href="${s.video_url}" download class="btn-small">&#11123;</a>`
          : `<button class="btn-small" onclick="compileSession('${s.name}')">Kompil.</button>`}
        ${!s.active
          ? `<button class="btn-small" style="color:var(--danger)"
               onclick="deleteSession('${s.name}')">&#10005;</button>`
          : ''}
      </div>
    </div>`).join('');
}

async function startTimelapse() {
  const interval = parseInt(document.getElementById('tl-interval').value);
  const fps      = parseInt(document.getElementById('tl-fps').value);
  const camIdx   = parseInt(document.getElementById('tl-cam-idx').value);

  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timelapse_interval: interval, timelapse_fps: fps, camera_index: camIdx })
  });

  const r = await fetch(`${API}/api/timelapse/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  });
  if (r.ok) {
    showToast('Timelapse gestartet');
    fetchTimelapse();
    loadSessions();
  } else {
    const e = await r.json();
    showToast('Fehler: ' + (e.detail || r.status));
  }
}

async function stopTimelapse() {
  await fetch(`${API}/api/timelapse/stop`, { method: 'POST' });
  showToast('Timelapse gestoppt');
  fetchTimelapse();
  loadSessions();
}

async function compileSession(session) {
  showToast('Kompilierung gestartet…', 4000);
  const r = await fetch(`${API}/api/timelapse/compile/${session}`, { method: 'POST' });
  if (r.ok) {
    pollCompileStatus(session);
  } else {
    showToast('Kompilierung fehlgeschlagen');
  }
}

async function pollCompileStatus(session) {
  const r = await fetch(`${API}/api/timelapse/compile/${session}/status`);
  const d = await r.json();
  if (d.status === 'running') {
    setTimeout(() => pollCompileStatus(session), 3000);
  } else if (d.status === 'done') {
    showToast('Video fertig!');
    loadSessions();
  } else {
    showToast('Kompilierung fehlgeschlagen');
  }
}

async function deleteSession(session) {
  if (!confirm(`Aufnahme "${session}" löschen?`)) return;
  const r = await fetch(`${API}/api/timelapse/sessions/${session}`, { method: 'DELETE' });
  if (r.ok) {
    showToast('Gelöscht');
    loadSessions();
  }
}

// ----------------------------------------------------------------
// Camera preview
// ----------------------------------------------------------------
async function refreshPreview() {
  const img = document.getElementById('camera-preview');
  const ph  = document.getElementById('preview-placeholder');
  try {
    const r = await fetch(`${API}/api/timelapse/preview?t=${Date.now()}`);
    if (!r.ok) throw new Error();
    const blob = await r.blob();
    img.src = URL.createObjectURL(blob);
    img.classList.add('loaded');
    ph.style.display = 'none';
    setDot('dot-cam', 'ok');
  } catch(e) {
    img.classList.remove('loaded');
    ph.style.display = '';
    setDot('dot-cam', 'error');
  }
}

// ----------------------------------------------------------------
// Settings modal
// ----------------------------------------------------------------
function openSettings() {
  loadSettingsModal();
  document.getElementById('settings-overlay').classList.remove('hidden');
}

function closeSettings(evt) {
  if (evt && evt.target !== document.getElementById('settings-overlay')) return;
  document.getElementById('settings-overlay').classList.add('hidden');
}

async function loadSettingsModal() {
  try {
    const r = await fetch(`${API}/api/settings`);
    const s = await r.json();
    document.getElementById('inside-mac').value  = s.inside_sensor_mac  || '';
    document.getElementById('outside-mac').value = s.outside_sensor_mac || '';
    document.getElementById('fan-gpio').value    = s.fan_gpio_pin        ?? 18;
    document.getElementById('ble-interval').value = s.ble_scan_interval  ?? 30;
    document.getElementById('ble-duration').value = s.ble_scan_duration  ?? 10;
  } catch(e) {}
}

async function saveSettings() {
  const body = {
    inside_sensor_mac:  document.getElementById('inside-mac').value.trim().toUpperCase(),
    outside_sensor_mac: document.getElementById('outside-mac').value.trim().toUpperCase(),
    fan_gpio_pin:        parseInt(document.getElementById('fan-gpio').value),
    ble_scan_interval:   parseInt(document.getElementById('ble-interval').value),
    ble_scan_duration:   parseInt(document.getElementById('ble-duration').value),
  };
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  const msg = document.getElementById('settings-saved');
  msg.classList.remove('hidden');
  setTimeout(() => msg.classList.add('hidden'), 2500);
  showToast('Einstellungen gespeichert');
}

async function discoverSensors() {
  const btn = document.querySelector('button[onclick="discoverSensors()"]');
  btn.textContent = '🔍 Suche läuft (10s)…';
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/api/sensors/discover`, { method: 'POST' });
    const d = await r.json();
    const devices = d.devices || [];
    const cont = document.getElementById('discover-result');

    if (!devices.length) {
      cont.innerHTML = '<div style="color:var(--text3)">Keine SwitchBot-Geräte gefunden</div>';
    } else {
      cont.innerHTML = devices.map(dev => `
        <div class="discover-item">
          <span class="mac-addr">${dev.mac}</span>
          <span style="color:var(--text3)">${dev.rssi} dBm</span>
          <button class="btn-small" onclick="setMac('inside','${dev.mac}')">Innen</button>
          <button class="btn-small" onclick="setMac('outside','${dev.mac}')">Außen</button>
        </div>`).join('');
    }
  } catch(e) {
    document.getElementById('discover-result').textContent = 'Fehler beim Scannen';
  }

  btn.textContent = '📡 Sensoren suchen (10s)';
  btn.disabled = false;
}

function setMac(role, mac) {
  document.getElementById(role === 'inside' ? 'inside-mac' : 'outside-mac').value = mac;
  showToast(`${role === 'inside' ? 'Innen' : 'Außen'}-Sensor: ${mac}`);
}

// ----------------------------------------------------------------
// Polling loop
// ----------------------------------------------------------------
async function pollAll() {
  await Promise.allSettled([
    fetchSensors(),
    fetchFan(),
    fetchTimelapse(),
  ]);
}

async function init() {
  initGauge();
  initCharts();
  await loadControlSettings();
  await loadSessions();
  await pollAll();
  await loadHistory();
  refreshPreview();

  // Poll every 10 seconds
  pollTimer = setInterval(pollAll, 10_000);
  // Reload history every 2 minutes
  setInterval(loadHistory, 120_000);
  // Reload sessions every 30 seconds
  setInterval(loadSessions, 30_000);
}

document.addEventListener('DOMContentLoaded', init);
