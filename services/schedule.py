"""Zeitplanung für Timelapse-Aufnahmen.

Reine Funktionen: `now` wird immer übergeben, nie intern gelesen. Dadurch ist
die gesamte Logik ohne Uhr, ohne Datenbank und ohne Kamera testbar.

Es wird in lokaler Zeit mit naiven datetime-Objekten gerechnet. Bei der
Zeitumstellung kann ein geplanter Zeitpunkt doppelt oder gar nicht auftreten –
bewusst akzeptiert, das Kulanzfenster fängt den Normalfall ab.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

MODE_INTERVAL = "interval"
MODE_TIMES = "times"

DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_GRACE_SECONDS = 300.0

# Mehr als 24 Aufnahmen pro Tag über feste Uhrzeiten zu pflegen ist im
# Dashboard nicht mehr sinnvoll bedienbar.
MAX_TIMES = 24

# Mehr als 50 Einzeltermine sind im Dashboard nicht mehr sinnvoll pflegbar.
MAX_ONESHOTS = 50


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
    oneshots: tuple[datetime, ...] = ()


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


def parse_date(value) -> date | None:
    """YYYY-MM-DD in ein date-Objekt. Sonst None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


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

    raw_shots = cam_get("oneshots", [])
    shots = {parse_datetime(v) for v in raw_shots} if isinstance(raw_shots, list) else set()
    shots.discard(None)

    return ScheduleConfig(
        mode=mode,
        interval_seconds=float(
            cam_get("timelapse_interval", DEFAULT_INTERVAL_SECONDS, "timelapse_interval")
        ),
        start=parse_time(cam_get("schedule_start", None)),
        end=parse_time(cam_get("schedule_end", None)),
        times=times,
        grace_seconds=float(cam_get("schedule_grace", DEFAULT_GRACE_SECONDS)),
        date_from=parse_date(cam_get("date_from", None)),
        date_to=parse_date(cam_get("date_to", None)),
        oneshots=tuple(sorted(shots))[:MAX_ONESHOTS],
    )


def next_due(
    now: datetime,
    cfg: ScheduleConfig,
    last_capture: datetime | None,
) -> datetime | None:
    """Nächster Aufnahmezeitpunkt aus wiederkehrendem Plan und Einzelterminen.

    Es wird nie ein Zeitpunkt in der Vergangenheit geliefert; verpasste
    Aufnahmen verfallen dadurch von selbst. Einzeltermine tragen ihr Datum
    selbst und sind vom Zeitraum unberuehrt. Geliefert wird der frueheste
    Kandidat, oder None wenn es keinen gibt.
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
    """Nächster Zeitpunkt aus dem wiederkehrenden Plan (Intervall oder Uhrzeiten).

    Der optionale Zeitraum date_from..date_to begrenzt den Plan. date_to ist
    einschliesslich: am Enddatum wird noch aufgenommen. Vor date_from wird None
    geliefert; der Loop fragt beim naechsten Durchlauf erneut.
    """
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
    """Nächster noch bevorstehender Einzeltermin, oder None."""
    for moment in cfg.oneshots:
        if moment > now:
            return moment
    return None


def _times_next(now: datetime, cfg: ScheduleConfig) -> datetime | None:
    if not cfg.times:
        return None
    for t in cfg.times:
        candidate = datetime.combine(now.date(), t)
        if candidate > now:
            return candidate
    return datetime.combine(now.date() + timedelta(days=1), cfg.times[0])


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
    # Tages.

    # Erste-Tag-Regel: Solange nichts aufgenommen wurde und die heutige Startzeit
    # noch bevorsteht, wird auf sie gewartet. Ohne diese Regel wuerde eine um 04:00
    # gestartete Aufnahme sofort auf dem Raster von gestern ausloesen.
    if cfg.end is None and last_capture is None:
        today_start = datetime.combine(now.date(), cfg.start)
        if now < today_start:
            return today_start

    # Versatz -1 deckt zwei Faelle ab: ein Fenster, das ueber Mitternacht laeuft,
    # und ein unbegrenztes Raster, das nach der ersten Aufnahme lueckenlos
    # weiterlaufen soll.
    offsets = (-1, 0, 1) if (cfg.end is not None or last_capture is not None) else (0, 1)
    for day_offset in offsets:
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
