/* ============================================================
   Trend Manager – Chart + Controls
============================================================ */

const COLORS = {
  inTemp:   '#ef5350',
  outTemp:  '#58a6ff',
  inHum:    '#3fb950',
  outHum:   '#f0883e',
  fan:      '#e3b341',
};

let trendChart = null;
let rawData = { inside: [], outside: [], fan: [] };
let activeHours = 24;

// ----------------------------------------------------------------
// Init
// ----------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  initChart();
  bindRangeButtons();
  bindSeriesTogggles();
  setDefaultCustomRange();
  loadData(activeHours);
});

// ----------------------------------------------------------------
// Chart setup – dual Y-axis
// ----------------------------------------------------------------
function initChart() {
  const ctx = document.getElementById('trend-chart').getContext('2d');
  trendChart = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
        makeSeries('Innen Temp',     COLORS.inTemp,  'y'),
        makeSeries('Aussen Temp',    COLORS.outTemp,  'y'),
        makeSeries('Innen Feuchte',  COLORS.inHum,   'y1'),
        makeSeries('Aussen Feuchte', COLORS.outHum,   'y1'),
        makeSeries('Luefter',        COLORS.fan,      'y1'),
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              const v = ctx.parsed.y;
              if (v == null) return null;
              const idx = ctx.datasetIndex;
              if (idx <= 1) return ctx.dataset.label + ': ' + v.toFixed(1) + ' °C';
              if (idx <= 3) return ctx.dataset.label + ': ' + v.toFixed(1) + ' %';
              return ctx.dataset.label + ': ' + v.toFixed(0) + ' %';
            },
          },
        },
      },
      scales: {
        x: {
          type: 'time',
          time: { tooltipFormat: 'dd.MM.yyyy HH:mm', displayFormats: { hour: 'HH:mm', day: 'dd.MM.' } },
          grid: { color: 'rgba(255,255,255,.06)' },
          ticks: { color: '#8b949e', maxTicksLimit: 12 },
        },
        y: {
          position: 'left',
          title: { display: true, text: 'Temperatur (°C)', color: '#8b949e' },
          grid: { color: 'rgba(255,255,255,.06)' },
          ticks: { color: '#8b949e' },
        },
        y1: {
          position: 'right',
          title: { display: true, text: 'Feuchte / Lüfter (%)', color: '#8b949e' },
          min: 0,
          max: 100,
          grid: { drawOnChartArea: false },
          ticks: { color: '#8b949e' },
        },
      },
    },
  });
}

function makeSeries(label, color, yAxisID) {
  return {
    label,
    borderColor: color,
    backgroundColor: color + '18',
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.3,
    fill: false,
    yAxisID,
    data: [],
  };
}

// ----------------------------------------------------------------
// Data loading
// ----------------------------------------------------------------
async function loadData(hours, fromTs, toTs) {
  const max = 800;
  let sensorUrl, fanUrl;
  if (fromTs && toTs) {
    sensorUrl = `/api/sensors/history?from_ts=${encodeURIComponent(fromTs)}&to_ts=${encodeURIComponent(toTs)}&max_points=${max}`;
    fanUrl    = `/api/fans/history?from_ts=${encodeURIComponent(fromTs)}&to_ts=${encodeURIComponent(toTs)}&max_points=${max}`;
  } else {
    sensorUrl = `/api/sensors/history?hours=${hours}&max_points=${max}`;
    fanUrl    = `/api/fans/history?hours=${hours}&max_points=${max}`;
  }

  try {
    const [sRes, fRes] = await Promise.all([fetch(sensorUrl), fetch(fanUrl)]);
    const sData = await sRes.json();
    const fData = await fRes.json();

    rawData.inside  = sData.inside  || [];
    rawData.outside = sData.outside || [];
    rawData.fan     = fData.events  || [];

    updateChart();
  } catch (e) {
    console.error('Trend data load failed:', e);
  }
}

function updateChart() {
  const ds = trendChart.data.datasets;
  ds[0].data = rawData.inside.map(r  => ({ x: r.timestamp, y: r.temperature }));
  ds[1].data = rawData.outside.map(r => ({ x: r.timestamp, y: r.temperature }));
  ds[2].data = rawData.inside.map(r  => ({ x: r.timestamp, y: r.humidity }));
  ds[3].data = rawData.outside.map(r => ({ x: r.timestamp, y: r.humidity }));
  ds[4].data = rawData.fan.map(r     => ({ x: r.timestamp, y: r.speed * 100 }));
  trendChart.update('none');
}

// ----------------------------------------------------------------
// Range buttons
// ----------------------------------------------------------------
function bindRangeButtons() {
  document.getElementById('range-buttons').addEventListener('click', (e) => {
    const btn = e.target.closest('.range-btn');
    if (!btn) return;
    const h = btn.dataset.hours;

    document.querySelectorAll('#range-buttons .range-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const custom = document.getElementById('custom-range');
    if (h === 'custom') {
      custom.classList.remove('hidden');
      return;
    }
    custom.classList.add('hidden');
    activeHours = parseInt(h);
    loadData(activeHours);
  });
}

function loadCustomRange() {
  const from = document.getElementById('range-from').value;
  const to   = document.getElementById('range-to').value;
  if (!from || !to) return;
  // Convert to ISO strings
  const fromTs = new Date(from).toISOString();
  const toTs   = new Date(to).toISOString();
  loadData(0, fromTs, toTs);
}

function setDefaultCustomRange() {
  const now = new Date();
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  document.getElementById('range-to').value   = toLocalISOString(now);
  document.getElementById('range-from').value = toLocalISOString(yesterday);
}

function toLocalISOString(d) {
  const pad = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
    + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

// ----------------------------------------------------------------
// Series toggles
// ----------------------------------------------------------------
function bindSeriesTogggles() {
  document.getElementById('series-toggles').addEventListener('change', (e) => {
    const label = e.target.closest('.series-toggle');
    if (!label) return;
    const idx = parseInt(label.dataset.idx);
    const visible = e.target.checked;

    label.classList.toggle('off', !visible);
    trendChart.data.datasets[idx].hidden = !visible;
    trendChart.update('none');
  });
}

// ----------------------------------------------------------------
// CSV Export
// ----------------------------------------------------------------
function exportCSV() {
  const rows = [['Zeitstempel', 'Innen Temp (°C)', 'Innen Feuchte (%)', 'Aussen Temp (°C)', 'Aussen Feuchte (%)', 'Luefter (%)']];

  // Build time-indexed map
  const map = new Map();
  for (const r of rawData.inside) {
    const key = r.timestamp;
    if (!map.has(key)) map.set(key, {});
    map.get(key).iTemp = r.temperature;
    map.get(key).iHum  = r.humidity;
  }
  for (const r of rawData.outside) {
    const key = r.timestamp;
    if (!map.has(key)) map.set(key, {});
    map.get(key).oTemp = r.temperature;
    map.get(key).oHum  = r.humidity;
  }
  for (const r of rawData.fan) {
    const key = r.timestamp;
    if (!map.has(key)) map.set(key, {});
    map.get(key).fan = (r.speed * 100).toFixed(0);
  }

  // Sort by timestamp
  const sorted = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  for (const [ts, v] of sorted) {
    rows.push([
      ts,
      v.iTemp ?? '',
      v.iHum  ?? '',
      v.oTemp ?? '',
      v.oHum  ?? '',
      v.fan   ?? '',
    ]);
  }

  const csv = rows.map(r => r.join(';')).join('\n');
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'trend_' + new Date().toISOString().slice(0, 16).replace(':', '') + '.csv';
  a.click();
  URL.revokeObjectURL(url);
}
