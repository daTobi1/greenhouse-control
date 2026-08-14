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
        "fan_min_runtime": 0.0,
        "fan_min_pause": 0.0,
    }
    base.update(over)
    return base


COLD_OUTSIDE = {"temperature": -5.0, "humidity": 50.0}


def _at(fan, temp, now):
    return fan.calculate_speed(
        {"temperature": temp, "humidity": 50.0}, COLD_OUTSIDE, _settings(), now=now
    )


# --- Frostschutz-Hysterese ---

def test_blocks_below_minimum():
    fan = FanController()
    assert _at(fan, 4.9, now=0.0).reason == "frost"


def test_stays_blocked_inside_the_hysteresis_band():
    """5.5 liegt über der Grenze, aber unter Grenze + 1.0 – noch gesperrt."""
    fan = FanController()
    _at(fan, 4.9, now=0.0)
    assert _at(fan, 5.5, now=10.0).reason == "frost"


def test_releases_above_the_hysteresis_band():
    fan = FanController()
    _at(fan, 4.9, now=0.0)
    assert _at(fan, 6.1, now=20.0).reason != "frost"


def test_stays_released_until_it_drops_below_again():
    fan = FanController()
    _at(fan, 4.9, now=0.0)
    _at(fan, 6.1, now=20.0)
    assert _at(fan, 5.5, now=30.0).reason != "frost"
    assert _at(fan, 4.9, now=40.0).reason == "frost"


# --- Kickstart ---

def test_kickstart_needed_when_starting_from_standstill():
    fan = FanController()
    fan.kickstart_duration = 0.6
    assert fan._needs_kickstart(0.25)


def test_no_kickstart_when_already_running():
    fan = FanController()
    fan.kickstart_duration = 0.6
    fan.set_speed(0.5)
    assert not fan._needs_kickstart(0.3)


def test_no_kickstart_when_stopping():
    fan = FanController()
    fan.kickstart_duration = 0.6
    fan.set_speed(0.5)
    assert not fan._needs_kickstart(0.0)


def test_kickstart_disabled_by_zero_duration():
    fan = FanController()
    fan.kickstart_duration = 0.0
    assert not fan._needs_kickstart(0.25)


def test_set_speed_still_clamps_and_stores():
    fan = FanController()
    fan.set_speed(1.5)
    assert fan.current_speed == 1.0
    fan.set_speed(-0.2)
    assert fan.current_speed == 0.0
