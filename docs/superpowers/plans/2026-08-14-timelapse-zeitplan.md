# Timelapse-Zeitplan – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Timelapse-Aufnahmen nach festen Uhrzeiten oder nach einem Intervall innerhalb eines täglichen Zeitfensters auslösen.

**Architecture:** Die gesamte Zeitlogik liegt in `services/schedule.py` als reine Funktionen — `now` wird immer hineingereicht, nie intern gelesen, damit alles ohne Uhr und ohne Hardware testbar ist. Der Timelapse-Loop berechnet den nächsten Sollzeitpunkt statt blind ein Intervall abzuschlafen und wartet in Häppchen von maximal 60 Sekunden darauf. Verpasste Zeitpunkte verfallen ohne Sonderfall: nach dem Aufwachen liegen sie außerhalb des Kulanzfensters und werden übersprungen. Der Plan ist maximal 24 Uhrzeiten pro Kamera groß und passt als JSON in die bestehende `settings`-Tabelle.

**Tech Stack:** Python 3.14, asyncio, FastAPI, aiosqlite, Vanilla JS

**Spec:** `docs/superpowers/specs/2026-08-14-timelapse-zeitplan-belichtung-regelung-design.md` (Block A, Abschnitt 3)

## Global Constraints

- Keine Schema-Migration. Alle neuen Schlüssel liegen als JSON in der bestehenden `settings`-Tabelle.
- Fehlt `cam_X_schedule_mode`, gilt `"interval"` ohne Start- und Endzeit — exakt das bisherige Verhalten `letzte Aufnahme + Intervall`. Bestehende Installationen ändern sich nicht.
- Gerechnet wird in lokaler Zeit über naive `datetime`-Objekte. Bei der Zeitumstellung kann ein Zeitpunkt doppelt oder gar nicht auftreten; das ist ein akzeptierter Kompromiss.
- Verpasste Zeitpunkte werden **nicht** nachgeholt. Jedes Bild trägt die Tageszeit, für die es geplant war.
- Reine Logik enthält kein `datetime.now()`, keine DB und keine Kamera.
- UI-Texte sind Deutsch, ohne Emojis. Dezimaltrennzeichen im UI ist das Komma (`parseDE`/`formatDE` existieren bereits in `app.js`).
- Testkommandos stehen als `python -m pytest`. Konkret unter Windows `venv/Scripts/python.exe -m pytest`, auf dem Pi `venv/bin/python -m pytest`.
- Dieser Plan setzt das Test-Setup aus Task 1 des Plans `2026-08-14-kamera-erkennung-und-belichtung.md` voraus (`pytest.ini`, `conftest.py`, `requirements-dev.txt`). Existiert es noch nicht, zuerst jene Task ausführen.
- Jede Task endet mit einem Commit. Commit-Nachrichten Englisch, Conventional Commits.

---

### Task 1: Zeitplan-Konfiguration lesen

**Files:**
- Create: `services/schedule.py`
- Create: `tests/test_schedule_config.py`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `schedule.ScheduleConfig` — eingefrorene Dataclass mit `mode: str`, `interval_seconds: float`, `start: time | None`, `end: time | None`, `times: tuple[time, ...]`, `grace_seconds: float`
  - `schedule.parse_time(value) -> time | None`
  - `schedule.parse_config(settings: dict, cam: int) -> ScheduleConfig`

`parse_config` bringt einen eigenen kleinen Fallback-Helfer für die alten, kameraunabhängigen Schlüssel mit, statt einen aus `camera.py` zu importieren. Die sechs Zeilen Dopplung sind der Preis dafür, dass `schedule.py` keine Abhängigkeit zum Kameramodul hat.

- [ ] **Step 1: Failing test schreiben**

`tests/test_schedule_config.py`:

```python
from datetime import time

from services import schedule


def test_defaults_match_legacy_behaviour():
    """Ohne jede Zeitplan-Einstellung gilt reiner Intervallbetrieb."""
    cfg = schedule.parse_config({}, 0)
    assert cfg.mode == "interval"
    assert cfg.start is None
    assert cfg.end is None
    assert cfg.times == ()
    assert cfg.interval_seconds == 300
    assert cfg.grace_seconds == 300


def test_reads_per_camera_keys():
    settings = {
        "cam_1_schedule_mode": "times",
        "cam_1_schedule_times": ["12:00", "08:00"],
        "cam_1_schedule_grace": 60,
        "cam_1_timelapse_interval": 900,
    }
    cfg = schedule.parse_config(settings, 1)
    assert cfg.mode == "times"
    assert cfg.times == (time(8, 0), time(12, 0)), "Uhrzeiten müssen sortiert sein"
    assert cfg.grace_seconds == 60
    assert cfg.interval_seconds == 900


def test_camera_zero_falls_back_to_legacy_interval():
    cfg = schedule.parse_config({"timelapse_interval": 1800}, 0)
    assert cfg.interval_seconds == 1800


def test_legacy_interval_not_used_for_other_cameras():
    cfg = schedule.parse_config({"timelapse_interval": 1800}, 2)
    assert cfg.interval_seconds == 300


def test_parses_start_and_end():
    cfg = schedule.parse_config(
        {"cam_0_schedule_start": "06:00", "cam_0_schedule_end": "20:30"}, 0
    )
    assert cfg.start == time(6, 0)
    assert cfg.end == time(20, 30)


def test_empty_string_is_no_time():
    cfg = schedule.parse_config({"cam_0_schedule_start": ""}, 0)
    assert cfg.start is None


def test_invalid_times_are_dropped():
    cfg = schedule.parse_config(
        {"cam_0_schedule_mode": "times", "cam_0_schedule_times": ["08:00", "quatsch", "", "25:00"]},
        0,
    )
    assert cfg.times == (time(8, 0),)


def test_duplicate_times_collapse():
    cfg = schedule.parse_config(
        {"cam_0_schedule_mode": "times", "cam_0_schedule_times": ["08:00", "08:00"]}, 0
    )
    assert cfg.times == (time(8, 0),)


def test_parse_time_accepts_seconds():
    assert schedule.parse_time("06:30:15") == time(6, 30, 15)


def test_parse_time_rejects_nonsense():
    assert schedule.parse_time(None) is None
    assert schedule.parse_time(42) is None
    assert schedule.parse_time("6") is None
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_schedule_config.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'services.schedule'`

- [ ] **Step 3: Implementieren**

`services/schedule.py`:

```python
"""Zeitplanung für Timelapse-Aufnahmen.

Reine Funktionen: `now` wird immer übergeben, nie intern gelesen. Dadurch ist
die gesamte Logik ohne Uhr, ohne Datenbank und ohne Kamera testbar.

Es wird in lokaler Zeit mit naiven datetime-Objekten gerechnet. Bei der
Zeitumstellung kann ein geplanter Zeitpunkt doppelt oder gar nicht auftreten –
bewusst akzeptiert, das Kulanzfenster fängt den Normalfall ab.
"""

from dataclasses import dataclass
from datetime import time

MODE_INTERVAL = "interval"
MODE_TIMES = "times"

DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_GRACE_SECONDS = 300.0

# Mehr als 24 Aufnahmen pro Tag über feste Uhrzeiten zu pflegen ist im
# Dashboard nicht mehr sinnvoll bedienbar.
MAX_TIMES = 24


@dataclass(frozen=True)
class ScheduleConfig:
    mode: str
    interval_seconds: float
    start: time | None
    end: time | None
    times: tuple[time, ...]
    grace_seconds: float


def parse_time(value) -> time | None:
    """"HH:MM" oder "HH:MM:SS" in ein time-Objekt. Sonst None."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None
    try:
        return time(*numbers)
    except ValueError:
        return None


def parse_config(settings: dict, cam: int) -> ScheduleConfig:
    """Zeitplan einer Kamera aus den Settings lesen.

    Kamera 0 fällt für das Intervall auf den alten, kameraunabhängigen
    Schlüssel zurück, damit bestehende Installationen ihre Einstellung behalten.
    """

    def cam_get(name: str, default, legacy: str | None = None):
        key = f"cam_{cam}_{name}"
        if settings.get(key) is not None:
            return settings[key]
        if cam == 0 and legacy is not None and settings.get(legacy) is not None:
            return settings[legacy]
        return default

    mode = str(cam_get("schedule_mode", MODE_INTERVAL))
    if mode not in (MODE_INTERVAL, MODE_TIMES):
        mode = MODE_INTERVAL

    raw_times = cam_get("schedule_times", [])
    parsed = {parse_time(v) for v in raw_times} if isinstance(raw_times, list) else set()
    parsed.discard(None)
    times = tuple(sorted(parsed))[:MAX_TIMES]

    return ScheduleConfig(
        mode=mode,
        interval_seconds=float(
            cam_get("timelapse_interval", DEFAULT_INTERVAL_SECONDS, "timelapse_interval")
        ),
        start=parse_time(cam_get("schedule_start", None)),
        end=parse_time(cam_get("schedule_end", None)),
        times=times,
        grace_seconds=float(cam_get("schedule_grace", DEFAULT_GRACE_SECONDS)),
    )
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_schedule_config.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add services/schedule.py tests/test_schedule_config.py
git commit -m "feat(schedule): parse timelapse schedule config from settings"
```

---

### Task 2: Nächster Zeitpunkt im Modus „feste Uhrzeiten"

**Files:**
- Modify: `services/schedule.py`
- Create: `tests/test_schedule_times.py`

**Interfaces:**
- Consumes: `ScheduleConfig` aus Task 1
- Produces: `schedule.next_due(now: datetime, cfg: ScheduleConfig, last_capture: datetime | None) -> datetime | None` — in dieser Task nur der Zweig `MODE_TIMES`

- [ ] **Step 1: Failing test schreiben**

`tests/test_schedule_times.py`:

```python
from datetime import datetime, time

from services import schedule


def _cfg(times, grace=300.0):
    return schedule.ScheduleConfig(
        mode=schedule.MODE_TIMES,
        interval_seconds=300.0,
        start=None,
        end=None,
        times=tuple(times),
        grace_seconds=grace,
    )


def test_next_time_today():
    cfg = _cfg([time(8, 0), time(12, 0), time(16, 0)])
    now = datetime(2026, 8, 14, 9, 30)
    assert schedule.next_due(now, cfg, None) == datetime(2026, 8, 14, 12, 0)


def test_first_time_of_day_before_all():
    cfg = _cfg([time(8, 0), time(12, 0)])
    now = datetime(2026, 8, 14, 5, 0)
    assert schedule.next_due(now, cfg, None) == datetime(2026, 8, 14, 8, 0)


def test_rolls_over_to_next_day():
    cfg = _cfg([time(8, 0), time(16, 0)])
    now = datetime(2026, 8, 14, 20, 0)
    assert schedule.next_due(now, cfg, None) == datetime(2026, 8, 15, 8, 0)


def test_exactly_on_a_scheduled_time_returns_the_next_one():
    """Nach dem Auslösen darf derselbe Zeitpunkt nicht erneut geliefert werden."""
    cfg = _cfg([time(8, 0), time(12, 0)])
    now = datetime(2026, 8, 14, 8, 0)
    assert schedule.next_due(now, cfg, None) == datetime(2026, 8, 14, 12, 0)


def test_single_time_repeats_daily():
    cfg = _cfg([time(12, 0)])
    assert schedule.next_due(datetime(2026, 8, 14, 13, 0), cfg, None) == datetime(2026, 8, 15, 12, 0)


def test_empty_times_yields_none():
    assert schedule.next_due(datetime(2026, 8, 14, 9, 0), _cfg([]), None) is None


def test_last_capture_is_irrelevant_in_times_mode():
    cfg = _cfg([time(8, 0), time(12, 0)])
    now = datetime(2026, 8, 14, 9, 30)
    stale = datetime(2026, 8, 1, 3, 0)
    assert schedule.next_due(now, cfg, stale) == datetime(2026, 8, 14, 12, 0)


def test_crosses_month_boundary():
    cfg = _cfg([time(8, 0)])
    now = datetime(2026, 8, 31, 23, 0)
    assert schedule.next_due(now, cfg, None) == datetime(2026, 9, 1, 8, 0)
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_schedule_times.py -v`
Expected: FAIL mit `AttributeError: module 'services.schedule' has no attribute 'next_due'`

- [ ] **Step 3: Implementieren**

In `services/schedule.py` den Import ergänzen und die Funktionen anhängen:

```python
from datetime import datetime, time, timedelta
```

```python
def next_due(
    now: datetime,
    cfg: ScheduleConfig,
    last_capture: datetime | None,
) -> datetime | None:
    """Nächster Aufnahmezeitpunkt, oder None wenn keiner bestimmbar ist.

    Es wird nie ein Zeitpunkt in der Vergangenheit geliefert; verpasste
    Aufnahmen verfallen dadurch von selbst.
    """
    if cfg.mode == MODE_TIMES:
        return _times_next(now, cfg)
    return _interval_next(now, cfg, last_capture)


def _times_next(now: datetime, cfg: ScheduleConfig) -> datetime | None:
    if not cfg.times:
        return None
    for t in cfg.times:
        candidate = datetime.combine(now.date(), t)
        if candidate > now:
            return candidate
    return datetime.combine(now.date() + timedelta(days=1), cfg.times[0])
```

Für diese Task genügt ein Platzhalter, den Task 3 ersetzt:

```python
def _interval_next(now, cfg, last_capture):
    return None
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_schedule_times.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add services/schedule.py tests/test_schedule_times.py
git commit -m "feat(schedule): resolve next capture time for fixed daily times"
```

---

### Task 3: Nächster Zeitpunkt im Intervallmodus und Fälligkeitsprüfung

**Files:**
- Modify: `services/schedule.py`
- Create: `tests/test_schedule_interval.py`

**Interfaces:**
- Consumes: `next_due` aus Task 2
- Produces:
  - vollständiger `_interval_next`
  - `schedule.is_due(now: datetime, target: datetime, grace_seconds: float) -> bool`

Der Kern ist ein festes Raster `start + k · interval` statt `letzte Aufnahme + interval`. Damit wandern die Aufnahmezeiten nicht mehr über den Tag, weil die Dauer der Aufnahme selbst nicht mehr aufaddiert wird.

Das Verfahren prüft die Tagesversätze −1, 0 und +1. Der Versatz −1 ist für Fenster über Mitternacht nötig: steht die Uhr auf 02:10 und das Fenster ist 22:00–04:00, dann gehört der aktuelle Zeitpunkt zum Fenster, das **gestern** um 22:00 begonnen hat.

- [ ] **Step 1: Failing test schreiben**

`tests/test_schedule_interval.py`:

```python
from datetime import datetime, time, timedelta

from services import schedule


def _cfg(interval=1800.0, start=None, end=None, grace=300.0):
    return schedule.ScheduleConfig(
        mode=schedule.MODE_INTERVAL,
        interval_seconds=interval,
        start=start,
        end=end,
        times=(),
        grace_seconds=grace,
    )


# --- ohne Startzeit: Altverhalten ---

def test_without_start_uses_last_capture():
    cfg = _cfg(interval=1800.0)
    last = datetime(2026, 8, 14, 10, 0)
    assert schedule.next_due(datetime(2026, 8, 14, 10, 5), cfg, last) == datetime(2026, 8, 14, 10, 30)


def test_without_start_and_without_last_capture_is_due_now():
    cfg = _cfg(interval=1800.0)
    now = datetime(2026, 8, 14, 10, 5)
    assert schedule.next_due(now, cfg, None) == now


# --- mit Startzeit, ohne Ende ---

def test_start_in_the_future_returns_start():
    cfg = _cfg(interval=1800.0, start=time(6, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 4, 0), cfg, None) == datetime(2026, 8, 14, 6, 0)


def test_snaps_to_the_raster():
    cfg = _cfg(interval=1800.0, start=time(6, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 10, 7), cfg, None) == datetime(2026, 8, 14, 10, 30)


def test_raster_does_not_drift():
    """Auch nach vielen Schritten liegen die Zeitpunkte exakt auf dem Raster."""
    cfg = _cfg(interval=3600.0, start=time(6, 0))
    now = datetime(2026, 8, 14, 6, 0)
    for _ in range(20):
        now = schedule.next_due(now + timedelta(seconds=37), cfg, None)
    assert now.minute == 0 and now.second == 0


def test_continues_over_midnight_without_end():
    cfg = _cfg(interval=3600.0, start=time(6, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 23, 50), cfg, None) == datetime(2026, 8, 15, 0, 0)


# --- mit Startzeit und Ende ---

def test_last_point_inside_window():
    cfg = _cfg(interval=1800.0, start=time(6, 0), end=time(20, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 19, 50), cfg, None) == datetime(2026, 8, 14, 20, 0)


def test_after_window_rolls_to_next_day():
    cfg = _cfg(interval=1800.0, start=time(6, 0), end=time(20, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 20, 1), cfg, None) == datetime(2026, 8, 15, 6, 0)


def test_before_window_returns_start_of_today():
    cfg = _cfg(interval=1800.0, start=time(6, 0), end=time(20, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 3, 0), cfg, None) == datetime(2026, 8, 14, 6, 0)


# --- Fenster über Mitternacht ---

def test_window_across_midnight_inside():
    cfg = _cfg(interval=1800.0, start=time(22, 0), end=time(4, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 2, 10), cfg, None) == datetime(2026, 8, 14, 2, 30)


def test_window_across_midnight_after_end():
    cfg = _cfg(interval=1800.0, start=time(22, 0), end=time(4, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 5, 0), cfg, None) == datetime(2026, 8, 14, 22, 0)


# --- Robustheit ---

def test_zero_interval_yields_none():
    cfg = _cfg(interval=0.0, start=time(6, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 10, 0), cfg, None) is None


# --- is_due ---

def test_due_exactly_on_target():
    t = datetime(2026, 8, 14, 12, 0)
    assert schedule.is_due(t, t, 300)


def test_due_inside_grace():
    t = datetime(2026, 8, 14, 12, 0)
    assert schedule.is_due(t + timedelta(seconds=299), t, 300)


def test_due_at_grace_boundary():
    t = datetime(2026, 8, 14, 12, 0)
    assert schedule.is_due(t + timedelta(seconds=300), t, 300)


def test_expired_beyond_grace():
    t = datetime(2026, 8, 14, 12, 0)
    assert not schedule.is_due(t + timedelta(seconds=301), t, 300)


def test_expired_after_long_downtime():
    t = datetime(2026, 8, 14, 12, 0)
    assert not schedule.is_due(t + timedelta(hours=2), t, 300)


def test_not_due_before_target():
    t = datetime(2026, 8, 14, 12, 0)
    assert not schedule.is_due(t - timedelta(seconds=1), t, 300)
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_schedule_interval.py -v`
Expected: FAIL — die Intervall-Tests scheitern an `None`, die `is_due`-Tests mit `AttributeError`

- [ ] **Step 3: Implementieren**

Den Platzhalter `_interval_next` in `services/schedule.py` ersetzen und `is_due` anhängen:

```python
def _interval_next(
    now: datetime, cfg: ScheduleConfig, last_capture: datetime | None
) -> datetime | None:
    if cfg.interval_seconds <= 0:
        return None

    step = timedelta(seconds=cfg.interval_seconds)

    # Ohne Startzeit gilt das Altverhalten: relativ zur letzten Aufnahme.
    if cfg.start is None:
        return now if last_capture is None else last_capture + step

    # Mit Startzeit läuft ein festes Raster ab der Startzeit des jeweiligen
    # Tages. Der Versatz -1 ist für Fenster über Mitternacht nötig: um 02:10
    # gehört man noch zum Fenster, das gestern um 22:00 begonnen hat.
    for day_offset in (-1, 0, 1):
        anchor = datetime.combine(now.date() + timedelta(days=day_offset), cfg.start)
        window_end = _window_end(anchor, cfg.start, cfg.end)

        if now < anchor:
            return anchor

        steps = int((now - anchor) // step) + 1
        candidate = anchor + steps * step
        if window_end is None or candidate <= window_end:
            return candidate

    return None


def _window_end(anchor: datetime, start: time, end: time | None) -> datetime | None:
    """Ende des Fensters, das bei `anchor` beginnt. None heißt unbegrenzt.

    Liegt die Endzeit nicht nach der Startzeit, endet das Fenster am Folgetag
    (Fenster über Mitternacht). end == start wird als 24-Stunden-Fenster
    behandelt.
    """
    if end is None:
        return None
    if end > start:
        return datetime.combine(anchor.date(), end)
    return datetime.combine(anchor.date() + timedelta(days=1), end)


def is_due(now: datetime, target: datetime, grace_seconds: float) -> bool:
    """True, wenn target erreicht und noch nicht verfallen ist.

    Nach einem Neustart liegt der zuvor geplante Zeitpunkt typisch weit
    zurück; genau dadurch verfällt er, ohne dass es einen Sonderfall braucht.
    """
    if now < target:
        return False
    return (now - target).total_seconds() <= grace_seconds
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_schedule_interval.py -v`
Expected: 18 passed

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 5: Commit**

```bash
git add services/schedule.py tests/test_schedule_interval.py
git commit -m "feat(schedule): add drift-free interval raster and grace-window due check"
```

---

### Task 4: Wake-Event pro Kamera

**Files:**
- Modify: `state.py:22-23`
- Modify: `api/timelapse.py:75`, `:84`
- Modify: `services/scheduler.py:219`
- Create: `tests/test_state_wake.py`

**Interfaces:**
- Consumes: nichts
- Produces: `state.get_timelapse_wake(cam: int = 0) -> asyncio.Event`

`state.timelapse_wake` ist derzeit **ein** Event für **alle** Kamera-Loops. Jeder Loop ruft `clear()` darauf auf, bevor er wartet — dadurch kann Loop 0 das Signal verschlucken, das für Loop 1 gedacht war. Das Attribut wird ersatzlos entfernt, damit kein Aufrufer versehentlich weiter darauf zugreift.

- [ ] **Step 1: Failing test schreiben**

`tests/test_state_wake.py`:

```python
import asyncio

import state


def test_same_camera_returns_same_event():
    assert state.get_timelapse_wake(0) is state.get_timelapse_wake(0)


def test_different_cameras_are_independent():
    a, b = state.get_timelapse_wake(0), state.get_timelapse_wake(1)
    assert a is not b
    a.set()
    assert a.is_set()
    assert not b.is_set()
    a.clear()


def test_returns_asyncio_event():
    assert isinstance(state.get_timelapse_wake(3), asyncio.Event)


def test_old_shared_event_is_gone():
    """Ein verbliebener Aufrufer würde sonst still das falsche Event benutzen."""
    assert not hasattr(state, "timelapse_wake")
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_state_wake.py -v`
Expected: FAIL mit `AttributeError: module 'state' has no attribute 'get_timelapse_wake'`

- [ ] **Step 3: Implementieren**

In `state.py` die Zeilen 22–23 ersetzen:

```python
# Ein Wake-Event je Kamera-Slot. Ein gemeinsames Event funktioniert nicht:
# jeder Loop ruft clear() auf, bevor er wartet, und würde damit das Signal
# für einen anderen Loop verschlucken.
_timelapse_wakes: dict[int, asyncio.Event] = {}


def get_timelapse_wake(cam: int = 0) -> asyncio.Event:
    """Event, mit dem der Timelapse-Loop einer Kamera sofort aufwacht."""
    if cam not in _timelapse_wakes:
        _timelapse_wakes[cam] = asyncio.Event()
    return _timelapse_wakes[cam]
```

- [ ] **Step 4: Aufrufer umstellen**

In `api/timelapse.py` in `start_timelapse` und `stop_timelapse` jeweils:

```python
    state.get_timelapse_wake(cam).set()
```

In `services/scheduler.py._timelapse_loop` den Block `_state.timelapse_wake.clear()` samt `wait_for` vorerst auf das kameraeigene Event umstellen; Task 5 ersetzt ihn vollständig:

```python
                wake = _state.get_timelapse_wake(cam_idx)
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
```

- [ ] **Step 5: Prüfen, dass keine Referenz übrig bleibt**

Run: `git grep -n "timelapse_wake" -- '*.py'`
Expected: nur Treffer auf `get_timelapse_wake` und `_timelapse_wakes`, kein `state.timelapse_wake`

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest tests/test_state_wake.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add state.py api/timelapse.py services/scheduler.py tests/test_state_wake.py
git commit -m "fix(timelapse): give each camera its own wake event"
```

---

### Task 5: Timelapse-Loop auf den Zeitplan umstellen

**Files:**
- Modify: `services/scheduler.py:133-231`

**Interfaces:**
- Consumes: `schedule.parse_config`, `schedule.next_due`, `schedule.is_due` aus Tasks 1–3; `state.get_timelapse_wake` aus Task 4
- Produces:
  - `Scheduler._sleep_until(wake, target) -> bool` — True, wenn durch das Wake-Event unterbrochen
  - `Scheduler._sleep_for(wake, seconds) -> bool`

Der Loop schläft nicht mehr blind das Intervall ab, sondern in Häppchen von maximal 60 Sekunden bis zum Sollzeitpunkt. Damit greifen Einstellungsänderungen spätestens nach einer Minute, auch bei einem Intervall von sechs Stunden.

- [ ] **Step 1: Toten Code entfernen**

In `_timelapse_manager` (Zeile 143) die wirkungslose Zuweisung streichen:

```python
                for i in range(camera_count):
                    if i not in self._tl_tasks or self._tl_tasks[i].done():
                        self._tl_tasks[i] = asyncio.create_task(
                            self._timelapse_loop(i), name=f"timelapse_cam{i}"
                        )
                        logger.info(f"Timelapse loop started for camera {i}")
```

- [ ] **Step 2: Schlaf-Helfer ergänzen**

Oben in `services/scheduler.py`:

```python
from datetime import datetime

from services import schedule

# Längster Schlaf am Stück. Begrenzt, damit Einstellungsänderungen auch bei
# stundenlangen Intervallen zeitnah greifen.
SETTINGS_POLL_SECONDS = 60.0

# Pause, wenn nichts zu tun ist (Aufnahme inaktiv oder kein Zeitpunkt planbar).
IDLE_POLL_SECONDS = 30.0
```

In der Klasse `Scheduler`:

```python
    async def _sleep_until(self, wake: asyncio.Event, target: datetime) -> bool:
        """Bis `target` schlafen. True, wenn das Wake-Event ausgelöst hat.

        Wird in Häppchen geschlafen, damit Einstellungsänderungen greifen und
        ein Sprung der Systemuhr nicht zu einem stundenlangen Schlaf führt.
        """
        while self._running:
            if wake.is_set():
                wake.clear()
                return True
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(
                    wake.wait(), timeout=min(remaining, SETTINGS_POLL_SECONDS)
                )
            except asyncio.TimeoutError:
                continue
            wake.clear()
            return True
        return False

    async def _sleep_for(self, wake: asyncio.Event, seconds: float) -> bool:
        return await self._sleep_until(
            wake, datetime.now() + timedelta(seconds=seconds)
        )
```

`timedelta` zum Import ergänzen: `from datetime import datetime, timedelta`.

- [ ] **Step 3: `_timelapse_loop` ersetzen**

```python
    async def _timelapse_loop(self, cam_idx: int):
        """Timelapse-Aufnahme einer Kamera nach ihrem Zeitplan."""
        last_cam_config = None
        last_capture: datetime | None = None
        wake = _state.get_timelapse_wake(cam_idx)

        while self._running:
            try:
                settings = await self._db.get_all_settings()
                tl_path  = settings.get("timelapse_path") or "timelapse"
                cam      = _state.get_camera(cam_idx)

                def cam_get(name, default, legacy=None):
                    key = f"cam_{cam_idx}_{name}"
                    if settings.get(key) is not None:
                        return settings[key]
                    if cam_idx == 0 and legacy is not None and settings.get(legacy) is not None:
                        return settings[legacy]
                    return default

                active        = cam_get("timelapse_active", False, "timelapse_active")
                dev_idx       = int(cam_get("device_index", cam_idx, "camera_index"))
                cap_w         = int(cam_get("capture_width", 0, "camera_capture_width"))
                cap_h         = int(cam_get("capture_height", 0, "camera_capture_height"))
                capture_mode  = cam_get("capture_mode", "still", "capture_mode")
                clip_duration = float(cam_get("clip_duration", 5, "clip_duration"))
                clip_fps      = int(cam_get("clip_fps", 10, "clip_fps"))

                cam_config = (tl_path, dev_idx, cap_w, cap_h)
                if cam_config != last_cam_config:
                    cam.setup(
                        frames_dir=f"{tl_path}/cam{cam_idx}/frames",
                        output_dir=f"{tl_path}/cam{cam_idx}/output",
                        camera_index=dev_idx,
                        capture_width=cap_w,
                        capture_height=cap_h,
                    )
                    last_cam_config = cam_config

                if not active:
                    if cam.is_capturing:
                        cam.stop_session()
                    last_capture = None
                    await self._sleep_for(wake, IDLE_POLL_SECONDS)
                    continue

                if not cam.is_capturing:
                    cam.start_session()

                cfg = schedule.parse_config(settings, cam_idx)
                target = schedule.next_due(datetime.now(), cfg, last_capture)
                if target is None:
                    logger.debug(f"cam{cam_idx}: kein Aufnahmezeitpunkt planbar")
                    await self._sleep_for(wake, IDLE_POLL_SECONDS)
                    continue

                if await self._sleep_until(wake, target):
                    continue  # Einstellungen geändert – Zeitpunkt neu berechnen

                now = datetime.now()
                if schedule.is_due(now, target, cfg.grace_seconds):
                    if capture_mode == "clip":
                        await asyncio.to_thread(cam.capture_clip, clip_duration, clip_fps)
                    else:
                        await asyncio.to_thread(cam.capture_frame)
                else:
                    logger.info(
                        f"cam{cam_idx}: Aufnahme {target:%Y-%m-%d %H:%M} verfallen "
                        f"(Kulanzfenster {cfg.grace_seconds:.0f}s überschritten)"
                    )
                # In beiden Fällen: Rhythmus ab jetzt fortsetzen, nicht nachholen.
                last_capture = now

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Timelapse loop cam{cam_idx} error: {exc}")
                await asyncio.sleep(60)
```

**Hinweis für den Fall, dass Plan `2026-08-14-kamera-erkennung-und-belichtung.md` bereits umgesetzt ist:** Dort wird `cam.setup(...)` durch `cam.setup(**camera_setup_kwargs(settings, cam_idx, tl_path))` ersetzt und `cam_config` durch das kwargs-Dict. Diese Fassung dann beibehalten und nur den Zeitplan-Teil übernehmen.

- [ ] **Step 4: Manuell prüfen — Intervall ohne Startzeit**

Run: `start_local.bat`

Im Dashboard Timelapse mit Intervall 0,0014 h (≈ 5 s) starten. Erwartet: alle ~5 s ein neues Bild, `frame_count` steigt. Das ist das unveränderte Altverhalten.

- [ ] **Step 5: Manuell prüfen — Verfall**

Bei laufender Aufnahme mit langem Intervall den Prozess mit Strg+C beenden, 10 Minuten warten, neu starten.

Erwartet im Log: **keine** sofortige Nachhol-Aufnahme, wenn ein Zeitplan mit Startzeit oder festen Uhrzeiten aktiv ist. Der Loop wartet auf den nächsten regulären Zeitpunkt.

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 7: Commit**

```bash
git add services/scheduler.py
git commit -m "feat(timelapse): drive capture loop from the schedule instead of a blind interval"
```

---

### Task 6: Zeitplan über die API

**Files:**
- Modify: `api/timelapse.py:28-65` (`get_status`)
- Modify: `api/settings.py`
- Modify: `db/database.py:12-44`

**Interfaces:**
- Consumes: `schedule.parse_config`, `schedule.next_due` aus Tasks 1–3; `state.get_timelapse_wake` aus Task 4
- Produces: `GET /api/timelapse/status?cam=N` liefert zusätzlich `schedule_mode`, `schedule_start`, `schedule_end`, `schedule_times`, `schedule_grace`, `next_due`

`next_due` wird serverseitig berechnet und als ISO-String geliefert, damit das Dashboard die Zeitlogik nicht doppelt implementieren muss. Im Intervallmodus **ohne** Startzeit ist der nächste Zeitpunkt von der letzten Aufnahme abhängig, die der API nicht bekannt ist — dort wird `null` geliefert und das Dashboard zeigt stattdessen das Intervall an.

- [ ] **Step 1: Defaults ergänzen**

In `db/database.py` zu `DEFAULT_SETTINGS`:

```python
    "cam_0_schedule_mode": "interval",   # interval | times
    "cam_0_schedule_start": None,        # "HH:MM" – ab wann das Intervall läuft
    "cam_0_schedule_end": None,          # "HH:MM" – optionales Fensterende
    "cam_0_schedule_times": [],          # ["08:00", "12:00"] im Modus "times"
    "cam_0_schedule_grace": 300,         # Kulanzfenster in Sekunden
```

`_seed_defaults` überschreibt gespeicherte `null`-Werte mit dem Default. Für `schedule_start` und `schedule_end` ist `None` aber ein gültiger Wert. Die Bedingung in Zeile 95–98 deshalb um eine Ausnahmeliste erweitern:

```python
    # Schlüssel, für die JSON-null ein gültiger Wert ist und nicht durch den
    # Default ersetzt werden darf.
    NULLABLE_SETTINGS = {"cam_0_schedule_start", "cam_0_schedule_end"}
```

```python
    async def _seed_defaults(self):
        for key, value in DEFAULT_SETTINGS.items():
            await self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            if key in NULLABLE_SETTINGS:
                continue
            await self._conn.execute(
                "UPDATE settings SET value = ? WHERE key = ? AND value = 'null'",
                (json.dumps(value), key),
            )
```

Für die Kameras 1–3 werden keine Defaults gesetzt; `parse_config` liefert dort dieselben Werte über seine eigenen Vorgaben. Das entspricht dem bestehenden Muster im Projekt.

- [ ] **Step 2: Status erweitern**

In `api/timelapse.py` oben ergänzen:

```python
from datetime import datetime

from services import schedule
```

Am Ende von `get_status` vor dem `return`:

```python
    cfg = schedule.parse_config(settings, cam)
    # Ohne Startzeit hängt der nächste Zeitpunkt an der letzten Aufnahme, die
    # hier nicht bekannt ist – dann bewusst kein Wert.
    if cfg.mode == schedule.MODE_INTERVAL and cfg.start is None:
        next_due = None
    else:
        nxt = schedule.next_due(datetime.now(), cfg, None)
        next_due = nxt.isoformat() if nxt else None
```

Und im Rückgabe-Dict:

```python
        "schedule_mode":  cfg.mode,
        "schedule_start": cfg.start.strftime("%H:%M") if cfg.start else None,
        "schedule_end":   cfg.end.strftime("%H:%M") if cfg.end else None,
        "schedule_times": [t.strftime("%H:%M") for t in cfg.times],
        "schedule_grace": cfg.grace_seconds,
        "next_due":       next_due,
```

- [ ] **Step 3: Loop bei Zeitplanänderungen wecken**

Ohne das dauert es bis zu 60 Sekunden, bis eine im Dashboard geänderte Uhrzeit greift. In `api/settings.py` am Ende von `update_settings` vor dem `return`:

```python
    # Betroffene Timelapse-Loops sofort wecken, damit ein geänderter Zeitplan
    # nicht erst beim nächsten Poll greift.
    schedule_pattern = re.compile(r"cam_(\d+)_(schedule_\w+|timelapse_interval)")
    for k in updates:
        m = schedule_pattern.match(k)
        if m:
            state.get_timelapse_wake(int(m.group(1))).set()
    if "timelapse_interval" in updates:
        state.get_timelapse_wake(0).set()
```

- [ ] **Step 4: Manuell prüfen**

Run: `start_local.bat`

```
curl "http://localhost:8080/api/timelapse/status?cam=0"
```

Erwartet: `schedule_mode` ist `"interval"`, `schedule_times` ist `[]`, `next_due` ist `null`.

Dann per PUT einen Zeitplan setzen:

```
curl -X PUT http://localhost:8080/api/settings -H "Content-Type: application/json" ^
  -d "{\"cam_0_schedule_mode\":\"times\",\"cam_0_schedule_times\":[\"08:00\",\"12:00\",\"16:00\"]}"
```

Erwartet: `status` liefert nun `schedule_mode: "times"` und ein `next_due` mit der nächsten der drei Uhrzeiten.

- [ ] **Step 5: Tests ausführen**

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 6: Commit**

```bash
git add api/timelapse.py api/settings.py db/database.py
git commit -m "feat(api): expose timelapse schedule and next due time"
```

---

### Task 7: Dashboard – Zeitplan-Bedienung

**Files:**
- Modify: `static/js/app.js:515-575` (`buildCameraSection`)
- Modify: `static/css/style.css`

**Interfaces:**
- Consumes: `escHtml`, `parseDE`, `formatDE`, `showToast` (existieren bereits in `app.js`)
- Produces:
  - `updateScheduleUI(ci)` — schaltet zwischen den beiden Modus-Blöcken um
  - `renderTimeFields(ci)` — erzeugt die Uhrzeitfelder
  - `defaultTimes(n) -> string[]` — gleichmäßig über den Tag verteilte Vorbelegung
  - `collectTimes(ci) -> string[]` — liest die gesetzten Uhrzeiten aus

- [ ] **Step 1: Markup ersetzen**

In `buildCameraSection(ci)` die bisherige Intervallzeile (`<label>Intervall (Stunden)</label>` samt umgebendem `control-row`) durch den folgenden Block ersetzen:

```html
      <div class="control-row" data-tooltip="Intervall: Aufnahmen im festen Zeitabstand. Feste Uhrzeiten: eine Aufnahme zu jeder eingestellten Uhrzeit, täglich wiederholt.">
        <label>Zeitplan</label>
        <select id="tl-schedule-mode-${ci}" onchange="updateScheduleUI(${ci}); saveSchedule(${ci})">
          <option value="interval">Intervall</option>
          <option value="times">Feste Uhrzeiten</option>
        </select>
      </div>

      <div id="tl-interval-block-${ci}">
        <div class="control-row" data-tooltip="Ab wann am Tag aufgenommen wird. Leer lassen: Intervall läuft ab dem Start der Aufnahme.">
          <label>Ab Uhrzeit</label>
          <input type="time" id="tl-start-${ci}" onchange="saveSchedule(${ci})" />
        </div>
        <div class="control-row" data-tooltip="Bis wann am Tag aufgenommen wird. Leer lassen: läuft durch, auch über Mitternacht.">
          <label>Bis Uhrzeit</label>
          <input type="time" id="tl-end-${ci}" onchange="saveSchedule(${ci})" />
        </div>
        <div class="control-row" data-tooltip="Zeitabstand zwischen zwei Aufnahmen in Stunden.">
          <label>Intervall (Stunden)</label>
          <input type="text" inputmode="decimal" id="tl-interval-${ci}" placeholder="0,0014"
                 onchange="saveSchedule(${ci})" />
        </div>
      </div>

      <div id="tl-times-block-${ci}" class="hidden">
        <div class="control-row" data-tooltip="Anzahl der Aufnahmen pro Tag. Für jede erscheint ein Uhrzeitfeld.">
          <label>Bilder pro Tag</label>
          <input type="number" id="tl-times-count-${ci}" min="1" max="24" step="1" value="3"
                 onchange="renderTimeFields(${ci}); saveSchedule(${ci})" />
        </div>
        <div id="tl-times-fields-${ci}" class="tl-times-grid"></div>
      </div>

      <div class="tl-next-due" id="tl-next-due-${ci}"></div>
```

- [ ] **Step 2: Umschaltung und Uhrzeitfelder implementieren**

Im Timelapse-Abschnitt von `app.js` ergänzen:

```javascript
function updateScheduleUI(ci) {
  const mode = document.getElementById(`tl-schedule-mode-${ci}`).value;
  document.getElementById(`tl-interval-block-${ci}`).classList.toggle('hidden', mode !== 'interval');
  document.getElementById(`tl-times-block-${ci}`).classList.toggle('hidden', mode !== 'times');
  if (mode === 'times' && !collectTimes(ci).length) renderTimeFields(ci);
}

function minutesToHHMM(total) {
  const m = ((total % 1440) + 1440) % 1440;
  return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}

function defaultTimes(n) {
  // Gleichmäßig zwischen 06:00 und 18:00, auf 5 Minuten gerundet.
  // Eine einzelne Aufnahme landet in der Tagesmitte.
  const START = 6 * 60, SPAN = 12 * 60;
  const out = [];
  for (let k = 0; k < n; k++) {
    const offset = n === 1 ? SPAN / 2 : (SPAN * k) / (n - 1);
    out.push(minutesToHHMM(Math.round((START + offset) / 5) * 5));
  }
  return out;
}

function collectTimes(ci) {
  const out = [];
  document.querySelectorAll(`#tl-times-fields-${ci} input[type=time]`).forEach(el => {
    if (el.value) out.push(el.value);
  });
  return out;
}

function renderTimeFields(ci, preset) {
  const host  = document.getElementById(`tl-times-fields-${ci}`);
  const input = document.getElementById(`tl-times-count-${ci}`);
  if (!host || !input) return;

  const n = Math.max(1, Math.min(24, parseInt(input.value) || 1));
  input.value = n;

  // Bereits gesetzte Uhrzeiten überleben eine Änderung der Anzahl.
  const existing = preset || collectTimes(ci);
  const fallback = defaultTimes(n);

  host.innerHTML = Array.from({ length: n }, (_, k) => {
    const value = existing[k] || fallback[k];
    return `<label class="tl-time-field">
      <span>${k + 1}.</span>
      <input type="time" id="tl-time-${ci}-${k}" value="${value}" onchange="saveSchedule(${ci})" />
    </label>`;
  }).join('');
}
```

- [ ] **Step 3: CSS ergänzen**

An `static/css/style.css` anhängen:

```css
.tl-times-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr));
  gap: .4rem;
  margin: .4rem 0 .6rem;
}
.tl-time-field {
  display: flex;
  align-items: center;
  gap: .35rem;
  font-size: .75rem;
  color: var(--text2);
}
.tl-time-field input {
  flex: 1;
  min-width: 0;
}
.tl-next-due {
  font-size: .75rem;
  color: var(--text3);
  margin: .2rem 0 .6rem;
  min-height: 1em;
}
```

- [ ] **Step 4: Manuell prüfen**

Run: `start_local.bat`, `http://localhost:8080` öffnen.

Prüfen:
- Zeitplan auf „Feste Uhrzeiten" umschalten: der Intervallblock verschwindet, drei Uhrzeitfelder mit 06:00, 12:00, 18:00 erscheinen.
- „Bilder pro Tag" auf 5 erhöhen: die drei gesetzten Uhrzeiten bleiben erhalten, zwei neue kommen dazu.
- Eine Uhrzeit auf 07:15 ändern, dann auf 2 reduzieren und wieder auf 5: 07:15 steht weiterhin an derselben Position.
- Zurück auf „Intervall": Ab-, Bis- und Intervallfeld sind wieder sichtbar.

- [ ] **Step 5: Commit**

```bash
git add static/js/app.js static/css/style.css
git commit -m "feat(ui): add timelapse schedule controls with fixed times mode"
```

---

### Task 8: Dashboard – Zeitplan speichern und nächste Aufnahme anzeigen

**Files:**
- Modify: `static/js/app.js:759-798` (`fetchTimelapse`), `:843-879` (`startTimelapse`)

**Interfaces:**
- Consumes: `schedule_*`- und `next_due`-Felder aus `GET /api/timelapse/status` (Task 6); `updateScheduleUI`, `renderTimeFields`, `collectTimes` aus Task 7
- Produces:
  - `saveSchedule(ci)` — speichert den Zeitplan sofort
  - `scheduleBody(ci)` — baut das Settings-Objekt, wird von `saveSchedule` und `startTimelapse` genutzt
  - `renderNextDue(ci, iso)`

- [ ] **Step 1: Speichern implementieren**

```javascript
function scheduleBody(ci) {
  const mode  = document.getElementById(`tl-schedule-mode-${ci}`).value;
  const start = document.getElementById(`tl-start-${ci}`).value;
  const end   = document.getElementById(`tl-end-${ci}`).value;
  const hours = parseDE(document.getElementById(`tl-interval-${ci}`).value);

  return {
    [`cam_${ci}_schedule_mode`]:  mode,
    [`cam_${ci}_schedule_start`]: start || null,
    [`cam_${ci}_schedule_end`]:   end   || null,
    [`cam_${ci}_schedule_times`]: collectTimes(ci),
    [`cam_${ci}_timelapse_interval`]: isNaN(hours) ? 300 : Math.max(1, Math.round(hours * 3600)),
  };
}

async function saveSchedule(ci) {
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(scheduleBody(ci)),
  });
  showToast('Zeitplan gespeichert');
  fetchTimelapse(ci);
}
```

- [ ] **Step 2: Nächste Aufnahme anzeigen**

```javascript
function renderNextDue(ci, iso) {
  const el = document.getElementById(`tl-next-due-${ci}`);
  if (!el) return;
  if (!iso) { el.textContent = ''; return; }

  const d = new Date(iso);
  if (isNaN(d)) { el.textContent = ''; return; }

  const now  = new Date();
  const time = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  const sameDay = d.toDateString() === now.toDateString();
  const day = sameDay ? 'heute' : d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  el.textContent = `Nächste Aufnahme: ${day} ${time}`;
}
```

- [ ] **Step 3: `fetchTimelapse` erweitern**

Den bisherigen Block, der nur das Intervallfeld befüllt (`app.js:782-787`), ersetzen:

```javascript
    const modeSel = document.getElementById(`tl-schedule-mode-${ci}`);
    modeSel.value = d.schedule_mode ?? 'interval';
    document.getElementById(`tl-start-${ci}`).value = d.schedule_start ?? '';
    document.getElementById(`tl-end-${ci}`).value   = d.schedule_end   ?? '';

    // Uhrzeiten nur nachziehen, solange der Nutzer nicht gerade tippt.
    const serverTimes = d.schedule_times ?? [];
    if (JSON.stringify(serverTimes) !== JSON.stringify(collectTimes(ci))) {
      document.getElementById(`tl-times-count-${ci}`).value = Math.max(1, serverTimes.length || 3);
      renderTimeFields(ci, serverTimes);
    }

    const tlIntervalEl = document.getElementById(`tl-interval-${ci}`);
    const serverSecs   = d.interval ?? 3600;
    const uiSecs       = Math.round(parseDE(tlIntervalEl.value) * 3600);
    if (isNaN(uiSecs) || uiSecs === serverSecs) {
      tlIntervalEl.value = formatDE(serverSecs / 3600, 4);
    }

    updateScheduleUI(ci);
    renderNextDue(ci, d.next_due);
```

- [ ] **Step 4: `startTimelapse` auf `scheduleBody` umstellen**

```javascript
async function startTimelapse(ci) {
  const devIdx       = parseInt(document.getElementById(`tl-cam-idx-${ci}`).value);
  const [capW, capH] = document.getElementById(`tl-resolution-${ci}`).value.split('x').map(Number);
  const captureMode  = document.getElementById(`tl-capture-mode-${ci}`).value;
  const clipDuration = parseInt(document.getElementById(`tl-clip-duration-${ci}`).value);
  const clipFps      = parseInt(document.getElementById(`tl-clip-fps-${ci}`).value);

  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...scheduleBody(ci),
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
```

- [ ] **Step 5: Manuell prüfen**

Run: `start_local.bat`, `http://localhost:8080` öffnen.

Prüfen:
- Modus „Feste Uhrzeiten", Uhrzeiten 08:00/12:00/16:00 setzen, Seite neu laden: die Werte stehen wieder da, der Modus ebenfalls.
- Unter dem Block steht „Nächste Aufnahme: heute HH:MM" mit der nächsten der drei Uhrzeiten. Nach 16:00 steht dort das morgige Datum.
- Modus „Intervall" mit „Ab Uhrzeit" 06:00 und leerem „Bis": „Nächste Aufnahme" zeigt den nächsten Rasterpunkt.
- „Ab Uhrzeit" leeren: die Anzeige verschwindet, das Verhalten fällt auf das reine Intervall zurück.
- Aufnahme starten und einen nahen Zeitpunkt setzen (z. B. eine Minute in der Zukunft). Erwartet: genau zu dieser Uhrzeit steigt `frame_count` um 1.

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 7: Commit**

```bash
git add static/js/app.js
git commit -m "feat(ui): persist timelapse schedule and show the next capture time"
```

---

## Abschlussprüfung auf der Zielhardware

- [ ] Modus „Feste Uhrzeiten" mit drei Zeitpunkten über einen ganzen Tag laufen lassen. Erwartet: genau drei Bilder, deren Dateinamen die eingestellten Uhrzeiten tragen.
- [ ] Modus „Intervall" mit Fenster 06:00–20:00 und 30 Minuten. Erwartet: 29 Bilder, exakt auf halben und vollen Stunden, nachts keine Aufnahme.
- [ ] Während einer laufenden Aufnahme den Dienst neu starten (`sudo systemctl restart greenhouse`). Erwartet im Log eine Meldung „verfallen" nur dann, wenn ein Zeitpunkt tatsächlich in die Ausfallzeit fiel — und keine sofortige Nachhol-Aufnahme.
- [ ] Zeitplan im Dashboard ändern, während die Aufnahme läuft. Erwartet: „Nächste Aufnahme" aktualisiert sich innerhalb weniger Sekunden, nicht erst nach einer Minute.
- [ ] Bei zwei konfigurierten Kameras beide mit unterschiedlichen Zeitplänen starten. Erwartet: die Zeitpläne beeinflussen sich nicht gegenseitig (Prüfung des Wake-Events pro Kamera).

---

# Nachtrag: Datumsangaben und Nacht-Semantik

Nach Tasks 1–3 vom Nutzer entschieden. Zwei Änderungen an der Zeitplan-Logik und
entsprechend erweiterte Bedienung. Die Tasks 9 und 10 kommen hinzu, die Tasks 6, 7 und 8
werden ergänzt.

**Entscheidung 1 — Nacht-Semantik.** Intervall mit Startzeit und ohne Endzeit: die Startzeit
gilt nur für den ersten Tag. Ist noch nichts aufgenommen und liegt `now` vor der heutigen
Startzeit, wird bis dahin gewartet. Danach läuft das Raster lückenlos weiter, auch über
Mitternacht. Damit löst eine um 04:00 gestartete Aufnahme nicht sofort um 04:30 aus, und ab
dem zweiten Tag entsteht keine Lücke zwischen Mitternacht und der Startzeit.

**Entscheidung 2 — Datum.** Zwei Dinge zugleich: ein Zeitraum `von`/`bis`, der den
wiederkehrenden Zeitplan begrenzt, und zusätzlich eine Liste einzelner Termine mit eigenem
Datum, die genau einmal auslösen.

---

### Task 9: Nacht-Semantik und Zeitraum

**Files:**
- Modify: `services/schedule.py`
- Create: `tests/test_schedule_dates.py`

**Interfaces:**
- Consumes: `ScheduleConfig`, `next_due`, `_interval_next` aus Tasks 1–3
- Produces:
  - `ScheduleConfig` zusätzlich mit `date_from: date | None` und `date_to: date | None`
  - `schedule.parse_date(value) -> date | None`
  - `_interval_next` mit korrigierter Anker-Wahl

- [ ] **Step 1: Failing test schreiben**

`tests/test_schedule_dates.py`:

```python
from datetime import date, datetime, time

from services import schedule


def _cfg(**over):
    base = dict(
        mode=schedule.MODE_INTERVAL,
        interval_seconds=1800.0,
        start=None,
        end=None,
        times=(),
        grace_seconds=300.0,
        date_from=None,
        date_to=None,
    )
    base.update(over)
    return schedule.ScheduleConfig(**base)


# --- parse_date ---

def test_parse_date_iso():
    assert schedule.parse_date("2026-04-01") == date(2026, 4, 1)


def test_parse_date_rejects_nonsense():
    assert schedule.parse_date(None) is None
    assert schedule.parse_date("") is None
    assert schedule.parse_date("01.04.2026") is None
    assert schedule.parse_date("2026-13-01") is None
    assert schedule.parse_date(42) is None


def test_parse_config_reads_dates():
    cfg = schedule.parse_config(
        {"cam_0_date_from": "2026-04-01", "cam_0_date_to": "2026-06-30"}, 0
    )
    assert cfg.date_from == date(2026, 4, 1)
    assert cfg.date_to == date(2026, 6, 30)


def test_parse_config_dates_default_to_none():
    cfg = schedule.parse_config({}, 0)
    assert cfg.date_from is None and cfg.date_to is None


# --- Nacht-Semantik: Startzeit gilt nur am ersten Tag ---

def test_waits_for_start_on_the_first_day():
    """Um 04:00 gestartet, ab 06:00 - nicht sofort um 04:30 ausloesen."""
    cfg = _cfg(start=time(6, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 4, 0), cfg, None) == datetime(2026, 8, 14, 6, 0)


def test_runs_continuously_once_something_was_captured():
    """Nach der ersten Aufnahme laeuft das Raster auch nachts weiter."""
    cfg = _cfg(start=time(6, 0))
    last = datetime(2026, 8, 14, 23, 30)
    assert schedule.next_due(datetime(2026, 8, 15, 4, 0), cfg, last) == datetime(2026, 8, 15, 4, 30)


def test_no_gap_across_midnight_after_first_day():
    cfg = _cfg(start=time(6, 0), interval_seconds=3600.0)
    last = datetime(2026, 8, 14, 23, 0)
    assert schedule.next_due(datetime(2026, 8, 14, 23, 50), cfg, last) == datetime(2026, 8, 15, 0, 0)


def test_started_after_the_start_time_captures_on_the_raster():
    """Um 20:10 gestartet, ab 06:00 - das Raster laeuft heute bereits."""
    cfg = _cfg(start=time(6, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 20, 10), cfg, None) == datetime(2026, 8, 14, 20, 30)


def test_bounded_window_is_unaffected_by_the_first_day_rule():
    cfg = _cfg(start=time(22, 0), end=time(4, 0))
    assert schedule.next_due(datetime(2026, 8, 14, 2, 10), cfg, None) == datetime(2026, 8, 14, 2, 30)


def test_odd_interval_has_no_gap_after_the_first_day():
    """7000 s teilt 24 h nicht glatt - das Raster darf trotzdem nicht neu ansetzen."""
    cfg = _cfg(start=time(6, 0), interval_seconds=7000.0)
    last = datetime(2026, 8, 15, 1, 26, 40)
    nxt = schedule.next_due(datetime(2026, 8, 15, 1, 30), cfg, last)
    assert nxt == datetime(2026, 8, 15, 3, 23, 20)


# --- Zeitraum ---

def test_before_date_from_yields_nothing():
    cfg = _cfg(start=time(6, 0), date_from=date(2026, 9, 1))
    assert schedule.next_due(datetime(2026, 8, 14, 10, 0), cfg, None) is None


def test_first_day_of_range_starts_at_the_start_time():
    cfg = _cfg(start=time(6, 0), date_from=date(2026, 8, 14))
    assert schedule.next_due(datetime(2026, 8, 14, 4, 0), cfg, None) == datetime(2026, 8, 14, 6, 0)


def test_after_date_to_yields_nothing():
    cfg = _cfg(start=time(6, 0), date_to=date(2026, 8, 13))
    assert schedule.next_due(datetime(2026, 8, 14, 10, 0), cfg, None) is None


def test_date_to_is_inclusive():
    cfg = _cfg(mode=schedule.MODE_TIMES, times=(time(12, 0),), date_to=date(2026, 8, 14))
    assert schedule.next_due(datetime(2026, 8, 14, 9, 0), cfg, None) == datetime(2026, 8, 14, 12, 0)


def test_times_mode_stops_after_date_to():
    cfg = _cfg(mode=schedule.MODE_TIMES, times=(time(12, 0),), date_to=date(2026, 8, 14))
    assert schedule.next_due(datetime(2026, 8, 14, 13, 0), cfg, None) is None


def test_range_without_bounds_behaves_as_before():
    cfg = _cfg(mode=schedule.MODE_TIMES, times=(time(12, 0),))
    assert schedule.next_due(datetime(2026, 8, 14, 13, 0), cfg, None) == datetime(2026, 8, 15, 12, 0)
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_schedule_dates.py -v`
Expected: FAIL mit `TypeError: ScheduleConfig.__init__() got an unexpected keyword argument 'date_from'`

- [ ] **Step 3: Datum in Konfiguration und Parsing**

In `services/schedule.py` den Import erweitern und `ScheduleConfig` um zwei Felder ergänzen.
Beide bekommen einen Default, damit bestehende Konstruktoraufrufe in den älteren Testdateien
unverändert weiterlaufen:

```python
from datetime import date, datetime, time, timedelta
```

```python
@dataclass(frozen=True)
class ScheduleConfig:
    mode: str
    interval_seconds: float
    start: time | None
    end: time | None
    times: tuple[time, ...]
    grace_seconds: float
    date_from: date | None = None
    date_to: date | None = None
```

```python
def parse_date(value) -> date | None:
    """YYYY-MM-DD in ein date-Objekt. Sonst None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
```

In `parse_config` die beiden Felder im Konstruktoraufruf ergänzen:

```python
        date_from=parse_date(cam_get("date_from", None)),
        date_to=parse_date(cam_get("date_to", None)),
```

- [ ] **Step 4: Zeitraum in `next_due` durchsetzen**

Der Zeitraum wird als äußere Schale um beide Modi gelegt, damit ihn keiner von beiden
vergessen kann:

```python
def next_due(
    now: datetime,
    cfg: ScheduleConfig,
    last_capture: datetime | None,
) -> datetime | None:
    """Nächster Aufnahmezeitpunkt, oder None wenn keiner bestimmbar ist.

    Der optionale Zeitraum date_from..date_to begrenzt den Plan. date_to ist
    einschliesslich: am Enddatum wird noch aufgenommen. Vor date_from wird None
    geliefert; der Loop fragt beim naechsten Durchlauf erneut.
    """
    if cfg.date_from is not None and now.date() < cfg.date_from:
        return None

    if cfg.mode == MODE_TIMES:
        candidate = _times_next(now, cfg)
    else:
        candidate = _interval_next(now, cfg, last_capture)

    if candidate is None:
        return None
    if cfg.date_to is not None and candidate.date() > cfg.date_to:
        return None
    return candidate
```

- [ ] **Step 5: Erste-Tag-Regel in `_interval_next`**

Der bisherige Code beschränkt den Tagesversatz `-1` auf `cfg.end is not None`. Das verhindert
zwar, dass eine frisch gestartete Aufnahme sofort auf dem Raster von gestern auslöst, sorgt
aber dafür, dass ab dem zweiten Tag zwischen Mitternacht und der Startzeit eine Lücke klafft.
Die Unterscheidung gehört an `last_capture`, nicht an `cfg.end`.

Vor der Schleife über die Tagesversätze einfügen:

```python
    # Erste-Tag-Regel: Solange nichts aufgenommen wurde und die heutige Startzeit
    # noch bevorsteht, wird auf sie gewartet. Ohne diese Regel wuerde eine um 04:00
    # gestartete Aufnahme sofort auf dem Raster von gestern ausloesen.
    if cfg.end is None and last_capture is None:
        today_start = datetime.combine(now.date(), cfg.start)
        if now < today_start:
            return today_start
```

und die Versatz-Auswahl ersetzen durch:

```python
    # Versatz -1 deckt zwei Faelle ab: ein Fenster, das ueber Mitternacht laeuft,
    # und ein unbegrenztes Raster, das nach der ersten Aufnahme lueckenlos
    # weiterlaufen soll.
    offsets = (-1, 0, 1) if (cfg.end is not None or last_capture is not None) else (0, 1)
    for day_offset in offsets:
```

Der restliche Rumpf der Schleife bleibt unverändert.

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest tests/test_schedule_dates.py -v`
Expected: 16 passed

Run: `python -m pytest`
Expected: alles grün, insbesondere die 18 Tests aus `tests/test_schedule_interval.py`

- [ ] **Step 7: Commit**

```bash
git add services/schedule.py tests/test_schedule_dates.py
git commit -m "feat(schedule): honour the start time on day one and bound the plan by date"
```

---

### Task 10: Einzeltermine mit Datum

**Files:**
- Modify: `services/schedule.py`
- Create: `tests/test_schedule_oneshots.py`

**Interfaces:**
- Consumes: `next_due`, `ScheduleConfig` aus Task 9
- Produces:
  - `ScheduleConfig` zusätzlich mit `oneshots: tuple[datetime, ...]`
  - `schedule.parse_datetime(value) -> datetime | None`
  - `_recurring_next` und `_oneshot_next` als interne Aufteilung von `next_due`

Einzeltermine laufen unabhängig vom Modus und unabhängig vom Zeitraum: sie tragen ihr Datum
selbst. Sie brauchen keinen Zustand, um nur einmal auszulösen — ein Termin in der
Vergangenheit wird schlicht nicht mehr zurückgegeben.

- [ ] **Step 1: Failing test schreiben**

`tests/test_schedule_oneshots.py`:

```python
from datetime import date, datetime, time

from services import schedule


def _cfg(**over):
    base = dict(
        mode=schedule.MODE_TIMES,
        interval_seconds=1800.0,
        start=None,
        end=None,
        times=(),
        grace_seconds=300.0,
        date_from=None,
        date_to=None,
        oneshots=(),
    )
    base.update(over)
    return schedule.ScheduleConfig(**base)


def test_parse_datetime_accepts_space_and_t():
    assert schedule.parse_datetime("2026-04-01 08:00") == datetime(2026, 4, 1, 8, 0)
    assert schedule.parse_datetime("2026-04-01T08:00") == datetime(2026, 4, 1, 8, 0)


def test_parse_datetime_rejects_nonsense():
    assert schedule.parse_datetime(None) is None
    assert schedule.parse_datetime("") is None
    assert schedule.parse_datetime("2026-04-01") is None
    assert schedule.parse_datetime("morgen frueh") is None


def test_parse_config_reads_and_sorts_oneshots():
    cfg = schedule.parse_config(
        {"cam_0_oneshots": ["2026-05-01 12:00", "2026-04-01 08:00", "kaputt"]}, 0
    )
    assert cfg.oneshots == (
        datetime(2026, 4, 1, 8, 0),
        datetime(2026, 5, 1, 12, 0),
    )


def test_oneshot_alone_is_returned():
    cfg = _cfg(oneshots=(datetime(2026, 4, 1, 8, 0),))
    assert schedule.next_due(datetime(2026, 3, 31, 9, 0), cfg, None) == datetime(2026, 4, 1, 8, 0)


def test_past_oneshot_is_ignored():
    cfg = _cfg(oneshots=(datetime(2026, 4, 1, 8, 0),))
    assert schedule.next_due(datetime(2026, 4, 1, 9, 0), cfg, None) is None


def test_oneshot_fires_once_then_the_next_one():
    cfg = _cfg(oneshots=(datetime(2026, 4, 1, 8, 0), datetime(2026, 5, 1, 8, 0)))
    assert schedule.next_due(datetime(2026, 4, 1, 8, 0), cfg, None) == datetime(2026, 5, 1, 8, 0)


def test_earlier_of_oneshot_and_recurring_wins():
    cfg = _cfg(times=(time(12, 0),), oneshots=(datetime(2026, 4, 1, 8, 0),))
    assert schedule.next_due(datetime(2026, 4, 1, 6, 0), cfg, None) == datetime(2026, 4, 1, 8, 0)


def test_recurring_wins_when_it_comes_first():
    cfg = _cfg(times=(time(12, 0),), oneshots=(datetime(2026, 4, 1, 18, 0),))
    assert schedule.next_due(datetime(2026, 4, 1, 6, 0), cfg, None) == datetime(2026, 4, 1, 12, 0)


def test_oneshot_ignores_the_date_range():
    """Ein Einzeltermin traegt sein Datum selbst und gilt auch ausserhalb des Zeitraums."""
    cfg = _cfg(
        times=(time(12, 0),),
        date_to=date(2026, 4, 1),
        oneshots=(datetime(2026, 9, 1, 8, 0),),
    )
    assert schedule.next_due(datetime(2026, 8, 14, 6, 0), cfg, None) == datetime(2026, 9, 1, 8, 0)


def test_no_recurring_and_no_oneshot_yields_none():
    assert schedule.next_due(datetime(2026, 4, 1, 6, 0), _cfg(), None) is None
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_schedule_oneshots.py -v`
Expected: FAIL mit `TypeError: ScheduleConfig.__init__() got an unexpected keyword argument 'oneshots'`

- [ ] **Step 3: Implementieren**

`ScheduleConfig` erweitern, wieder mit Default:

```python
    oneshots: tuple[datetime, ...] = ()
```

Konstante und Parser ergänzen:

```python
# Mehr als 50 Einzeltermine sind im Dashboard nicht mehr sinnvoll pflegbar.
MAX_ONESHOTS = 50


def parse_datetime(value) -> datetime | None:
    """YYYY-MM-DD HH:MM oder mit T als Trenner. Sonst None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace(" ", "T"))
    except ValueError:
        return None
```

`parse_datetime("2026-04-01")` liefert ein `datetime` mit Uhrzeit 00:00 und wäre damit gültig,
obwohl keine Uhrzeit angegeben wurde. Das ist unerwünscht — ein Einzeltermin ohne Uhrzeit ist
eine unvollständige Eingabe. Deshalb zusätzlich ablehnen, wenn kein Trennzeichen vorhanden war:

```python
def parse_datetime(value) -> datetime | None:
    """YYYY-MM-DD HH:MM oder mit T als Trenner. Sonst None.

    Ein reines Datum ohne Uhrzeit wird abgelehnt: es waere eine unvollstaendige
    Eingabe, die stillschweigend als Mitternacht durchginge.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace(" ", "T")
    if "T" not in text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
```

In `parse_config` vor dem Konstruktoraufruf:

```python
    raw_shots = cam_get("oneshots", [])
    shots = {parse_datetime(v) for v in raw_shots} if isinstance(raw_shots, list) else set()
    shots.discard(None)
```

und im Konstruktoraufruf `oneshots=tuple(sorted(shots))[:MAX_ONESHOTS],`.

`next_due` wird aufgeteilt, damit die Zeitraum-Begrenzung nur den wiederkehrenden Teil betrifft:

```python
def next_due(
    now: datetime,
    cfg: ScheduleConfig,
    last_capture: datetime | None,
) -> datetime | None:
    """Nächster Aufnahmezeitpunkt aus wiederkehrendem Plan und Einzelterminen.

    Einzeltermine tragen ihr Datum selbst und sind vom Zeitraum unberuehrt.
    Geliefert wird der frueheste Kandidat, oder None wenn es keinen gibt.
    """
    candidates = [
        c
        for c in (_recurring_next(now, cfg, last_capture), _oneshot_next(now, cfg))
        if c is not None
    ]
    return min(candidates) if candidates else None


def _recurring_next(
    now: datetime, cfg: ScheduleConfig, last_capture: datetime | None
) -> datetime | None:
    if cfg.date_from is not None and now.date() < cfg.date_from:
        return None
    candidate = (
        _times_next(now, cfg)
        if cfg.mode == MODE_TIMES
        else _interval_next(now, cfg, last_capture)
    )
    if candidate is None:
        return None
    if cfg.date_to is not None and candidate.date() > cfg.date_to:
        return None
    return candidate


def _oneshot_next(now: datetime, cfg: ScheduleConfig) -> datetime | None:
    for moment in cfg.oneshots:
        if moment > now:
            return moment
    return None
```

- [ ] **Step 4: Tests ausführen**

Run: `python -m pytest tests/test_schedule_oneshots.py -v`
Expected: 10 passed

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 5: Commit**

```bash
git add services/schedule.py tests/test_schedule_oneshots.py
git commit -m "feat(schedule): add one-shot appointments with their own date"
```

---

### Ergänzungen zu Task 6, 7 und 8

Diese Punkte werden in die jeweiligen Tasks eingearbeitet, nicht separat ausgeführt.

**Task 6** liefert im Status zusätzlich `date_from`, `date_to` und `oneshots`, und das
Wake-Muster in `api/settings.py` erfasst die neuen Schlüssel. Neue Defaults in
`db/database.py`:

```python
    "cam_0_date_from": None,
    "cam_0_date_to": None,
    "cam_0_oneshots": [],
```

**Task 7** ergänzt oberhalb des Modus-Umschalters einen Zeitraum-Block und unterhalb der
Uhrzeitfelder einen Block für Einzeltermine:

```
Zeitraum von  [01.04.2026]   bis  [30.06.2026]   (beide optional)

Zeitplan   ( ) Intervall   ( ) Feste Uhrzeiten
   ... wie bisher ...

Einzeltermine
  1. [01.04.2026] [08:00]   [x]
  2. [15.04.2026] [08:00]   [x]
  [+ Termin hinzufügen]
```

Einzeltermine sind eine dynamische Liste mit Hinzufügen- und Entfernen-Knopf, höchstens 50.
Ein Termin zählt nur, wenn beide Felder gefüllt sind; unvollständige Zeilen werden beim
Speichern verworfen. Bereits vergangene Termine werden ausgegraut dargestellt, aber nicht
gelöscht — der Nutzer soll sehen, was er eingetragen hatte.

**Task 8** speichert die neuen Felder mit: `cam_N_date_from` und `cam_N_date_to` als
`"YYYY-MM-DD"` oder `null`, `cam_N_oneshots` als Liste von `"YYYY-MM-DD HH:MM"`. Die Anzeige
„Nächste Aufnahme" nennt bei einem Zeitpunkt, der nicht heute liegt, zusätzlich das Datum.
