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
