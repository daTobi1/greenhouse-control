"""Zeitplanung für Timelapse-Aufnahmen.

Reine Funktionen: `now` wird immer übergeben, nie intern gelesen. Dadurch ist
die gesamte Logik ohne Uhr, ohne Datenbank und ohne Kamera testbar.

Es wird in lokaler Zeit mit naiven datetime-Objekten gerechnet. Bei der
Zeitumstellung kann ein geplanter Zeitpunkt doppelt oder gar nicht auftreten –
bewusst akzeptiert, das Kulanzfenster fängt den Normalfall ab.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

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


def _interval_next(now, cfg, last_capture):
    return None
