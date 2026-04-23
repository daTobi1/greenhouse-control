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
    // Regulation enabled/disabled
    const regEnabled = d.regulation_enabled !== false;
    const fanCard = document.querySelector('.fan-card');
    const regToggle = document.getElementById('regulation-toggle');
    if (regToggle && regToggle !== document.activeElement) {
      regToggle.checked = regEnabled;
    }
    if (fanCard) {
      fanCard.classList.toggle('regulation-off', !regEnabled);
    }

    if (!regEnabled) {
      document.getElementById('gauge-sub').textContent = 'Aus';
    } else {
      document.getElementById('gauge-sub').textContent =
        (d.manual_override ? 'Manuell' : 'Auto') + trendLabel;
    }

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
// Regulation toggle (central on/off)
// ----------------------------------------------------------------
async function toggleRegulation(enabled) {
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ regulation_enabled: enabled })
  });
  showToast(enabled ? 'Regelung eingeschaltet' : 'Regelung ausgeschaltet');
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
    document.getElementById('fan-min').value       = formatDE((s.fan_min_speed ?? 0.2) * 100, 0);
    document.getElementById('fan-max').value       = formatDE((s.fan_max_speed ?? 1.0) * 100, 0);
    document.getElementById('fan-deadband').value  = formatDE((s.fan_deadband ?? 0.1) * 100, 0);
    document.getElementById('fan-min-temp').value  = formatDE(s.fan_min_temperature ?? 5, 1);
  } catch(e) {}
}

async function saveControlSettings() {
  const body = {
    target_temperature:      parseDE(document.getElementById('target-temp').value),
    target_humidity:         parseFloat(document.getElementById('target-hum').value),
    control_mode:            document.getElementById('control-mode').value,
    temp_control_range:      parseDE(document.getElementById('temp-range').value),
    humidity_control_range:  parseFloat(document.getElementById('hum-range').value),
    fan_min_speed:           parseDE(document.getElementById('fan-min').value) / 100,
    fan_max_speed:           parseDE(document.getElementById('fan-max').value) / 100,
    fan_deadband:            parseDE(document.getElementById('fan-deadband').value) / 100,
    fan_min_temperature:     parseDE(document.getElementById('fan-min-temp').value),
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
      legend: { labels: { color: '#8b949e', boxWidth: 10, font: { family: 'DM Sans', size: 11 } } },
      tooltip: {
        backgroundColor: '#161b22',
        borderColor: '#30363d',
        borderWidth: 1,
        titleFont: { family: 'DM Sans' },
        bodyFont: { family: 'JetBrains Mono', size: 11 },
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
        ticks: { color: '#8b949e', maxTicksLimit: 8, maxRotation: 0, font: { family: 'JetBrains Mono', size: 10 } },
        grid:  { color: '#21262d' },
      },
      y: {
        ticks: {
          color: '#8b949e',
          font: { family: 'JetBrains Mono', size: 10 },
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
  charts.temp = makeChart('chart-temp', 'Innen (°C)', 'Außen (°C)', '#f0883e', '#58a6ff');
  charts.hum  = makeChart('chart-hum',  'Innen (%)',  'Außen (%)',  '#f0883e', '#58a6ff');

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

let _historyAbort = null;

async function loadHistory() {
  const tempHours = parseInt(document.getElementById('chart-hours-temp').value);
  const humHours  = parseInt(document.getElementById('chart-hours-hum').value);
  const fanHours  = parseInt(document.getElementById('chart-hours-fan').value);

  // Cancel previous in-flight history request
  if (_historyAbort) _historyAbort.abort();
  _historyAbort = new AbortController();
  const signal = _historyAbort.signal;

  try {
    const [tempSensor, humSensor, fanData] = await Promise.all([
      fetch(`${API}/api/sensors/history?hours=${tempHours}`, { signal }).then(r => r.json()),
      fetch(`${API}/api/sensors/history?hours=${humHours}`, { signal }).then(r => r.json()),
      fetch(`${API}/api/fans/history?hours=${fanHours}`, { signal }).then(r => r.json()),
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
    if (e.name === 'AbortError') return;
    console.error('History load error:', e);
  }
}

// ----------------------------------------------------------------
// Timelapse – Multi-Camera
// ----------------------------------------------------------------
let _cameraCount = 1;
let _availableCameras = [];  // shared list from hardware scan

function buildCameraSection(ci) {
  const label = _cameraCount > 1 ? ` ${ci}` : '';
  return `
  <section class="timelapse-grid" id="tl-section-${ci}">
    <div class="card timelapse-card">
      <div class="card-label">Timelapse${label}</div>

      <div class="tl-status">
        <span id="tl-status-dot-${ci}" class="status-dot"></span>
        <span id="tl-status-text-${ci}">Inaktiv</span>
        <span id="tl-frame-count-${ci}" class="tl-frames"></span>
      </div>

      <div class="control-row" data-tooltip="Standbild: ein JPEG pro Intervall. Kurzer Clip: ein kurzes MP4-Video pro Intervall.">
        <label>Aufnahmemodus</label>
        <select id="tl-capture-mode-${ci}" onchange="updateCaptureModeUI(${ci})">
          <option value="still">Standbild (JPEG)</option>
          <option value="clip">Kurzer Clip (MP4)</option>
        </select>
      </div>
      <div id="clip-options-${ci}" class="hidden">
        <div class="control-row" data-tooltip="Länge des aufgezeichneten Clips in Sekunden.">
          <label>Clip-Dauer</label>
          <div class="input-group">
            <input type="number" id="tl-clip-duration-${ci}" min="1" max="60" step="1" value="5" />
            <span class="unit">s</span>
          </div>
        </div>
      </div>
      <div class="control-row" data-tooltip="Zeitabstand zwischen zwei Timelapse-Aufnahmen in Stunden.">
        <label>Intervall (Stunden)</label>
        <input type="text" inputmode="decimal" id="tl-interval-${ci}" placeholder="0,0014" />
      </div>
      <div class="control-row" data-tooltip="Hardware-Kamera auswählen.">
        <label>Kamera</label>
        <div class="cam-select-wrap">
          <select id="tl-cam-idx-${ci}" onchange="loadResolutions(${ci}, this.value)">
            <option value="${ci}">Kamera ${ci}</option>
          </select>
          <button class="btn-small" onclick="loadCamerasForSlot(${ci})" title="Kameras suchen">&#8635;</button>
        </div>
      </div>
      <div class="control-row" data-tooltip="Aufnahmeauflösung.">
        <label>Auflösung</label>
        <select id="tl-resolution-${ci}" onchange="loadFps(${ci}, document.getElementById('tl-cam-idx-${ci}').value, this.value)">
          <option value="0x0">Kamera Standard</option>
        </select>
      </div>
      <div class="control-row" data-tooltip="Bildrate der Kameraaufnahme.">
        <label>Aufnahme-FPS</label>
        <select id="tl-clip-fps-${ci}">
          <option value="10">10 fps</option>
        </select>
      </div>

      <div class="btn-row">
        <button class="btn"            id="btn-tl-start-${ci}" onclick="startTimelapse(${ci})">Starten</button>
        <button class="btn btn-danger" id="btn-tl-stop-${ci}"  onclick="stopTimelapse(${ci})" disabled>Stoppen</button>
      </div>

      <div class="card-label" style="margin-top:1rem">Aufnahmen</div>
      <div id="session-list-${ci}" class="session-list"></div>
    </div>

    <div class="card camera-card">
      <div class="card-label">Kamera-Vorschau${label}
        <button class="btn-small" onclick="refreshPreview(${ci})">&#8635;</button>
      </div>
      <div class="preview-wrap">
        <img id="camera-preview-${ci}" src="" alt="Kein Bild" class="camera-img" />
        <div id="preview-placeholder-${ci}" class="preview-placeholder">Kamera nicht verfügbar</div>
      </div>
    </div>
  </section>`;
}

async function initCameraSections() {
  try {
    const r = await fetch(`${API}/api/settings`);
    const s = await r.json();
    _cameraCount = Math.max(1, Math.min(4, parseInt(s.camera_count) || 1));
  } catch(e) { _cameraCount = 1; }

  const container = document.getElementById('timelapse-container');
  container.innerHTML = '';
  for (let i = 0; i < _cameraCount; i++) {
    container.innerHTML += buildCameraSection(i);
  }
  // Scan cameras once, then populate all dropdowns
  await loadAllCameras();
  for (let i = 0; i < _cameraCount; i++) {
    loadResolutions(i, document.getElementById(`tl-cam-idx-${i}`).value);
  }
}

async function loadAllCameras() {
  try {
    const r = await fetch(`${API}/api/timelapse/cameras`);
    const d = await r.json();
    _availableCameras = d.cameras || [];
  } catch(e) { _availableCameras = []; }
  for (let i = 0; i < _cameraCount; i++) {
    populateCameraDropdown(i);
  }
}

function populateCameraDropdown(ci) {
  const select = document.getElementById(`tl-cam-idx-${ci}`);
  if (!select) return;
  const prev = select.value;
  if (!_availableCameras.length) {
    select.innerHTML = `<option value="${ci}">Kamera ${ci}</option>`;
  } else {
    select.innerHTML = _availableCameras
      .map(c => `<option value="${c.index}">${escHtml(c.name)} (Index ${c.index})</option>`)
      .join('');
    if (_availableCameras.some(c => String(c.index) === prev)) select.value = prev;
  }
}

async function loadCamerasForSlot(ci) {
  const select = document.getElementById(`tl-cam-idx-${ci}`);
  select.innerHTML = '<option value="">Suche…</option>';
  select.disabled = true;
  await loadAllCameras();
  select.disabled = false;
  await loadResolutions(ci, select.value);
}

function updateCaptureModeUI(ci) {
  const mode = document.getElementById(`tl-capture-mode-${ci}`).value;
  document.getElementById(`clip-options-${ci}`).classList.toggle('hidden', mode !== 'clip');
}

async function loadResolutions(ci, devIdx) {
  const sel = document.getElementById(`tl-resolution-${ci}`);
  sel.innerHTML = '<option value="0x0">Kamera Standard</option>';
  sel.disabled = true;
  try {
    const r = await fetch(`${API}/api/timelapse/resolutions?camera=${devIdx}`);
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
    const wKey = ci === 0 ? (s.cam_0_capture_width ?? s.camera_capture_width ?? 0) : (s[`cam_${ci}_capture_width`] ?? 0);
    const hKey = ci === 0 ? (s.cam_0_capture_height ?? s.camera_capture_height ?? 0) : (s[`cam_${ci}_capture_height`] ?? 0);
    const saved = `${wKey}x${hKey}`;
    if ([...sel.options].some(o => o.value === saved)) sel.value = saved;
  } catch(e) {}
  sel.disabled = false;
  await loadFps(ci, devIdx, sel.value);
}

async function loadFps(ci, devIdx, resolution) {
  const sel   = document.getElementById(`tl-clip-fps-${ci}`);
  const saved = sel.value;
  sel.innerHTML = '';
  sel.disabled  = true;
  const [w, h] = (resolution || '0x0').split('x').map(Number);
  const fallback = [5, 10, 15, 20, 25, 30];
  try {
    const r = await fetch(`${API}/api/timelapse/fps?camera=${devIdx}&width=${w}&height=${h}`);
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

async function fetchTimelapse(ci) {
  try {
    const r = await fetch(`${API}/api/timelapse/status?cam=${ci}`);
    const d = await r.json();

    const dot    = document.getElementById(`tl-status-dot-${ci}`);
    const text   = document.getElementById(`tl-status-text-${ci}`);
    const frames = document.getElementById(`tl-frame-count-${ci}`);

    if (d.active) {
      dot.className  = 'status-dot ok';
      text.textContent = `Aufnahme: ${d.session || ''}`;
      frames.textContent = `${d.frame_count} Bilder`;
      document.getElementById(`btn-tl-start-${ci}`).disabled = true;
      document.getElementById(`btn-tl-stop-${ci}`).disabled  = false;
    } else {
      dot.className  = 'status-dot';
      text.textContent = 'Inaktiv';
      frames.textContent = '';
      document.getElementById(`btn-tl-start-${ci}`).disabled = false;
      document.getElementById(`btn-tl-stop-${ci}`).disabled  = true;
    }

    const tlIntervalEl = document.getElementById(`tl-interval-${ci}`);
    const serverSecs   = d.interval ?? 3600;
    const uiSecs       = Math.round(parseDE(tlIntervalEl.value) * 3600);
    if (isNaN(uiSecs) || uiSecs === serverSecs) {
      tlIntervalEl.value = formatDE(serverSecs / 3600, 4);
    }
    document.getElementById(`tl-capture-mode-${ci}`).value = d.capture_mode ?? 'still';
    document.getElementById(`tl-clip-duration-${ci}`).value = d.clip_duration ?? 5;
    document.getElementById(`tl-clip-fps-${ci}`).value = d.clip_fps ?? 10;
    updateCaptureModeUI(ci);
    const camSel = document.getElementById(`tl-cam-idx-${ci}`);
    camSel.value = String(d.camera_index ?? ci);

    // Camera dot (use first camera status)
    if (ci === 0) setDot('dot-cam', d.camera_available ? 'ok' : 'warn');
  } catch(e) {}
}

async function fetchAllTimelapse() {
  const tasks = [];
  for (let i = 0; i < _cameraCount; i++) tasks.push(fetchTimelapse(i));
  await Promise.allSettled(tasks);
}

async function loadSessions(ci) {
  try {
    const r = await fetch(`${API}/api/timelapse/sessions?cam=${ci}`);
    const d = await r.json();
    renderSessions(ci, d.sessions || []);
  } catch(e) {}
}

async function loadAllSessions() {
  const tasks = [];
  for (let i = 0; i < _cameraCount; i++) tasks.push(loadSessions(i));
  await Promise.allSettled(tasks);
}

function renderSessions(ci, sessions) {
  const list = document.getElementById(`session-list-${ci}`);
  if (!list) return;
  if (!sessions.length) {
    list.innerHTML = '<div style="color:var(--text3);font-size:.75rem">Keine Aufnahmen</div>';
    return;
  }
  list.innerHTML = sessions.map(s => {
    const safeName = escHtml(s.name);
    return `
    <div class="session-item">
      <span class="session-name">${safeName}${s.active ? ' ●' : ''}</span>
      <span class="session-info" onclick="openGallery('${s.name.replace(/'/g, "\\'")}', ${ci})">${s.frame_count} ${s.capture_mode === 'clip' ? 'Clips' : 'Bilder'}</span>
      <div class="session-actions">
        ${!s.active
          ? `<button class="btn-small" style="color:var(--danger)"
               onclick="deleteSession('${s.name.replace(/'/g, "\\'")}', ${ci})">&#10005;</button>`
          : ''}
      </div>
    </div>`;
  }).join('');
}

async function startTimelapse(ci) {
  const intervalHours = parseDE(document.getElementById(`tl-interval-${ci}`).value);
  const intervalSecs  = Math.round(intervalHours * 3600);
  const devIdx        = parseInt(document.getElementById(`tl-cam-idx-${ci}`).value);
  const [capW, capH]  = document.getElementById(`tl-resolution-${ci}`).value.split('x').map(Number);
  const captureMode   = document.getElementById(`tl-capture-mode-${ci}`).value;
  const clipDuration  = parseInt(document.getElementById(`tl-clip-duration-${ci}`).value);
  const clipFps       = parseInt(document.getElementById(`tl-clip-fps-${ci}`).value);

  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      [`cam_${ci}_timelapse_interval`]: intervalSecs,
      [`cam_${ci}_device_index`]: devIdx,
      [`cam_${ci}_capture_width`]: capW,
      [`cam_${ci}_capture_height`]: capH,
      [`cam_${ci}_capture_mode`]: captureMode,
      [`cam_${ci}_clip_duration`]: clipDuration,
      [`cam_${ci}_clip_fps`]: clipFps,
    })
  });

  const r = await fetch(`${API}/api/timelapse/start?cam=${ci}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  });
  if (r.ok) {
    showToast(`Timelapse${_cameraCount > 1 ? ' ' + ci : ''} gestartet`);
    fetchTimelapse(ci);
    loadSessions(ci);
  } else {
    const e = await r.json();
    showToast('Fehler: ' + (e.detail || r.status));
  }
}

async function stopTimelapse(ci) {
  await fetch(`${API}/api/timelapse/stop?cam=${ci}`, { method: 'POST' });
  showToast(`Timelapse${_cameraCount > 1 ? ' ' + ci : ''} gestoppt`);
  fetchTimelapse(ci);
  loadSessions(ci);
}

async function deleteSession(session, ci) {
  if (!confirm(`Aufnahme "${session}" löschen?`)) return;
  const r = await fetch(`${API}/api/timelapse/sessions/${session}?cam=${ci}`, { method: 'DELETE' });
  if (r.ok) {
    showToast('Gelöscht');
    loadSessions(ci);
  }
}

// ----------------------------------------------------------------
// Camera preview (per camera)
// ----------------------------------------------------------------
let _previewAbort = null;

async function refreshPreview(ci) {
  if (ci === undefined) { for (let i = 0; i < _cameraCount; i++) refreshPreview(i); return; }
  const img = document.getElementById(`camera-preview-${ci}`);
  const ph  = document.getElementById(`preview-placeholder-${ci}`);
  if (!img || !ph) return;

  // Cancel previous in-flight preview request
  if (_previewAbort) _previewAbort.abort();
  _previewAbort = new AbortController();

  try {
    const r = await fetch(`${API}/api/timelapse/preview?cam=${ci}&t=${Date.now()}`, { signal: _previewAbort.signal });
    if (!r.ok) throw new Error();
    const blob = await r.blob();
    // Revoke previous Blob URL to prevent memory leak
    if (img.src && img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(blob);
    img.classList.add('loaded');
    ph.style.display = 'none';
    if (ci === 0) setDot('dot-cam', 'ok');
  } catch(e) {
    if (e.name === 'AbortError') return;
    img.classList.remove('loaded');
    ph.style.display = '';
    if (ci === 0) setDot('dot-cam', 'error');
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
      document.getElementById('upd-status-text').textContent = 'Update abgeschlossen';
      document.getElementById('upd-spinner').classList.add('hidden');
      document.getElementById('update-badge').classList.add('hidden');
      document.getElementById('btn-do-update').classList.add('hidden');
      document.getElementById('btn-recheck').classList.add('hidden');
      document.getElementById('upd-reboot-dialog').classList.remove('hidden');
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

function reloadAfterUpdate() {
  location.reload();
}

let _historyVisible = false;

async function toggleHistory() {
  const wrap = document.getElementById('upd-history-wrap');
  _historyVisible = !_historyVisible;
  wrap.classList.toggle('hidden', !_historyVisible);
  if (_historyVisible) await loadVersionHistory();
}

async function loadVersionHistory() {
  const list = document.getElementById('upd-history-list');
  list.innerHTML = '<span style="color:var(--text3);font-size:.8rem">Lade…</span>';
  try {
    const r = await fetch(`${API}/api/update/history`);
    const d = await r.json();
    if (d.error || !d.commits.length) {
      list.innerHTML = `<span style="color:var(--text3);font-size:.8rem">${d.error || 'Keine Commits gefunden'}</span>`;
      return;
    }
    list.innerHTML = d.commits.map(c => {
      const safeSubject = escHtml(c.subject);
      const safeShort = escHtml(c.short);
      const safeHash = escHtml(c.hash);
      return `
      <div class="upd-commit${c.current ? ' current' : ''}">
        <div class="upd-commit-meta">
          <code>${safeShort}</code>
          <span class="upd-date">${escHtml(c.date)}</span>
        </div>
        <span class="upd-commit-subject" title="${safeSubject}">${safeSubject}</span>
        ${c.current
          ? '<span style="color:var(--text3);font-size:.75rem;flex-shrink:0">aktiv</span>'
          : `<button class="btn-small" onclick="applyRollback('${safeHash}', '${safeSubject.replace(/'/g, "\\'")}')">Wiederherstellen</button>`
        }
      </div>`;
    }).join('');
  } catch(e) {
    list.innerHTML = '<span style="color:var(--danger);font-size:.8rem">Fehler beim Laden</span>';
  }
}

async function applyRollback(hash, subject) {
  if (!confirm(`Version wiederherstellen?\n\n${subject}\n\nDie Anwendung wird danach neu gestartet.`)) return;

  document.getElementById('upd-history-wrap').classList.add('hidden');
  _historyVisible = false;
  document.getElementById('btn-do-update').classList.add('hidden');
  document.getElementById('btn-recheck').classList.add('hidden');
  document.getElementById('btn-history').classList.add('hidden');
  document.getElementById('upd-spinner').classList.remove('hidden');
  document.getElementById('upd-log-wrap').classList.remove('hidden');
  document.getElementById('upd-status-text').textContent = 'Rollback läuft…';

  await fetch(`${API}/api/update/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ commit: hash }),
  });
  pollUpdateStatus();
}

async function rebootPi() {
  document.getElementById('upd-reboot-dialog').classList.add('hidden');
  document.getElementById('upd-status-text').textContent = 'Pi wird neu gestartet…';
  try {
    await fetch(`${API}/api/update/reboot`, { method: 'POST' });
  } catch(e) {}
  setTimeout(pollUntilServerBack, 15_000);
}

async function pollUntilServerBack() {
  try {
    const r = await fetch(`${API}/api/update/status`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) { location.reload(); return; }
  } catch(e) {}
  setTimeout(pollUntilServerBack, 3000);
}

// ----------------------------------------------------------------
// WiFi management
// ----------------------------------------------------------------
let _wifiNetworks = [];

function openWifi() {
  document.getElementById('wifi-overlay').classList.remove('hidden');
  fetchWifiStatus();
}

function closeWifi(evt) {
  if (evt && evt.target !== document.getElementById('wifi-overlay')) return;
  document.getElementById('wifi-overlay').classList.add('hidden');
}

async function fetchWifiStatus() {
  try {
    const r = await fetch(`${API}/api/wifi/status`);
    const d = await r.json();
    const statusEl = document.getElementById('wifi-conn-status');
    const ssidEl   = document.getElementById('wifi-conn-ssid');
    const signalEl = document.getElementById('wifi-conn-signal');
    const ipEl     = document.getElementById('wifi-conn-ip');
    const dot      = document.getElementById('dot-wifi');

    const toggleBtn = document.getElementById('btn-wifi-toggle');

    if (d.mock_mode) {
      statusEl.textContent = 'Nicht verfügbar (Mock)';
      statusEl.style.color = 'var(--text3)';
      ssidEl.textContent = '--';
      signalEl.textContent = '--';
      ipEl.textContent = '--';
      toggleBtn.textContent = '--';
      toggleBtn.disabled = true;
      dot.className = 'status-dot';
      return;
    }

    // WLAN-Adapter an/aus
    toggleBtn.disabled = false;
    if (d.wifi_enabled === false) {
      statusEl.textContent = 'WLAN ausgeschaltet';
      statusEl.style.color = 'var(--text3)';
      ssidEl.textContent = '--';
      signalEl.textContent = '--';
      ipEl.textContent = d.ip || '--';
      dot.className = 'status-dot';
      toggleBtn.textContent = 'Einschalten';
      toggleBtn.className = 'btn-small';
      return;
    }

    toggleBtn.textContent = 'Ausschalten';
    toggleBtn.className = 'btn-small btn-small-danger';

    if (d.connected) {
      statusEl.textContent = 'Verbunden';
      statusEl.style.color = 'var(--accent)';
      ssidEl.textContent = d.ssid || '--';
      signalEl.textContent = d.signal != null ? d.signal + '%' : '--';
      ipEl.textContent = d.ip || '--';
      dot.className = 'status-dot ok';
    } else {
      statusEl.textContent = 'Nicht verbunden';
      statusEl.style.color = 'var(--danger)';
      ssidEl.textContent = '--';
      signalEl.textContent = '--';
      ipEl.textContent = d.ip || '--';
      dot.className = 'status-dot error';
    }
  } catch(e) {
    document.getElementById('dot-wifi').className = 'status-dot';
  }
}

async function wifiScan(rescan) {
  const btn = document.getElementById('btn-wifi-scan');
  const rescanBtn = document.getElementById('btn-wifi-rescan');
  const spinner = document.getElementById('wifi-scan-spinner');
  btn.disabled = true;
  if (rescanBtn) rescanBtn.disabled = true;
  spinner.classList.remove('hidden');
  const list = document.getElementById('wifi-network-list');
  list.innerHTML = '<div class="wifi-empty">Scanne...</div>';

  try {
    // Rescan auslösen wenn gewünscht (kann kurz die Verbindung stören)
    if (rescan) {
      spinner.textContent = 'Rescan...';
      await fetch(`${API}/api/wifi/rescan`, { method: 'POST' });
    }
    spinner.textContent = 'Scanne...';
    const r = await fetch(`${API}/api/wifi/scan`);
    const d = await r.json();
    if (!r.ok) {
      list.innerHTML = `<div class="wifi-empty">Scan fehlgeschlagen: ${escHtml(d.error || 'Unbekannter Fehler')}</div>`;
      return;
    }
    _wifiNetworks = d.networks || [];
    if (!_wifiNetworks.length) {
      list.innerHTML = '<div class="wifi-empty">Keine Netzwerke gefunden</div>';
      return;
    }
    list.innerHTML = _wifiNetworks.map((n, i) => {
      const bars = signalBars(n.signal);
      const lock = n.secured ? '&#128274;' : '';
      return `<div class="wifi-network-item" onclick="wifiSelectNetwork(${i})">
        <div class="wifi-bars">${bars}</div>
        <span class="wifi-ssid">${escHtml(n.ssid)}</span>
        <span class="wifi-lock">${lock}</span>
        <span class="wifi-signal">${n.signal}%</span>
      </div>`;
    }).join('');
  } catch(e) {
    list.innerHTML = '<div class="wifi-empty">Scan fehlgeschlagen</div>';
  } finally {
    btn.disabled = false;
    if (rescanBtn) rescanBtn.disabled = false;
    spinner.classList.add('hidden');
    spinner.textContent = 'Scanne...';
  }
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function signalBars(signal) {
  const count = signal > 75 ? 4 : signal > 50 ? 3 : signal > 25 ? 2 : 1;
  return [4, 7, 10, 13].map((h, i) =>
    `<div class="bar${i < count ? ' active' : ''}" style="height:${h}px"></div>`
  ).join('');
}

function wifiSelectNetwork(idx) {
  const n = _wifiNetworks[idx];
  if (!n) return;
  document.getElementById('wifi-manual-ssid').value = n.ssid;
  const passInput = document.getElementById('wifi-manual-pass');
  passInput.value = '';
  if (n.secured) {
    passInput.focus();
  } else {
    wifiConnectManual();
  }
}

function toggleWifiPass() {
  const inp = document.getElementById('wifi-manual-pass');
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

async function toggleWifiRadio() {
  const btn = document.getElementById('btn-wifi-toggle');
  const turningOn = btn.textContent === 'Einschalten';

  if (!turningOn && !confirm('WLAN wirklich ausschalten?\n\nWenn der Pi nur per WLAN erreichbar ist, verlierst du die Verbindung zum Dashboard.')) {
    return;
  }

  btn.disabled = true;
  btn.textContent = turningOn ? 'Schalte ein...' : 'Schalte aus...';

  try {
    const r = await fetch(`${API}/api/wifi/radio?enabled=${turningOn}`, { method: 'POST' });
    const d = await r.json();
    if (r.ok) {
      showToast(turningOn ? 'WLAN eingeschaltet' : 'WLAN ausgeschaltet');
      setTimeout(fetchWifiStatus, 2000);
    } else {
      showToast(d.error || 'Fehler');
      btn.disabled = false;
    }
  } catch(e) {
    showToast('Verbindungsfehler');
    btn.disabled = false;
  }
}

async function wifiConnectManual() {
  const ssid = document.getElementById('wifi-manual-ssid').value.trim();
  const pass = document.getElementById('wifi-manual-pass').value;
  const statusEl = document.getElementById('wifi-connect-status');

  if (!ssid) {
    showToast('Bitte SSID eingeben');
    return;
  }

  const btn = document.getElementById('btn-wifi-connect');
  btn.disabled = true;
  btn.textContent = 'Verbinde...';
  statusEl.classList.remove('hidden');
  statusEl.textContent = 'Verbindung wird hergestellt...';
  statusEl.style.color = 'var(--text3)';

  try {
    const r = await fetch(`${API}/api/wifi/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid, password: pass }),
    });
    const d = await r.json();
    if (r.ok && d.connected) {
      statusEl.textContent = `Verbunden mit "${ssid}" (IP: ${d.ip || '...'})`;
      statusEl.style.color = 'var(--accent)';
      showToast(`WLAN verbunden: ${ssid}`);
      fetchWifiStatus();
    } else {
      statusEl.textContent = d.detail || d.error || 'Verbindung fehlgeschlagen';
      statusEl.style.color = 'var(--danger)';
    }
  } catch(e) {
    statusEl.textContent = 'Verbindungsfehler';
    statusEl.style.color = 'var(--danger)';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Verbinden';
  }
}

// ----------------------------------------------------------------
// PWA install guide
// ----------------------------------------------------------------
function togglePwaGuide() {
  const guide = document.getElementById('pwa-guide');
  const btn = document.getElementById('btn-pwa-guide');
  if (guide.classList.contains('hidden')) {
    guide.classList.remove('hidden');
    btn.textContent = 'Anleitung ausblenden';
    // Build install link: prefer Tailscale IP, fallback to local IP
    const tsIp = document.getElementById('settings-ts-ip');
    const link = document.getElementById('pwa-install-link');
    let ip = null;
    if (tsIp && tsIp.textContent && tsIp.textContent !== '--') {
      ip = tsIp.textContent;
    } else {
      ip = location.hostname;
    }
    if (ip && link) {
      const url = `http://${ip}`;
      link.href = url;
      link.textContent = url;
    }
  } else {
    guide.classList.add('hidden');
    btn.textContent = 'Anleitung anzeigen';
  }
}

// ----------------------------------------------------------------
// Tailscale VPN
// ----------------------------------------------------------------
async function fetchTailscaleStatus() {
  try {
    const r = await fetch(`${API}/api/tailscale/status`);
    const d = await r.json();
    const statusEl  = document.getElementById('settings-ts-status');
    const ipEl      = document.getElementById('settings-ts-ip');
    const hostEl    = document.getElementById('settings-ts-hostname');
    const netEl     = document.getElementById('settings-ts-tailnet');
    const dot       = document.getElementById('dot-ts');
    const toggleBtn = document.getElementById('btn-settings-ts-toggle');
    const authWrap  = document.getElementById('settings-ts-auth-wrap');

    if (!d.installed) {
      statusEl.textContent = 'Nicht installiert';
      statusEl.style.color = 'var(--text3)';
      ipEl.textContent = hostEl.textContent = netEl.textContent = '--';
      toggleBtn.textContent = '--';
      toggleBtn.disabled = true;
      dot.className = 'status-dot';
      authWrap.classList.add('hidden');
      return;
    }

    toggleBtn.disabled = false;
    authWrap.classList.add('hidden');

    if (d.state === 'Running') {
      statusEl.textContent = 'Verbunden';
      statusEl.style.color = 'var(--accent)';
      ipEl.textContent = d.ip || '--';
      hostEl.textContent = d.hostname || '--';
      netEl.textContent = d.tailnet || '--';
      dot.className = 'status-dot ok';
      toggleBtn.textContent = 'Ausschalten';
      toggleBtn.className = 'btn-small btn-small-danger';
    } else if (d.state === 'NeedsLogin') {
      statusEl.textContent = 'Anmeldung erforderlich';
      statusEl.style.color = 'var(--warn)';
      ipEl.textContent = hostEl.textContent = netEl.textContent = '--';
      dot.className = 'status-dot warn';
      toggleBtn.textContent = 'Einschalten';
      toggleBtn.className = 'btn-small';
      if (d.auth_url) {
        authWrap.classList.remove('hidden');
        const link = document.getElementById('settings-ts-auth-url');
        link.href = d.auth_url;
        link.textContent = d.auth_url;
      }
    } else {
      statusEl.textContent = 'Gestoppt';
      statusEl.style.color = 'var(--text3)';
      ipEl.textContent = hostEl.textContent = netEl.textContent = '--';
      dot.className = 'status-dot';
      toggleBtn.textContent = 'Einschalten';
      toggleBtn.className = 'btn-small';
    }
  } catch(e) {
    document.getElementById('dot-ts').className = 'status-dot';
  }
}

async function toggleTailscaleFromSettings() {
  const btn = document.getElementById('btn-settings-ts-toggle');
  const spinner = document.getElementById('settings-ts-spinner');
  const isOn = btn.textContent === 'Ausschalten';
  btn.disabled = true;
  spinner.classList.remove('hidden');

  try {
    const endpoint = isOn ? '/api/tailscale/down' : '/api/tailscale/up';
    const r = await fetch(`${API}${endpoint}`, { method: 'POST' });
    const d = await r.json();
    if (d.auth_url) {
      const authWrap = document.getElementById('settings-ts-auth-wrap');
      authWrap.classList.remove('hidden');
      const link = document.getElementById('settings-ts-auth-url');
      link.href = d.auth_url;
      link.textContent = d.auth_url;
    }
    showToast(d.message || (isOn ? 'VPN gestoppt' : 'VPN gestartet'));
  } catch(e) {
    showToast('Tailscale-Fehler');
  }

  spinner.classList.add('hidden');
  setTimeout(fetchTailscaleStatus, 2000);
}

async function reauthTailscale() {
  const btn = document.getElementById('btn-settings-ts-reauth');
  const spinner = document.getElementById('settings-ts-spinner');
  btn.disabled = true;
  spinner.textContent = 'Tailscale wird zurueckgesetzt... (kann bis zu 30s dauern)';
  spinner.classList.remove('hidden');

  try {
    const r = await fetch(`${API}/api/tailscale/reauth`, { method: 'POST' });
    const d = await r.json();
    if (d.auth_url) {
      const authWrap = document.getElementById('settings-ts-auth-wrap');
      authWrap.classList.remove('hidden');
      const link = document.getElementById('settings-ts-auth-url');
      link.href = d.auth_url;
      link.textContent = d.auth_url;
    }
    if (d.debug) console.log('reauth debug:\n' + d.debug);
    showToast(d.message || 'Neu-Anmeldung gestartet');
  } catch(e) {
    showToast('Tailscale-Fehler: ' + e.message);
  }

  btn.disabled = false;
  spinner.textContent = 'Bitte warten...';
  spinner.classList.add('hidden');
  setTimeout(fetchTailscaleStatus, 3000);
}

async function submitTsAuthKey() {
  const input = document.getElementById('ts-authkey-input');
  const key = input.value.trim();
  if (!key) { showToast('Bitte Auth-Key eingeben'); return; }

  const spinner = document.getElementById('settings-ts-spinner');
  spinner.textContent = 'Tailscale wird verbunden... (kann bis zu 30s dauern)';
  spinner.classList.remove('hidden');

  try {
    const r = await fetch(`${API}/api/tailscale/authkey`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key}),
    });
    const d = await r.json();
    if (d.debug) console.log('authkey debug:\n' + d.debug);
    if (d.ok) {
      showToast('Tailscale verbunden!');
      document.getElementById('settings-ts-authkey-wrap').classList.add('hidden');
      input.value = '';
    } else {
      showToast('Fehler: ' + (d.error || 'Unbekannt'));
    }
  } catch(e) {
    showToast('Tailscale-Fehler: ' + e.message);
  }

  spinner.classList.add('hidden');
  setTimeout(fetchTailscaleStatus, 2000);
}

function toggleSettingsTsSetup() {
  const guide = document.getElementById('settings-ts-setup-guide');
  const btn = document.getElementById('btn-settings-ts-setup');
  if (guide.classList.contains('hidden')) {
    guide.classList.remove('hidden');
    btn.textContent = 'Anleitung ausblenden';
  } else {
    guide.classList.add('hidden');
    btn.textContent = 'Einrichtungsanleitung';
  }
}

async function systemReboot() {
  if (!confirm('Raspberry Pi jetzt neu starten?\n\nDas Dashboard ist für ca. 30–60 Sekunden nicht erreichbar.')) return;
  const el = document.getElementById('system-status');
  el.textContent = 'Pi wird neu gestartet…';
  el.classList.remove('hidden');
  try { await fetch(`${API}/api/system/reboot`, { method: 'POST' }); } catch(e) {}
  setTimeout(pollUntilServerBack, 20_000);
}

async function systemShutdown() {
  if (!confirm('Raspberry Pi jetzt herunterfahren?\n\nEr muss danach manuell wieder eingeschaltet werden.')) return;
  const el = document.getElementById('system-status');
  el.textContent = 'Pi wird heruntergefahren…';
  el.classList.remove('hidden');
  try { await fetch(`${API}/api/system/shutdown`, { method: 'POST' }); } catch(e) {}
}

function openUpdate() {
  // Reset state from previous session
  document.getElementById('upd-history-wrap').classList.add('hidden');
  document.getElementById('upd-reboot-dialog').classList.add('hidden');
  document.getElementById('btn-do-update').classList.remove('hidden');
  document.getElementById('btn-recheck').classList.remove('hidden');
  document.getElementById('btn-history').classList.remove('hidden');
  _historyVisible = false;
  document.getElementById('update-overlay').classList.remove('hidden');
  checkUpdate();
}

function closeUpdate(evt) {
  if (evt && evt.target !== document.getElementById('update-overlay')) return;
  document.getElementById('update-overlay').classList.add('hidden');
}

function openSettings() {
  loadSettingsModal();
  const s = document.getElementById('system-status');
  s.classList.add('hidden');
  s.textContent = '';
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
    document.getElementById('camera-count').value   = s.camera_count ?? 1;
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

async function updateShareUrl(enabled) {
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
  // Sofort speichern damit die URL beim Klick auch wirklich erreichbar ist
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ timelapse_share_enabled: enabled }),
  });
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
    camera_count: Math.max(1, Math.min(4, parseInt(document.getElementById('camera-count').value) || 1)),
  };
  // Tooltip delay is client-side only
  localStorage.setItem('tooltip_delay_ms', document.getElementById('tooltip-delay').value || '600');
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  scheduleUpdateCheck(body.update_check_interval_days);
  // Rebuild camera sections if count changed
  if (body.camera_count !== _cameraCount) {
    await initCameraSections();
    await loadAllSessions();
    await fetchAllTimelapse();
    refreshPreview();
  }
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
      cont.innerHTML = devices.map(dev => {
        const safeMac = escHtml(dev.mac);
        return `
        <div class="discover-item">
          <span class="mac-addr">${safeMac}</span>
          <span style="color:var(--text3)">${dev.rssi} dBm</span>
          <button class="btn-small" onclick="setMac('inside','${safeMac}')">Innen</button>
          <button class="btn-small" onclick="setMac('outside','${safeMac}')">Außen</button>
        </div>`;
      }).join('');
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
    fetchAllTimelapse(),
  ]);
}

async function init() {
  initGauge();
  initCharts();
  await loadControlSettings();
  await initCameraSections();
  await loadAllSessions();
  await pollAll();
  await loadHistory();
  refreshPreview();
  fetchWifiStatus();
  fetchTailscaleStatus();

  // Poll every 10 seconds
  pollTimer = setInterval(pollAll, 10_000);
  // Reload history every 2 minutes
  setInterval(loadHistory, 120_000);
  // Reload sessions every 30 seconds
  setInterval(loadAllSessions, 30_000);
  // Tailscale status every 60 seconds
  setInterval(fetchTailscaleStatus, 60_000);
  // Schedule automatic update check based on saved interval
  try {
    const r = await fetch(`${API}/api/settings`);
    const s = await r.json();
    scheduleUpdateCheck(s.update_check_interval_days ?? 7);
  } catch(e) {}

  // Register service worker for PWA
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}

// ----------------------------------------------------------------
// Gallery / Lightbox
// ----------------------------------------------------------------
let _galleryFiles = [];
let _lightboxIdx  = 0;
let _galleryCam   = 0;

async function openGallery(session, ci) {
  _galleryCam = ci || 0;
  document.getElementById('gallery-title').textContent = `Aufnahmen – ${session}`;
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = '<div class="gallery-empty">Lade…</div>';
  document.getElementById('gallery-overlay').classList.remove('hidden');

  try {
    const r = await fetch(`${API}/api/timelapse/sessions/${encodeURIComponent(session)}/files?cam=${_galleryCam}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    _galleryFiles = d.files;
    renderGallery();
  } catch(err) {
    console.error('Gallery load error:', err);
    grid.innerHTML = `<div class="gallery-empty">Fehler beim Laden der Dateien.<br><small style="color:var(--text3)">${err.message || err}</small></div>`;
  }
}

function renderGallery() {
  const grid = document.getElementById('gallery-grid');
  if (!_galleryFiles.length) {
    grid.innerHTML = '<div class="gallery-empty">Keine Aufnahmen vorhanden.</div>';
    return;
  }
  grid.innerHTML = _galleryFiles.map((f, i) => {
    const label = escHtml(f.name.replace(/\.\w+$/, '').replace(/_/g, ' '));
    const safeUrl = escHtml(f.url);
    const safeName = escHtml(f.name);
    if (f.type === 'video') {
      return `<div class="gallery-thumb" onclick="openLightbox(${i})">
        <video src="${safeUrl}" muted preload="metadata" style="pointer-events:none"></video>
        <span class="thumb-video-icon">▶</span>
        <span class="thumb-label">${label}</span>
      </div>`;
    }
    return `<div class="gallery-thumb" onclick="openLightbox(${i})">
      <img src="${safeUrl}" loading="lazy" alt="${safeName}">
      <span class="thumb-label">${label}</span>
    </div>`;
  }).join('');
}

function closeGallery(e) {
  if (e && e.target !== document.getElementById('gallery-overlay')) return;
  document.getElementById('gallery-overlay').classList.add('hidden');
}

function openLightbox(idx) {
  _lightboxIdx = idx;
  showLightboxItem();
  document.getElementById('lightbox-overlay').classList.remove('hidden');
}

function closeLightbox() {
  document.getElementById('lightbox-overlay').classList.add('hidden');
  const vid = document.getElementById('lightbox-video');
  vid.pause();
  vid.src = '';
}

function lightboxNav(dir) {
  _lightboxIdx = (_lightboxIdx + dir + _galleryFiles.length) % _galleryFiles.length;
  showLightboxItem();
}

function showLightboxItem() {
  const f   = _galleryFiles[_lightboxIdx];
  const img = document.getElementById('lightbox-img');
  const vid = document.getElementById('lightbox-video');

  document.getElementById('lightbox-counter').textContent =
    `${_lightboxIdx + 1} / ${_galleryFiles.length}`;

  if (f.type === 'video') {
    vid.pause();
    vid.src = f.url;
    vid.classList.remove('hidden');
    img.classList.add('hidden');
    img.src = '';
  } else {
    img.src = f.url;
    img.classList.remove('hidden');
    vid.classList.add('hidden');
    vid.pause();
    vid.src = '';
  }
}

document.addEventListener('keydown', e => {
  const lb = document.getElementById('lightbox-overlay');
  if (!lb.classList.contains('hidden')) {
    if (e.key === 'ArrowLeft')  lightboxNav(-1);
    if (e.key === 'ArrowRight') lightboxNav(1);
    if (e.key === 'Escape')     closeLightbox();
  } else if (!document.getElementById('gallery-overlay').classList.contains('hidden')) {
    if (e.key === 'Escape') closeGallery({ target: document.getElementById('gallery-overlay') });
  }
});

// Pause polling when tab is hidden, resume when visible
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  } else {
    if (!pollTimer) {
      pollAll();
      pollTimer = setInterval(pollAll, 10_000);
    }
  }
});

document.addEventListener('DOMContentLoaded', init);
