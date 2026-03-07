/* ============================================================
   Greenhouse Control Dashboard
============================================================ */

const API = '';          // same origin
let charts = {};
let pollTimer = null;
let manualOpen = false;

// ----------------------------------------------------------------
// Tooltip system
// ----------------------------------------------------------------
(function () {
  const tip = document.createElement('div');
  tip.className = 'tooltip hidden';
  document.body.appendChild(tip);

  let _timer = null;
  let _lastE = null;

  function getDelay() {
    return parseInt(localStorage.getItem('tooltip_delay_ms') ?? '600');
  }

  function show(e) {
    const el = e.target.closest('[data-tooltip]');
    clearTimeout(_timer);
    if (!el) { tip.classList.add('hidden'); return; }
    _lastE = e;
    _timer = setTimeout(() => {
      tip.textContent = el.dataset.tooltip;
      tip.classList.remove('hidden');
      if (_lastE) move(_lastE);
    }, getDelay());
  }

  function move(e) {
    _lastE = e;
    if (tip.classList.contains('hidden')) return;
    const x = e.clientX + 14;
    const y = e.clientY - tip.offsetHeight - 8;
    tip.style.left = Math.min(x, window.innerWidth  - tip.offsetWidth  - 10) + 'px';
    tip.style.top  = Math.max(y, 8) + 'px';
  }

  function hide(e) {
    clearTimeout(_timer);
    const el = e.target.closest('[data-tooltip]');
    if (el && el.contains(e.relatedTarget)) return;
    tip.classList.add('hidden');
  }

  document.addEventListener('mouseover',  show);
  document.addEventListener('mousemove',  move);
  document.addEventListener('mouseout',   hide);
})();

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
// Format helpers (German locale: comma as decimal separator)
// ----------------------------------------------------------------
function parseDE(val) {
  return parseFloat(String(val).replace(',', '.'));
}

function formatDE(num, decimals = 1) {
  if (num == null || isNaN(num)) return '';
  return Number(num).toFixed(decimals).replace('.', ',');
}

function fmtVal(v, decimals = 1) {
  return v == null ? '--' : Number(v).toFixed(decimals).replace('.', ',');
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

    document.getElementById('target-temp').value   = formatDE(s.target_temperature  ?? 25, 1);
    document.getElementById('target-hum').value    = s.target_humidity     ?? 65;
    const mode = s.control_mode === 'combined' ? 'combined_or' : (s.control_mode ?? 'combined_or');
    document.getElementById('control-mode').value  = mode;
    document.getElementById('temp-range').value    = formatDE(s.temp_control_range   ?? 5,  1);
    document.getElementById('hum-range').value     = s.humidity_control_range ?? 20;
    document.getElementById('fan-min').value       = formatDE(s.fan_min_speed        ?? 0.2, 2);
    document.getElementById('fan-max').value       = formatDE(s.fan_max_speed        ?? 1.0, 2);
  } catch(e) {}
}

async function saveControlSettings() {
  const body = {
    target_temperature:      parseDE(document.getElementById('target-temp').value),
    target_humidity:         parseFloat(document.getElementById('target-hum').value),
    control_mode:            document.getElementById('control-mode').value,
    temp_control_range:      parseDE(document.getElementById('temp-range').value),
    humidity_control_range:  parseFloat(document.getElementById('hum-range').value),
    fan_min_speed:           parseDE(document.getElementById('fan-min').value),
    fan_max_speed:           parseDE(document.getElementById('fan-max').value),
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
    plugins: {
      legend: { labels: { color: '#8b949e', boxWidth: 12 } },
      tooltip: {
        callbacks: {
          label: ctx => {
            const v = ctx.parsed.y;
            return ctx.dataset.label + ': ' + (v == null ? '--' : String(v % 1 === 0 ? v : v.toFixed(1)).replace('.', ','));
          }
        }
      }
    },
    scales: {
      x: {
        ticks: { color: '#8b949e', maxTicksLimit: 8, maxRotation: 0 },
        grid:  { color: '#21262d' },
      },
      y: {
        ticks: {
          color: '#8b949e',
          callback: val => String(val % 1 === 0 ? val : val.toFixed(1)).replace('.', ','),
        },
        grid:  { color: '#21262d' },
        grace: '5%',
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
    options: { ...CHART_DEFAULTS.options }
  });
}

function initCharts() {
  charts.temp = makeChart('chart-temp', 'Innen (°C)', 'Außen (°C)', '#ef5350', '#58a6ff');
  charts.hum  = makeChart('chart-hum',  'Innen (%)',  'Außen (%)',  '#3fb950', '#58a6ff');

  const ctx3 = document.getElementById('chart-fan').getContext('2d');
  charts.fan = new Chart(ctx3, {
    ...CHART_DEFAULTS,
    data: {
      labels: [],
      datasets: [
        { label: 'Lüfter (%)', data: [],
          borderColor: '#e3b341', backgroundColor: '#e3b34122',
          borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3 },
      ]
    },
    options: { ...CHART_DEFAULTS.options }
  });
}

function tsToLabel(ts) {
  const d = new Date(ts);
  return d.getHours().toString().padStart(2,'0') + ':' +
         d.getMinutes().toString().padStart(2,'0');
}

async function loadHistory() {
  const tempHours = parseInt(document.getElementById('chart-hours-temp').value);
  const humHours  = parseInt(document.getElementById('chart-hours-hum').value);
  const fanHours  = parseInt(document.getElementById('chart-hours-fan').value);

  try {
    const [tempSensor, humSensor, fanData] = await Promise.all([
      fetch(`${API}/api/sensors/history?hours=${tempHours}`).then(r => r.json()),
      fetch(`${API}/api/sensors/history?hours=${humHours}`).then(r => r.json()),
      fetch(`${API}/api/fans/history?hours=${fanHours}`).then(r => r.json()),
    ]);

    // Temperature chart
    const tInside  = tempSensor.inside  || [];
    const tOutside = tempSensor.outside || [];
    const tLabels  = tInside.map(r => tsToLabel(r.timestamp));
    charts.temp.data.labels           = tLabels;
    charts.temp.data.datasets[0].data = tInside.map(r => r.temperature);
    charts.temp.data.datasets[1].data = (() => {
      const oMap = {};
      tOutside.forEach(r => { oMap[tsToLabel(r.timestamp)] = r.temperature; });
      return tLabels.map(l => oMap[l] ?? null);
    })();
    charts.temp.update();

    // Humidity chart
    const hInside  = humSensor.inside  || [];
    const hOutside = humSensor.outside || [];
    const hLabels  = hInside.map(r => tsToLabel(r.timestamp));
    charts.hum.data.labels           = hLabels;
    charts.hum.data.datasets[0].data = hInside.map(r => r.humidity);
    charts.hum.data.datasets[1].data = (() => {
      const oMap = {};
      hOutside.forEach(r => { oMap[tsToLabel(r.timestamp)] = r.humidity; });
      return hLabels.map(l => oMap[l] ?? null);
    })();
    charts.hum.update();

    // Fan speed chart
    const events = fanData.events || [];
    charts.fan.data.labels           = events.map(e => tsToLabel(e.timestamp));
    charts.fan.data.datasets[0].data = events.map(e => Math.round(e.speed * 100));
    charts.fan.update();

  } catch(e) {
    console.error('History load error:', e);
  }
}

// ----------------------------------------------------------------
// Timelapse
// ----------------------------------------------------------------
async function loadCameras() {
  const select = document.getElementById('tl-cam-idx');
  const prev   = select.value;
  select.innerHTML = '<option value="">Suche…</option>';
  select.disabled  = true;

  try {
    const r = await fetch(`${API}/api/timelapse/cameras`);
    const d = await r.json();
    const cameras = d.cameras || [];

    if (!cameras.length) {
      select.innerHTML = '<option value="0">Keine Kamera gefunden</option>';
    } else {
      select.innerHTML = cameras
        .map(c => `<option value="${c.index}">${c.name} (Index ${c.index})</option>`)
        .join('');
      if (cameras.some(c => String(c.index) === prev)) select.value = prev;
    }
  } catch(e) {
    select.innerHTML = '<option value="0">Kamera 0</option>';
  }
  select.disabled = false;
  await loadResolutions(select.value);
}

function updateCaptureModeUI() {
  const mode = document.getElementById('tl-capture-mode').value;
  document.getElementById('clip-options').classList.toggle('hidden', mode !== 'clip');
}

async function loadResolutions(camIdx) {
  const sel = document.getElementById('tl-resolution');
  sel.innerHTML = '<option value="0x0">Kamera Standard</option>';
  sel.disabled = true;
  try {
    const r = await fetch(`${API}/api/timelapse/resolutions?camera=${camIdx}`);
    const d = await r.json();
    (d.resolutions || []).forEach(res => {
      const opt = document.createElement('option');
      opt.value = `${res.width}x${res.height}`;
      opt.textContent = res.label;
      sel.appendChild(opt);
    });
    // Restore saved resolution
    const sr = await fetch(`${API}/api/settings`);
    const s  = await sr.json();
    const saved = `${s.camera_capture_width ?? 0}x${s.camera_capture_height ?? 0}`;
    if ([...sel.options].some(o => o.value === saved)) sel.value = saved;
  } catch(e) {}
  sel.disabled = false;
  await loadFps(camIdx, sel.value);
}

async function loadFps(camIdx, resolution) {
  const sel   = document.getElementById('tl-clip-fps');
  const saved = sel.value;
  sel.innerHTML = '';
  sel.disabled  = true;
  const [w, h] = (resolution || '0x0').split('x').map(Number);
  const fallback = [5, 10, 15, 20, 25, 30];
  try {
    const r = await fetch(`${API}/api/timelapse/fps?camera=${camIdx}&width=${w}&height=${h}`);
    const d = await r.json();
    const list = d.fps && d.fps.length ? d.fps : fallback;
    list.forEach(fps => {
      const opt = document.createElement('option');
      opt.value = fps;
      opt.textContent = `${fps} fps`;
      sel.appendChild(opt);
    });
  } catch(e) {
    fallback.forEach(fps => {
      const opt = document.createElement('option');
      opt.value = fps;
      opt.textContent = `${fps} fps`;
      sel.appendChild(opt);
    });
  }
  if ([...sel.options].some(o => o.value === saved)) sel.value = saved;
  sel.disabled = false;
}

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

    // Update timelapse form from settings (interval stored in seconds, displayed in hours)
    document.getElementById('tl-interval').value = formatDE((d.interval ?? 3600) / 3600, 2);
    document.getElementById('tl-capture-mode').value = d.capture_mode ?? 'still';
    document.getElementById('tl-clip-duration').value = d.clip_duration ?? 5;
    document.getElementById('tl-clip-fps').value = d.clip_fps ?? 10;
    updateCaptureModeUI();
    // Sync dropdown selection without triggering a re-scan
    const camSel = document.getElementById('tl-cam-idx');
    camSel.value = String(d.camera_index ?? 0);

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
      <span class="session-info">${s.frame_count} ${s.capture_mode === 'clip' ? 'Clips' : 'Bilder'}</span>
      <div class="session-actions">
        ${!s.active
          ? `<button class="btn-small" style="color:var(--danger)"
               onclick="deleteSession('${s.name}')">&#10005;</button>`
          : ''}
      </div>
    </div>`).join('');
}

async function startTimelapse() {
  const intervalHours = parseDE(document.getElementById('tl-interval').value);
  const intervalSecs  = Math.round(intervalHours * 3600);
  const camIdx        = parseInt(document.getElementById('tl-cam-idx').value);
  const [capW, capH]  = document.getElementById('tl-resolution').value.split('x').map(Number);
  const captureMode   = document.getElementById('tl-capture-mode').value;
  const clipDuration  = parseInt(document.getElementById('tl-clip-duration').value);
  const clipFps       = parseInt(document.getElementById('tl-clip-fps').value);

  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      timelapse_interval: intervalSecs,
      camera_index: camIdx,
      camera_capture_width: capW,
      camera_capture_height: capH,
      capture_mode: captureMode,
      clip_duration: clipDuration,
      clip_fps: clipFps,
    })
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
// ----------------------------------------------------------------
// Update
// ----------------------------------------------------------------
let _updateAvailable = false;
let _updateTimer = null;

function scheduleUpdateCheck(days) {
  if (_updateTimer) clearInterval(_updateTimer);
  if (days > 0) {
    _updateTimer = setInterval(checkUpdate, days * 86_400_000);
  }
}

async function checkUpdate() {
  document.getElementById('upd-status-text').textContent = 'Prüfe…';
  document.getElementById('upd-current').textContent = '…';
  document.getElementById('upd-latest').textContent  = '…';
  document.getElementById('btn-do-update').disabled   = true;

  try {
    const r = await fetch(`${API}/api/update/check`);
    const d = await r.json();

    if (d.error) {
      document.getElementById('upd-status-text').textContent = `Fehler: ${d.error}`;
      return;
    }

    document.getElementById('upd-current').textContent = d.current ?? '--';
    document.getElementById('upd-latest').textContent  = d.latest  ?? '--';

    if (d.update_available) {
      document.getElementById('upd-status-text').textContent = 'Update verfügbar';
      document.getElementById('btn-do-update').disabled = false;
      _updateAvailable = true;
      document.getElementById('update-badge').classList.remove('hidden');
    } else if (d.up_to_date) {
      document.getElementById('upd-status-text').textContent = 'Aktuell – kein Update nötig';
      _updateAvailable = false;
      document.getElementById('update-badge').classList.add('hidden');
    } else {
      document.getElementById('upd-status-text').textContent = 'Unbekannt';
    }
  } catch(e) {
    document.getElementById('upd-status-text').textContent = 'Verbindungsfehler';
  }
}

async function applyUpdate() {
  if (!confirm('Update jetzt installieren?\nDie Anwendung wird danach automatisch neu gestartet.')) return;

  document.getElementById('btn-do-update').disabled = true;
  document.getElementById('upd-spinner').classList.remove('hidden');
  document.getElementById('upd-log-wrap').classList.remove('hidden');
  document.getElementById('upd-log').textContent = 'Starte Update…\n';
  document.getElementById('upd-status-text').textContent = 'Installiere…';

  await fetch(`${API}/api/update/apply`, { method: 'POST' });
  pollUpdateStatus();
}

async function pollUpdateStatus() {
  try {
    const r = await fetch(`${API}/api/update/status`);
    const d = await r.json();

    document.getElementById('upd-log').textContent = d.log || '';
    document.getElementById('upd-log').scrollTop = 9999;

    if (d.status === 'running') {
      setTimeout(pollUpdateStatus, 1500);
    } else if (d.status === 'done') {
      document.getElementById('upd-status-text').textContent = 'Update abgeschlossen – Seite wird neu geladen…';
      document.getElementById('upd-spinner').classList.add('hidden');
      document.getElementById('update-badge').classList.add('hidden');
      setTimeout(() => location.reload(), 4000);
    } else {
      document.getElementById('upd-status-text').textContent = 'Fehler beim Update';
      document.getElementById('upd-spinner').classList.add('hidden');
    }
  } catch(e) {
    // Server restarted – reload page
    document.getElementById('upd-status-text').textContent = 'Server startet neu…';
    setTimeout(() => location.reload(), 5000);
  }
}

function openUpdate() {
  document.getElementById('update-overlay').classList.remove('hidden');
  checkUpdate();
}

function closeUpdate(evt) {
  if (evt && evt.target !== document.getElementById('update-overlay')) return;
  document.getElementById('update-overlay').classList.add('hidden');
}

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
    document.getElementById('ble-interval').value       = s.ble_scan_interval         ?? 30;
    document.getElementById('ble-duration').value       = s.ble_scan_duration         ?? 10;
    document.getElementById('update-interval-days').value = s.update_check_interval_days ?? 7;
    document.getElementById('timelapse-path').value  = s.timelapse_path ?? 'timelapse';
    const shareOn = s.timelapse_share_enabled ?? false;
    document.getElementById('timelapse-share').checked = shareOn;
    updateShareUrl(shareOn);
    document.getElementById('tooltip-delay').value = localStorage.getItem('tooltip_delay_ms') ?? '600';
  } catch(e) {}
}

// ----------------------------------------------------------------
// Folder Picker
// ----------------------------------------------------------------
let _fpCurrentPath = '';

async function openFolderPicker() {
  const current = document.getElementById('timelapse-path').value.trim() || 'timelapse';
  document.getElementById('folder-picker-overlay').classList.remove('hidden');
  await fpBrowse(current);
}

function closeFolderPicker(event) {
  if (event && event.target !== document.getElementById('folder-picker-overlay')) return;
  document.getElementById('folder-picker-overlay').classList.add('hidden');
  document.getElementById('fp-new-name').value = '';
}

async function fpBrowse(path) {
  try {
    const r = await fetch(`${API}/api/fs/browse?path=${encodeURIComponent(path)}`);
    if (!r.ok) { showToast('Zugriff verweigert'); return; }
    const d = await r.json();
    _fpCurrentPath = d.path;

    document.getElementById('fp-path').textContent = d.path;

    const list = document.getElementById('fp-list');
    let html = '';
    if (d.parent) {
      html += `<div class="fp-item fp-up" onclick="fpBrowse('${esc(d.parent)}')">
        <span class="fp-icon">&#8593;</span> Übergeordneter Ordner
      </div>`;
    }
    if (d.dirs.length === 0 && !d.parent) {
      html += '<div class="fp-empty">Keine Unterordner</div>';
    }
    d.dirs.forEach(dir => {
      html += `<div class="fp-item" onclick="fpBrowse('${esc(dir.path)}')">
        <span class="fp-icon">&#128193;</span>${dir.name}
      </div>`;
    });
    if (d.dirs.length === 0 && d.parent) {
      html += '<div class="fp-empty">Keine Unterordner vorhanden</div>';
    }
    list.innerHTML = html;
  } catch(e) {
    showToast('Fehler beim Laden des Verzeichnisses');
  }
}

async function fpMkdir() {
  const name = document.getElementById('fp-new-name').value.trim();
  if (!name) return;
  const newPath = _fpCurrentPath.replace(/\\/g, '/').replace(/\/$/, '') + '/' + name;
  try {
    const r = await fetch(`${API}/api/fs/mkdir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: newPath }),
    });
    if (!r.ok) { showToast('Ordner konnte nicht erstellt werden'); return; }
    document.getElementById('fp-new-name').value = '';
    await fpBrowse(newPath);
  } catch(e) {
    showToast('Fehler beim Erstellen');
  }
}

function fpSelect() {
  document.getElementById('timelapse-path').value = _fpCurrentPath;
  document.getElementById('folder-picker-overlay').classList.add('hidden');
  document.getElementById('fp-new-name').value = '';
}

function esc(str) {
  return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function updateShareUrl(enabled) {
  const wrap = document.getElementById('share-url-wrap');
  if (enabled) {
    wrap.classList.remove('hidden');
    const url = window.location.origin + '/api/timelapse/browse';
    const a   = document.getElementById('share-url');
    a.href = url;
    a.textContent = url;
  } else {
    wrap.classList.add('hidden');
  }
}

async function saveSettings() {
  const body = {
    inside_sensor_mac:  document.getElementById('inside-mac').value.trim().toUpperCase(),
    outside_sensor_mac: document.getElementById('outside-mac').value.trim().toUpperCase(),
    fan_gpio_pin:               parseInt(document.getElementById('fan-gpio').value),
    ble_scan_interval:          parseInt(document.getElementById('ble-interval').value),
    ble_scan_duration:          parseInt(document.getElementById('ble-duration').value),
    update_check_interval_days: Math.max(0, parseInt(document.getElementById('update-interval-days').value) || 0),
    timelapse_path:         document.getElementById('timelapse-path').value.trim() || 'timelapse',
    timelapse_share_enabled: document.getElementById('timelapse-share').checked,
  };
  // Tooltip delay is client-side only
  localStorage.setItem('tooltip_delay_ms', document.getElementById('tooltip-delay').value || '600');
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  scheduleUpdateCheck(body.update_check_interval_days);
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
  await loadCameras();
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
  // Schedule automatic update check based on saved interval
  try {
    const r = await fetch(`${API}/api/settings`);
    const s = await r.json();
    scheduleUpdateCheck(s.update_check_interval_days ?? 7);
  } catch(e) {}
}

document.addEventListener('DOMContentLoaded', init);
