/* ================================================================
   Ansichten: klassisch / optimiert / mobil

   Ein einziges Dokument, drei Anordnungen. Umgeschaltet wird über eine
   Klasse am <html>-Element; nichts wird aus dem DOM entfernt, damit in
   keiner Ansicht eine Funktion fehlt. Die Wahl liegt im localStorage und
   gilt deshalb pro Gerät und Browser, nicht für alle Geräte gemeinsam.
================================================================ */

const VIEWS = ['klassisch', 'optimiert', 'mobil'];
const VIEW_KEY = 'gh_view';
const PANEL_KEY = 'gh_panels';

// Karten, die in der mobilen Ansicht eingeklappt werden können. Der Rest
// (Messwerte, Lüfter) bleibt immer offen – das ist der Blick beim Betreten.
const COLLAPSIBLE = '.control-card, .chart-card, .timelapse-card, .camera-card';

const VIEW_HINT = {
  klassisch: 'Die gewohnte Anordnung, unverändert.',
  optimiert: 'Dichter für große Bildschirme. Selten genutzte Regelungswerte sind eingeklappt.',
  mobil:     'Für Telefon und Tablet. Abschnitte lassen sich auf- und zuklappen.',
};

function lesen(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function schreiben(key, wert) {
  try { localStorage.setItem(key, wert); } catch (e) { /* privater Modus */ }
}

function erkenneAnsicht() {
  const grob = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  return (grob || window.innerWidth < 820) ? 'mobil' : 'optimiert';
}

function aktuelleAnsicht() {
  const gespeichert = lesen(VIEW_KEY);
  return VIEWS.includes(gespeichert) ? gespeichert : erkenneAnsicht();
}

function setView(name) {
  if (!VIEWS.includes(name)) return;
  schreiben(VIEW_KEY, name);
  wendeAn(name);
  if (typeof showToast === 'function') showToast('Ansicht: ' + name);
}

function wendeAn(name) {
  const root = document.documentElement;
  VIEWS.forEach(v => root.classList.toggle('view-' + v, v === name));

  const select = document.getElementById('view-mode');
  if (select) select.value = name;
  const hint = document.getElementById('view-hint');
  if (hint) hint.textContent = VIEW_HINT[name];

  // Feinschliff ist nur in den optimierten Ansichten einklappbar; klassisch
  // zeigt ihn wie bisher offen.
  if (name === 'klassisch') setFein(true);
  else setFein(feinOffen());

  if (name === 'mobil') panelsHerstellen();
  else document.querySelectorAll('.mob-zu').forEach(c => c.classList.remove('mob-zu'));

  if (name === 'optimiert') zeitraumKoppeln();
}

/* --- Feinschliff in der Regelungskarte --------------------------- */

function feinOffen() {
  return lesen('gh_fein') === 'auf';
}

function setFein(offen) {
  const gruppe = document.getElementById('regelung-fein');
  const knopf  = document.getElementById('regelung-fein-toggle');
  if (!gruppe || !knopf) return;
  gruppe.classList.toggle('fein-zu', !offen);
  knopf.setAttribute('aria-expanded', offen ? 'true' : 'false');
  knopf.textContent = offen ? 'Feinschliff ausblenden' : 'Feinschliff anzeigen';
}

function toggleFein() {
  const neu = !feinOffen();
  schreiben('gh_fein', neu ? 'auf' : 'zu');
  setFein(neu);
}

/* --- Klappabschnitte der mobilen Ansicht ------------------------- */

function panelSchluessel(card) {
  const label = card.querySelector(':scope > .card-label');
  const text = label ? label.textContent.trim().split('\n')[0].trim() : '';
  return text.slice(0, 32) || 'unbenannt';
}

function panelStatus() {
  try { return JSON.parse(lesen(PANEL_KEY) || '{}'); } catch (e) { return {}; }
}

function panelStatusSetzen(key, offen) {
  const status = panelStatus();
  status[key] = offen;
  schreiben(PANEL_KEY, JSON.stringify(status));
}

/** Klappzustand auf alle vorhandenen Karten anwenden. Wird auch nach dem
 *  Nachladen der Kamera-Abschnitte erneut aufgerufen. */
function panelsHerstellen() {
  if (!document.documentElement.classList.contains('view-mobil')) return;
  const status = panelStatus();
  document.querySelectorAll(COLLAPSIBLE).forEach(card => {
    const key = panelSchluessel(card);
    // Vorgabe: zu. Der obere Bereich mit Messwerten und Lüfter genügt beim
    // Hinschauen; alles Weitere holt man sich mit einem Tipp.
    const offen = status[key] === true;
    card.classList.toggle('mob-zu', !offen);
    const label = card.querySelector(':scope > .card-label');
    if (label && !label.hasAttribute('role')) {
      label.setAttribute('role', 'button');
      label.setAttribute('tabindex', '0');
    }
  });
}

function panelUmschalten(card) {
  const key = panelSchluessel(card);
  const jetztZu = card.classList.toggle('mob-zu');
  panelStatusSetzen(key, !jetztZu);
}

/* --- Ein Zeitraum für alle drei Verläufe ------------------------- */

function zeitraumKoppeln() {
  const alle = document.querySelectorAll('.chart-range');
  if (alle.length < 2) return;
  alle.forEach(sel => {
    if (sel.dataset.gekoppelt) return;
    sel.dataset.gekoppelt = '1';
    sel.addEventListener('change', () => {
      if (!document.documentElement.classList.contains('view-optimiert')) return;
      alle.forEach(anderer => { if (anderer !== sel) anderer.value = sel.value; });
    });
  });
}

/* --- Zielabweichung auf den Innen-Karten ------------------------- */

/** Zeigt "Ziel 24,0 · +2,4 K" unter dem Messwert. Nur eine Ergänzung –
 *  in der klassischen Ansicht blendet die CSS sie aus. */
function renderZiel(id, istWert, sollWert, einheit) {
  const el = document.getElementById(id);
  if (!el) return;
  if (istWert == null || isNaN(istWert) || sollWert == null || isNaN(sollWert)) {
    el.textContent = '';
    return;
  }
  const diff = istWert - sollWert;
  const zahl = typeof formatDE === 'function' ? formatDE(sollWert, 1) : String(sollWert);
  const dz = typeof formatDE === 'function' ? formatDE(Math.abs(diff), 1) : String(Math.abs(diff));

  const delta = document.createElement('span');
  delta.className = 'ziel-delta ' +
    (diff > 0.05 ? 'drueber' : diff < -0.05 ? 'drunter' : 'passt');
  delta.textContent = Math.abs(diff) <= 0.05
    ? 'erreicht'
    : (diff > 0 ? '+' : '−') + dz + ' ' + einheit;

  el.textContent = 'Ziel ' + zahl + ' ';
  el.appendChild(delta);
}

/* --- Start ------------------------------------------------------- */

document.addEventListener('click', ev => {
  const label = ev.target.closest ? ev.target.closest('.card-label') : null;
  if (!label) return;
  if (!document.documentElement.classList.contains('view-mobil')) return;
  // Bedienelemente in der Kopfzeile (z. B. Zeitraumwahl) nicht abfangen.
  if (ev.target.closest('select, input, button, a, label.toggle-switch')) return;
  const card = label.parentElement;
  if (card && card.matches(COLLAPSIBLE)) panelUmschalten(card);
});

document.addEventListener('keydown', ev => {
  if (ev.key !== 'Enter' && ev.key !== ' ') return;
  const label = ev.target.closest ? ev.target.closest('.card-label') : null;
  if (!label || !document.documentElement.classList.contains('view-mobil')) return;
  const card = label.parentElement;
  if (card && card.matches(COLLAPSIBLE)) {
    ev.preventDefault();
    panelUmschalten(card);
  }
});

// Die Kamera-Abschnitte entstehen erst nach dem Laden der Einstellungen.
const beobachter = new MutationObserver(() => panelsHerstellen());

document.addEventListener('DOMContentLoaded', () => {
  wendeAn(aktuelleAnsicht());
  const container = document.getElementById('timelapse-container');
  if (container) beobachter.observe(container, { childList: true });
});
