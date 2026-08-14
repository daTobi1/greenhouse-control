# Design: Timelapse-Zeitplan, Belichtung, Kamera-Erkennung, Regelung

Datum: 2026-08-14
Status: freigegeben

## 1. Kontext

Die Gewächshaus-Steuerung läuft produktiv auf einem Raspberry Pi. Vier Problemfelder
werden in dieser Arbeit adressiert:

1. **Timelapse-Zeitplan** – Aufnahmen sind nur über ein Intervall steuerbar. Gewünscht
   sind feste Uhrzeiten pro Tag sowie ein Intervallmodus mit Startzeit und optionaler
   Endzeit.
2. **Belichtung** – Timelapse-Bilder sind mal zu dunkel, mal zu hell.
3. **Kamera-Erkennung** – Auflösungserkennung liefert unzuverlässige und teils falsche
   Ergebnisse.
4. **Regelung** – Der Feuchtevergleich ist physikalisch falsch, ausgefallene Sensoren
   werden nicht erkannt, und der Lüfter taktet um den Sollwert.

## 2. Umfang

**Im Umfang:**

- Block A: Timelapse-Zeitplan (feste Uhrzeiten / Intervall mit Zeitfenster)
- Block B: Belichtungsstabilisierung inkl. Deflicker beim Compile
- Block C: Kamera- und Auflösungserkennung über v4l2
- Block D: Regelung – absolute Feuchte, Sensor-Ausfallsicherung, Laufruhe
- Test-Setup (pytest) für die reinen Logikmodule

**Nicht im Umfang:**

- Tag/Nacht-Sollwertprofil für die Regelung (separat, später)
- I-Anteil im Regler (bewusst verworfen: bei Abluftlüftung nicht erforderlich)
- Timeout für den manuellen Lüftermodus (separat, später)
- Heartbeat-Einträge in `fan_events` (separat, später)
- Tests für Kamera- und GPIO-Code (Hardware erforderlich)

## 3. Block A – Timelapse-Zeitplan

### 3.1 Verhalten

Pro Kamera gibt es zwei Modi:

**Modus `interval`** – Startzeit, optionale Endzeit, Intervall.

- Ohne Startzeit: `letzte Aufnahme + Intervall` (exakt das bisherige Verhalten).
- Mit Startzeit: festes Raster `start_heute + k · interval`. Dadurch kein Drift über
  den Tag, anders als bisher.
- Mit Endzeit: Fällt der nächste Rasterpunkt hinter die Endzeit, geht es am nächsten
  Tag zur Startzeit weiter.
- Endzeit vor Startzeit (z. B. 22:00–04:00) bedeutet ein über Mitternacht laufendes
  Fenster.

**Modus `times`** – Liste fester Uhrzeiten, täglich wiederholt.

- Nächster Zeitpunkt ist die kleinste Uhrzeit der sortierten Liste, die nach `now`
  liegt; existiert keine, die erste Uhrzeit des Folgetags.
- Leere Liste bedeutet: keine Aufnahmen.

**Verpasste Zeitpunkte verfallen.** Es gibt ein Kulanzfenster (`grace`, Default 300 s).
Liegt der Sollzeitpunkt weiter zurück, wird nicht nachgeholt. Damit trägt jedes Bild
die Tageszeit, für die es geplant war; im Video entsteht stattdessen eine Lücke.

Der Zeitplan wirkt **innerhalb einer laufenden Session**. Start/Stopp bleiben
unverändert: „Starten" legt die Session an und aktiviert die Aufnahme, der Zeitplan
bestimmt nur noch, *wann* innerhalb dieser Session ausgelöst wird.

### 3.2 `services/schedule.py` (neu)

```python
@dataclass(frozen=True)
class ScheduleConfig:
    mode: str                 # "interval" | "times"
    interval_seconds: float
    start: time | None
    end: time | None
    times: tuple[time, ...]
    grace_seconds: float

def parse_config(settings: dict, cam: int) -> ScheduleConfig
def next_due(now: datetime, cfg: ScheduleConfig,
             last_capture: datetime | None) -> datetime | None
def is_due(now: datetime, target: datetime, grace_seconds: float) -> bool
```

`next_due` gibt `None` zurück, wenn kein Zeitpunkt bestimmbar ist (Modus `times` mit
leerer Liste). Der Loop schläft dann bis zum nächsten Settings-Check.

`is_due(now, target, grace)` ist `target <= now <= target + grace`.

Alle Funktionen sind rein: keine DB, keine Kamera, kein `datetime.now()` im Inneren.
`now` wird immer übergeben. Das macht sie vollständig testbar.

**Zeitzone:** Gerechnet wird in lokaler Zeit über naive `datetime`-Objekte. In der
Nacht der Zeitumstellung kann ein Zeitpunkt doppelt oder gar nicht auftreten. Das ist
ein bewusst akzeptierter Kompromiss; das Kulanzfenster fängt den Normalfall ab.

### 3.3 Änderungen in `services/scheduler.py`

`_timelapse_loop` wird umgebaut:

- Statt `await wait_for(wake, timeout=interval)` wird der nächste Sollzeitpunkt über
  `next_due()` berechnet und in Häppchen von **maximal 60 Sekunden** darauf gewartet.
  Damit greifen Einstellungsänderungen spätestens nach einer Minute, auch bei
  Intervallen von mehreren Stunden.
- Nach dem Aufwachen prüft `is_due()`. Liegt der Zeitpunkt außerhalb des
  Kulanzfensters (typisch nach einem Neustart), wird nicht ausgelöst und der nächste
  Zeitpunkt berechnet. Die Verfallslogik braucht damit keinen Sonderfall.
- `last_capture` wird pro Kamera im Loop gehalten; nur der Intervallmodus ohne
  Startzeit nutzt ihn.
- Toter Code in Zeile 143 (`cam = _state.get_camera(i)` ohne Verwendung) entfällt.

### 3.4 Wake-Event pro Kamera

`state.timelapse_wake` ist derzeit **ein** Event für **alle** Kamera-Loops. Ein
`clear()` in Loop 0 kann das Signal für Loop 1 verschlucken. Ersetzt durch:

```python
def get_timelapse_wake(cam: int) -> asyncio.Event
```

Aufrufer in `api/timelapse.py` (`/start`, `/stop`) werden angepasst.

### 3.5 UI

Im Timelapse-Block jeder Kamera ersetzt ein Modus-Umschalter das bisherige
Intervallfeld:

```
Zeitplan   ( ) Intervall   ( ) Feste Uhrzeiten

── Intervall ──────────────      ── Feste Uhrzeiten ────────
Ab Uhrzeit    [06:00]            Bilder pro Tag  [ 3 ]
Bis (optional)[20:00]            1. [08:00]   2. [12:00]
Intervall     [0,5]  h           3. [16:00]
```

- „Ab Uhrzeit" leer bedeutet: Intervall läuft ab Start der Session (Altverhalten).
- „Bis" leer bedeutet: durchlaufend über Mitternacht.
- Ändert der Nutzer „Bilder pro Tag", werden genau so viele Uhrzeitfelder gezeigt.
  Neue Felder werden gleichmäßig über den Tag vorbelegt (bei n Bildern:
  `06:00 + k · 12h/n`, gerundet auf 5 Minuten) und sind frei überschreibbar.
  Bereits gesetzte Uhrzeiten bleiben beim Ändern der Anzahl erhalten.
- Bereich für „Bilder pro Tag": 1–24.
- Unter dem Block steht die nächste geplante Aufnahme als Klartext
  („Nächste Aufnahme: heute 12:00").

Die Werte werden wie bisher beim Klick auf „Starten" gespeichert, zusätzlich beim
Verlassen eines Feldes (`change`), damit der Zeitplan auch während einer laufenden
Session angepasst werden kann.

## 4. Block B – Belichtung

### 4.1 Ursachen

1. `_apply_props()` steigt bei leerem `_cam_props` sofort aus (`camera.py:102`).
   Die Warm-up-Schleife wird damit übersprungen — ohne konfigurierte Properties wird
   **kein einziger** Frame verworfen. Das erste Frame direkt nach dem Öffnen ist bei
   USB-Kameras nahezu immer falsch belichtet.
2. Fünf Warm-up-Frames sind zu wenig. UVC-Kameras brauchen für die Einregelung der
   Auto-Belichtung typisch 15–30 Frames bzw. 1–2 Sekunden. Fünf Frames bei 30 fps sind
   0,17 s — das Bild entsteht mitten im Einregelvorgang.
3. Belichtung wird überhaupt nicht gesteuert: `CAMERA_PROPERTIES` kennt weder
   `CAP_PROP_AUTO_EXPOSURE` (21), `CAP_PROP_EXPOSURE` (15), `CAP_PROP_GAIN` (14),
   `CAP_PROP_BRIGHTNESS` (10) noch `CAP_PROP_GAMMA` (22).
4. Selbst bei perfekter Einregelung schwankt die Helligkeit von Bild zu Bild, weil die
   Kamera für jede Aufnahme neu geöffnet wird (Flicker).

### 4.2 `services/exposure.py` (neu)

```python
def measure_brightness(frame) -> float
    """Mittlere Luma 0..255. Bildmitte doppelt gewichtet, damit ein heller
    Himmel am Bildrand die Messung nicht dominiert."""

def within_tolerance(measured: float, target: float, tolerance: float) -> bool

def correction_factor(measured: float, target: float,
                      damping: float = 0.8) -> float
    """Multiplikator für die Belichtungszeit. Gedämpft, um Überschwingen zu
    vermeiden: 1 + damping * (target/measured - 1). Auf [0.25, 4.0] begrenzt.
    Bei measured <= 1.0 wird der obere Anschlag zurückgegeben."""
```

Reine Funktionen, `measure_brightness` arbeitet auf einem numpy-Array. Testbar ohne
Kamera über synthetische Arrays.

### 4.3 Änderungen in `services/camera.py`

**Warm-up** (`_apply_props`):

- Der Early-Return bei leerem `_cam_props` entfällt. Warm-up läuft immer.
- Statt einer festen Frame-Zahl wird gelesen, bis `warmup_seconds` verstrichen sind
  (Default 1,5 s), begrenzt auf maximal 60 Frames als Schutz gegen hängende Kameras.

**Belichtungsregelung** in `capture_frame()`:

```
1. Kamera öffnen, FOURCC + Auflösung + Properties setzen
2. Warm-up bis warmup_seconds
3. Frame lesen, Helligkeit messen
4. Solange außerhalb der Toleranz und < 3 Iterationen:
     Belichtung mit correction_factor() korrigieren,
     2 Frames verwerfen, neu lesen und messen
5. Das Frame mit der geringsten Abweichung zum Ziel speichern
```

- Bei `target_brightness == 0` entfällt Schritt 3–5 vollständig; es bleibt beim
  verlängerten Warm-up.
- Voraussetzung für die Korrektur ist, dass die Kamera manuelle Belichtung zulässt.
  Geprüft wird einmalig durch Setzen von `CAP_PROP_AUTO_EXPOSURE` auf manuell und
  Rücklesen. Schlägt das fehl, greift der Fallback.
- **Fallback ohne Hardware-Belichtungssteuerung:** Helligkeitskorrektur per
  `cv2.convertScaleAbs(frame, alpha=f, beta=0)` auf dem fertigen Frame, mit `f`
  begrenzt auf `[0.7, 1.3]`. Stärkere Korrekturen werden nicht angewendet, um kein
  Rauschen hochzuziehen; stattdessen wird die Abweichung geloggt.

**Neue Einträge in `CAMERA_PROPERTIES`:**

| key | prop | typ | Bereich |
|---|---|---|---|
| `auto_exposure` | 21 | bool | – |
| `exposure` | 15 | range | wird geprobt, `auto_key: auto_exposure` |
| `gain` | 14 | range | wird geprobt |
| `brightness` | 10 | range | wird geprobt |
| `gamma` | 22 | range | wird geprobt |

Die Bereiche werden wie bei den bestehenden Properties über `detect_properties()`
ermittelt, da sie treiberabhängig sind (V4L2 liefert `exposure` in 100-µs-Schritten,
typisch 1–5000).

### 4.4 UI der Kamera-Einstellungen

Die Regler in `buildCameraSection()` sind derzeit fest verdrahtet (je ein
`<div class="cam-prop-row">` pro Property). Mit fünf neuen Properties wird das
unhandlich. Der Block wird **generisch aus der API-Antwort von
`GET /api/timelapse/camera/properties` gerendert**, die Typ, Bereich, Einheit und
`auto_key` bereits mitliefert. Damit entfällt die Doppelpflege zwischen
`CAMERA_PROPERTIES` und HTML.

Zusätzlich, ausserhalb der Property-Liste:

```
Ziel-Helligkeit   ──────●────   120     (0 = Regelung aus)
Toleranz          ───●───────    12
Aufwärmzeit       ──●────────   1,5 s
```

### 4.5 Deflicker beim Compile

Im Standbild-Modus wird der Filter erweitert:

```
-vf "fps={fps},deflicker=size=7:mode=am"
```

Neue globale Einstellung `timelapse_deflicker` (Default `true`), abschaltbar in den
Einstellungen. Im Clip-Modus wird per Stream-Copy zusammengefügt; dort ist der Filter
nicht anwendbar und wird ignoriert.

## 5. Block C – Kamera- und Auflösungserkennung

### 5.1 Ursachen

1. **FOURCC wird nie gesetzt.** USB-Webcams liefern hohe Auflösungen fast
   ausschließlich als MJPG. OpenCV öffnet mit V4L2 standardmäßig in YUYV, wo die
   Bandbreite von USB 2.0 die meisten Kameras auf 640×480 begrenzt. Ohne
   `CAP_PROP_FOURCC` findet die Erkennung bei einer 1080p-Kamera nur VGA. Betrifft
   auch `capture_frame` und `capture_clip`: dort wird eine hohe Auflösung gesetzt, die
   die Kamera in YUYV nicht liefern kann — der Treiber snapped stumm auf etwas anderes.
2. **Es wird kein Frame gelesen.** Viele V4L2-Treiber geben bei
   `cap.get(CAP_PROP_FRAME_WIDTH)` den gesetzten Wunschwert zurück, ohne je ein Bild in
   dieser Auflösung geliefert zu haben. Die Prüfung `(aw, ah) == (w, h)` erzeugt damit
   falsch positive Einträge.
3. **Zustandsabhängigkeit.** Breite und Höhe werden nacheinander gesetzt. Nach
   `set(WIDTH, 1920)` steht die Kamera kurz auf 1920×480 — ungültig, der Treiber
   snapped. Das Ergebnis eines Tests hängt dadurch von der zuvor getesteten Auflösung
   ab. Das erklärt „mal wird es erkannt, mal nicht" bei unveränderter Hardware.
4. **Kein Lock.** `detect_cameras`, `detect_resolutions`, `detect_fps`,
   `detect_properties`, `capture_preview` und `capture_frame` öffnen dieselbe Kamera;
   V4L2 vergibt exklusiv. Erkennung während einer Aufnahme liefert eine leere Liste,
   Aufnahme während einer Erkennung fällt aus. `initCameraSections()` startet zudem bei
   jedem Dashboard-Laden einen Scan.
5. **Blindes Scannen von Index 0–9.** Ein USB-Gerät hat auf dem Pi meist mehrere
   `/dev/video*`-Nodes (Capture + Metadata, bei H.264-Kameras bis zu vier). Dieselbe
   Kamera erscheint mehrfach, teils unter Indizes, die beim Öffnen scheitern. Das
   Öffnen von 10 Indizes dauert auf dem Pi mehrere Sekunden.
6. **`detect_fps`** hat dieselben Probleme; zusätzlich ist `CAP_PROP_FPS` bei vielen
   Treibern nicht setzbar und liefert 0. FPS hängt von Format *und* Auflösung ab.

### 5.2 `services/v4l2.py` (neu)

`v4l-utils` wird von `install.sh:137` bereits mitinstalliert.

```python
def available() -> bool
    """True, wenn v4l2-ctl im PATH ist."""

def list_devices() -> list[dict]
    """[{index, device, name}] aus /sys/class/video4linux/video*/.
    Gefiltert auf echte Capture-Nodes: nur Nodes, deren
    /sys/class/video4linux/videoN/index den Wert 0 hat.
    name kommt aus /sys/class/video4linux/videoN/name."""

def list_formats(device: str) -> list[dict]
    """[{fourcc, width, height, fps: [30.0, 15.0]}] durch Parsen von
    `v4l2-ctl --list-formats-ext -d <device>`."""
```

`v4l2-ctl --list-formats-ext` liest die Format-Matrix direkt aus dem Treiber, ohne
Streaming zu starten. Das ist schnell (Millisekunden), exakt, frei von Snap-Effekten
und kollidiert nicht mit einer laufenden Aufnahme.

Aufrufe laufen über `subprocess.run` mit Timeout (5 s) und werden bei Fehler zu einer
leeren Liste, damit der Fallback greift.

### 5.3 Umbau in `services/camera.py`

- `detect_cameras()`: primär `v4l2.list_devices()`. Rückgabe wie bisher
  `[{index, name}]`, aber mit echtem Gerätenamen statt „Kamera N".
- `detect_resolutions(index)`: primär aus `v4l2.list_formats()`, dedupliziert über
  alle Formate, absteigend nach Pixelzahl sortiert. Der Filter gegen
  `COMMON_RESOLUTIONS` entfällt — die Treiberliste ist die Wahrheit, inklusive krummer
  Formate.
- `detect_fps(index, w, h)`: die FPS-Werte, die v4l2 für genau diese Auflösung meldet.
- **OpenCV-Fallback** (Windows-Entwicklungsmaschine, fehlendes `v4l2-ctl`) wird
  korrigiert: FOURCC zuerst, dann Breite, dann Höhe, dann `read()` und Prüfung gegen
  `frame.shape` statt gegen `cap.get()`.
- Ergebnisse werden pro Geräteindex in einem Modul-Cache gehalten. Invalidierung über
  den Refresh-Knopf im Dashboard (neuer Query-Parameter `refresh=1`).

### 5.4 Gerätelock

Ein `threading.Lock` pro **Geräteindex** — nicht pro Kamera-Slot, da zwei Slots auf
dasselbe Gerät zeigen können. Zentral in `state.py`:

```python
def device_lock(device_index: int) -> threading.Lock
```

Alle Zugriffe, die `cv2.VideoCapture` öffnen, laufen hindurch:
`capture_frame`, `capture_clip`, `capture_preview`, `detect_properties` und der
OpenCV-Fallback der Erkennung.

- **Aufnahme** wartet blockierend (die Erkennung ist kurz).
- **Erkennung und Vorschau** nehmen den Lock mit 2 s Timeout. Bei Belegung liefert die
  API HTTP 409 mit „Kamera gerade in Benutzung" statt einer leeren Liste; das Frontend
  zeigt eine Meldung und behält die bisherige Auswahl bei, statt auf
  „Kamera Standard" zurückzufallen.

`v4l2.list_formats()` braucht den Lock nicht, da es nicht streamt.

### 5.5 Format als Einstellung

Neu: `cam_X_fourcc`, Default `"MJPG"`. Auswahl im Dashboard aus den von v4l2
gemeldeten Formaten. Wird in `capture_frame`, `capture_clip` und `capture_preview`
**vor** Breite und Höhe gesetzt:

```python
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
```

Nebeneffekt: MJPG öffnet bei hoher Auflösung deutlich schneller als YUYV, was dem
Belichtungs-Warm-up zugutekommt.

## 6. Block D – Regelung

### 6.1 `services/psychrometrics.py` (neu)

```python
def abs_humidity(temp_c: float, rel_hum: float) -> float
    """Absolute Feuchte in g/m³ (Magnus).
    6.112 * exp(17.67*T/(T+243.5)) * rh * 2.1674 / (273.15 + T)"""

def dew_point(temp_c: float, rel_hum: float) -> float
    """Taupunkt in °C (Magnus).
    alpha = ln(rh/100) + 17.67*T/(T+243.5)
    Td = 243.5*alpha / (17.67 - alpha)"""
```

`rel_hum <= 0` wird auf 0,1 % angehoben, damit der Logarithmus definiert bleibt.

### 6.2 Änderungen in `fan_controller.calculate_speed()`

**Feuchtevergleich über absolute Feuchte.** Bisher wird relative Feuchte verglichen
(`o_hum < i_hum`), was physikalisch falsch ist: Innen 25 °C / 70 % rF entspricht
16,1 g/m³, außen 10 °C / 90 % rF nur 8,5 g/m³ — Lüften entfeuchtet also deutlich,
wird von der bisherigen Logik aber verboten. Neu:

```
o_abs < i_abs - humidity_abs_margin
```

`humidity_abs_margin` (Default 0,5 g/m³) verhindert Reagieren auf Messrauschen.

**Feuchtelüftung kühlt nicht mehr gegen den Sollwert.** Der Feuchtezweig ist nur aktiv,
solange `i_temp > target_temperature - humidity_temp_guard` (Default 3,0 °C). Bisher
fing das nur der Frostschutz bei 5 °C ab.

**Echte Hysterese.** `fan_deadband` wird durch zwei Schwellen auf dem Rohwert ersetzt:

- Einschalten: `raw >= fan_start_threshold` (Default 0,10)
- Ausschalten: `raw <= fan_stop_threshold` (Default 0,03)

Bisher wurde erst bei `raw <= 0` abgeschaltet, also exakt am Sollwert — kombiniert mit
dem Sprung von 0 auf `fan_min` erzeugte das Ein/Aus-Flattern im Minutentakt.

**Mindestlaufzeit und Mindestpause.** `fan_min_runtime` (Default 120 s) und
`fan_min_pause` (Default 60 s). Der Controller merkt sich den Zeitpunkt des letzten
Zustandswechsels über `time.monotonic()`. Ein Wechsel wird unterdrückt, solange die
Mindestdauer nicht erreicht ist. Ausnahmen, die sofort greifen: Frostschutz und
Failsafe bei veralteten Sensordaten.

**Kickstart.** Beim Übergang von 0 auf aktiv wird für `fan_kickstart_duration`
(Default 0,6 s) auf 100 % gefahren, danach auf die Zieldrehzahl. Viele DC-Lüfter laufen
bei 20 % PWM aus dem Stillstand nicht an. Umgesetzt in `FanController.set_speed()`
(nicht im Scheduler), damit es auch im manuellen Modus greift. Der Puls läuft in einem
eigenen Thread, um den Event-Loop nicht zu blockieren.

**Frostschutz mit Hysterese.** Aus unterhalb `fan_min_temperature`, wieder frei ab
`fan_min_temperature + 1,0 °C`.

### 6.3 Sensor-Ausfallsicherung

**Aktuelles Verhalten:** `get_sensor_data()` liefert den zuletzt empfangenen Wert ohne
Altersprüfung. Bei leerer Batterie oder hängendem BLE-Stack regelt die Steuerung
unbegrenzt auf einem eingefrorenen Messwert weiter. Der `timestamp` wird zwar
geschrieben, aber nie ausgewertet.

**Neu** in `services/switchbot.py`:

```python
def get_sensor_data(self, role: str, max_age_s: float | None = None) -> dict | None
```

Mit `max_age_s` wird `None` zurückgegeben, wenn `timestamp` älter ist. Ohne den
Parameter bleibt das Verhalten unverändert (Rückwärtskompatibilität für bestehende
Aufrufer in `api/sensors.py`).

Neue Einstellung `sensor_max_age` (Default 300 s).

Im `_fan_loop`:

- Innensensor veraltet oder nicht vorhanden → `set_speed(0)`, `reason="sensor_stale"`.
- Außensensor veraltet oder nicht vorhanden → wie bisher keine Lüftung, aber
  `reason="no_outside_data"` statt stummer Blockade.

Beide Zustände werden über `/api/fans/status` als `blocked_reason` ausgeliefert und im
Dashboard als Warnung angezeigt.

### 6.4 API-Erweiterungen

- `GET /api/sensors/current`: zusätzlich `age_seconds`, `stale`, `abs_humidity`,
  `dew_point` pro Rolle.
- `GET /api/fans/status`: zusätzlich `blocked_reason`
  (`null | "sensor_stale" | "no_outside_data" | "frost" | "disabled"`).

Im Dashboard werden absolute Feuchte und Taupunkt bei den Sensorwerten mit angezeigt,
da sie für das Verständnis der Regelentscheidung nötig sind.

## 7. Datenmodell

Alle neuen Schlüssel liegen als JSON in der bestehenden `settings`-Tabelle. **Keine
Schema-Migration erforderlich.**

### Pro Kamera (X = 0…3)

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `cam_X_schedule_mode` | `"interval"` | `interval` \| `times` |
| `cam_X_schedule_start` | `null` | `"HH:MM"`, Startzeit im Intervallmodus |
| `cam_X_schedule_end` | `null` | `"HH:MM"`, optionale Endzeit |
| `cam_X_schedule_times` | `[]` | `["08:00","12:00"]` |
| `cam_X_schedule_grace` | `300` | Kulanzfenster in Sekunden |
| `cam_X_target_brightness` | `120` | 0 = Regelung aus |
| `cam_X_brightness_tol` | `12` | Toleranzband ± |
| `cam_X_warmup_seconds` | `1.5` | Aufwärmzeit vor der Aufnahme |
| `cam_X_fourcc` | `"MJPG"` | Pixelformat |
| `cam_X_prop_auto_exposure` | – | wie bestehende `cam_X_prop_*` |
| `cam_X_prop_exposure` | – | |
| `cam_X_prop_gain` | – | |
| `cam_X_prop_brightness` | – | |
| `cam_X_prop_gamma` | – | |

### Global

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `timelapse_deflicker` | `true` | Deflicker-Filter beim Compile |
| `sensor_max_age` | `300` | Sekunden, ab wann Messwerte als veraltet gelten |
| `humidity_abs_margin` | `0.5` | g/m³ Mindestunterschied für Feuchtelüftung |
| `humidity_temp_guard` | `3.0` | °C unter Ziel, ab da keine Feuchtelüftung |
| `fan_start_threshold` | `0.10` | Rohwert zum Einschalten |
| `fan_stop_threshold` | `0.03` | Rohwert zum Ausschalten |
| `fan_min_runtime` | `120` | Sekunden Mindestlaufzeit |
| `fan_min_pause` | `60` | Sekunden Mindestpause |
| `fan_kickstart_duration` | `0.6` | Sekunden 100-%-Anlaufpuls, 0 = aus |

`fan_deadband` bleibt in der Datenbank bestehen, wird aber nicht mehr gelesen. Beim
Seeding wird `fan_start_threshold` einmalig aus einem vorhandenen `fan_deadband`
übernommen, falls dieser vom Default abweicht.

## 8. Abwärtskompatibilität

Bestehende Installationen ändern ihr Verhalten nicht:

- Fehlt `cam_X_schedule_mode`, gilt `"interval"` ohne Start- und Endzeit — das ist
  exakt die bisherige Logik `letzte Aufnahme + Intervall`.
- `cam_X_target_brightness` mit Default 120 aktiviert die Belichtungsregelung.
  Das ist eine **gewollte** Verhaltensänderung; sie lässt sich mit 0 abschalten.
- `cam_X_fourcc` mit Default MJPG ist eine gewollte Verhaltensänderung. Meldet die
  Kamera MJPG nicht, wird ohne FOURCC geöffnet (Altverhalten).
- Die Regelungsänderungen ändern das Verhalten gewollt. Der Feuchtezweig wird dadurch
  in vielen Situationen erstmals aktiv, in denen er zuvor blockiert war.

## 9. Neue und geänderte Dateien

**Neu:**

| Datei | Inhalt |
|---|---|
| `services/schedule.py` | `ScheduleConfig`, `parse_config`, `next_due`, `is_due` |
| `services/exposure.py` | `measure_brightness`, `within_tolerance`, `correction_factor` |
| `services/v4l2.py` | `available`, `list_devices`, `list_formats` |
| `services/psychrometrics.py` | `abs_humidity`, `dew_point` |
| `tests/` | pytest-Suite |
| `pytest.ini` | Test-Konfiguration |
| `requirements-dev.txt` | `pytest`, `numpy` — nicht auf dem Pi installiert |

**Geändert:**

| Datei | Änderung |
|---|---|
| `services/scheduler.py` | Zeitplan-Trigger, Wake-Event pro Kamera, toter Code raus |
| `services/camera.py` | Warm-up, Belichtungsregelung, FOURCC, v4l2-Erkennung, Deflicker |
| `services/fan_controller.py` | absolute Feuchte, Hysterese, Mindestzeiten, Kickstart |
| `services/switchbot.py` | `max_age_s` in `get_sensor_data` |
| `state.py` | `get_timelapse_wake`, `device_lock` |
| `db/database.py` | neue Defaults, Übernahme `fan_deadband` → `fan_start_threshold` |
| `api/timelapse.py` | Zeitplan-Felder im Status, 409 bei belegter Kamera, `refresh`-Parameter |
| `api/settings.py` | `cam_X_fourcc` in das Re-Setup-Muster aufnehmen |
| `api/sensors.py` | `age_seconds`, `stale`, `abs_humidity`, `dew_point` |
| `api/fans.py` | `blocked_reason` |
| `static/index.html` | Einstellungen für Deflicker und Sensor-Alter |
| `static/js/app.js` | Zeitplan-UI, generische Property-Regler, Fehlerbehandlung 409 |
| `static/css/style.css` | Styles für Zeitplan-Block |

`api/settings.py` prüft in `cam_keys_pattern` derzeit nur
`device_index|capture_width|capture_height`. `fourcc` muss ergänzt werden, sonst greift
ein geändertes Pixelformat erst nach einem Neustart.

## 10. Tests

Getestet werden die reinen Logikmodule — dort sitzt das Risiko und dort ist keine
Hardware nötig. Kamera-, GPIO- und BLE-Code bleiben ungetestet.

**`tests/test_schedule.py`**

- `times`: nächster Zeitpunkt heute; nach der letzten Uhrzeit → erste Uhrzeit morgen;
  leere Liste → `None`; unsortierte Eingabe wird sortiert.
- `interval` ohne Start: `last + interval`; ohne `last` → sofort fällig.
- `interval` mit Start: Raster ab Startzeit, kein Drift über viele Schritte;
  vor der Startzeit → Startzeit heute.
- `interval` mit Ende: letzter Punkt vor Ende; danach → Startzeit morgen.
- Fenster über Mitternacht (`start > end`).
- `is_due`: innerhalb, exakt am Rand und außerhalb des Kulanzfensters.
- Verfall: Sollzeitpunkt 2 h in der Vergangenheit ist nicht fällig.

**`tests/test_psychrometrics.py`**

- `abs_humidity` gegen bekannte Tabellenwerte (20 °C/50 % ≈ 8,65 g/m³;
  25 °C/70 % ≈ 16,1 g/m³), Toleranz 2 %.
- `dew_point` gegen Tabellenwerte (20 °C/50 % ≈ 9,3 °C), Toleranz 0,3 °C.
- Randfälle: 100 % rF → Taupunkt = Temperatur; 0 % rF wirft nicht.
- Negative Temperaturen.

**`tests/test_exposure.py`**

- `measure_brightness` auf konstanten Arrays (0, 128, 255).
- Mittengewichtung: helles Zentrum ergibt höheren Wert als heller Rand.
- `correction_factor`: zu dunkel → > 1; zu hell → < 1; auf Ziel → ≈ 1;
  Begrenzung auf `[0.25, 4.0]`; `measured == 0` wirft nicht.

**`tests/test_fan_controller.py`**

- Temperaturzweig: Außen wärmer → 0; Außen kälter → proportional.
- Feuchtezweig: relative Feuchte außen höher, absolute niedriger → lüftet
  (der Fall, den die alte Logik falsch behandelt hat).
- Feuchtezweig blockiert unterhalb `target_temperature - humidity_temp_guard`.
- Hysterese: Einschalten erst ab `fan_start_threshold`, Ausschalten erst bei
  `fan_stop_threshold`, dazwischen Zustandserhalt.
- Mindestlaufzeit verhindert vorzeitiges Abschalten; Frostschutz durchbricht sie.
- Frostschutz-Hysterese: aus bei 4,9 °C, bleibt aus bei 5,5 °C, ein ab 6,1 °C.
- `combined_and` nimmt das Minimum, `combined_or` das Maximum.
- Skalierung in `[fan_min, fan_max]`.

**`tests/test_v4l2.py`**

- Parser gegen eine eingecheckte Beispielausgabe von `v4l2-ctl --list-formats-ext`
  (Textfixture, kein Subprozess).
- Fehlendes `v4l2-ctl` → `available()` ist `False`, `list_formats()` ist leer.

Der Zeitpunkt wird in allen Tests injiziert, nie aus `datetime.now()` gelesen.

## 11. Bewusst akzeptierte Kompromisse

- **Zeitumstellung:** In der Umstellungsnacht kann ein geplanter Zeitpunkt doppelt
  oder gar nicht auftreten. Der Aufwand für zeitzonenbewusstes Rechnen steht in keinem
  Verhältnis zum Nutzen bei zwei Nächten im Jahr.
- **Kein Nachholen verpasster Aufnahmen:** bewusste Entscheidung zugunsten korrekter
  Tageszeiten pro Bild.
- **Software-Helligkeitskorrektur auf ±30 % begrenzt:** stärkere Korrekturen würden
  Rauschen sichtbar anheben. Kameras ohne steuerbare Belichtung bleiben damit
  eingeschränkt.
- **Kein I-Anteil im Regler:** bleibende Regelabweichung wird in Kauf genommen. Bei
  einem Abluftlüfter mit passiver Zuluft ist der Effekt gering.
- **Detection-Cache:** Wird die Kamera im laufenden Betrieb getauscht, zeigt das
  Dashboard alte Werte bis zum Klick auf den Refresh-Knopf.

## 12. Reihenfolge der Umsetzung

Die vier Blöcke sind weitgehend unabhängig. Sinnvolle Reihenfolge:

1. **Test-Setup** — Grundlage für alles Weitere.
2. **Block C (Kamera-Erkennung)** — FOURCC und Gerätelock sind Voraussetzung dafür,
   dass die Belichtungsmessung überhaupt stabile Bedingungen vorfindet.
3. **Block B (Belichtung)** — baut auf C auf, beide ändern `capture_frame`.
4. **Block A (Zeitplan)** — unabhängig, größter UI-Anteil.
5. **Block D (Regelung)** — vollständig unabhängig von A–C.
