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
