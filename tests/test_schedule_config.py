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
