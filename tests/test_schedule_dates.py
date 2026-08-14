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
