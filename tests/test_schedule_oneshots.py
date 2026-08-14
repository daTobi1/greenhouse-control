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
