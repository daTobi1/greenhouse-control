import pytest

from services.fan_controller import FanController


def _settings(**over):
    base = {
        "target_temperature": 25.0,
        "temp_control_range": 5.0,
        "fan_min_speed": 0.2,
        "fan_max_speed": 1.0,
        "control_mode": "temperature",
        "fan_min_temperature": 5.0,
        "fan_start_threshold": 0.10,
        "fan_stop_threshold": 0.03,
        "fan_min_runtime": 120.0,
        "fan_min_pause": 60.0,
    }
    base.update(over)
    return base


def _inside(raw):
    """Innenluft, die genau den gewünschten Rohwert erzeugt."""
    return {"temperature": 25.0 + raw * 5.0, "humidity": 50.0}


COLD_OUTSIDE = {"temperature": 5.0, "humidity": 50.0}


def _run(fan, raw, now, **over):
    return fan.calculate_speed(_inside(raw), COLD_OUTSIDE, _settings(**over), now=now)


def test_stays_off_below_start_threshold():
    fan = FanController()
    assert _run(fan, 0.05, now=0.0).speed == 0.0


def test_starts_at_start_threshold():
    fan = FanController()
    d = _run(fan, 0.10, now=0.0)
    assert d.speed > 0.0
    assert d.reason == "auto"


def test_keeps_running_between_thresholds():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    d = _run(fan, 0.05, now=200.0)
    assert d.speed > 0.0
    assert d.reason == "auto"


def test_stops_below_stop_threshold():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    d = _run(fan, 0.01, now=200.0)
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_min_runtime_holds_the_fan_on():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    d = _run(fan, 0.0, now=30.0)
    assert d.speed == pytest.approx(0.2)
    assert d.reason == "min_runtime"


def test_min_runtime_releases_after_the_period():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    _run(fan, 0.0, now=30.0)
    d = _run(fan, 0.0, now=130.0)
    assert d.speed == 0.0


def test_min_pause_holds_the_fan_off():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    _run(fan, 0.0, now=200.0)          # schaltet ab bei t=200
    d = _run(fan, 0.50, now=230.0)
    assert d.speed == 0.0
    assert d.reason == "min_pause"


def test_min_pause_releases_after_the_period():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    _run(fan, 0.0, now=200.0)
    d = _run(fan, 0.50, now=270.0)
    assert d.speed > 0.0
    assert d.reason == "auto"


def test_first_start_is_not_delayed_by_min_pause():
    """Frisch gestarteter Dienst darf nicht erst eine Pause absitzen."""
    fan = FanController()
    assert _run(fan, 0.50, now=5.0).speed > 0.0


def test_frost_overrides_min_runtime():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    d = fan.calculate_speed(
        {"temperature": 4.0, "humidity": 90.0}, COLD_OUTSIDE, _settings(), now=10.0
    )
    assert d.speed == 0.0
    assert d.reason == "frost"


def test_missing_sensor_overrides_min_runtime():
    fan = FanController()
    _run(fan, 0.50, now=0.0)
    d = fan.calculate_speed(None, COLD_OUTSIDE, _settings(), now=10.0)
    assert d.speed == 0.0
    assert d.reason == "no_inside_data"


def test_speed_scales_into_the_configured_range():
    fan = FanController()
    d = _run(fan, 1.0, now=0.0, fan_min_speed=0.3, fan_max_speed=0.9)
    assert d.speed == pytest.approx(0.9)
