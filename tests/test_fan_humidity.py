import pytest

from services.fan_controller import FanController


def _settings(**over):
    base = {
        "target_temperature": 25.0,
        "target_humidity": 65.0,
        "temp_control_range": 5.0,
        "humidity_control_range": 20.0,
        "fan_min_speed": 0.2,
        "fan_max_speed": 1.0,
        "fan_deadband": 0.1,
        "control_mode": "combined_or",
        "fan_min_temperature": 5.0,
        "humidity_abs_margin": 0.5,
        "humidity_temp_guard": 3.0,
        # Diese Datei prueft den Zweig fuer relative Feuchte. Ausdruecklich
        # gesetzt, weil die Vorgabe der Anlage inzwischen VPD ist.
        "humidity_metric": "relative",
    }
    base.update(over)
    return base


def _air(temp, hum):
    return {"temperature": temp, "humidity": hum}


def test_vents_when_outside_is_absolutely_drier():
    """Innen 25/70 = 16.1 g/m3, außen 10/90 = 8.5 g/m3.

    Die alte Logik verbot das Lüften, weil 90 % > 70 % – obwohl die Außenluft
    objektiv nur halb so viel Wasser enthält.
    """
    fan = FanController()
    d = fan.calculate_speed(_air(25.0, 70.0), _air(10.0, 90.0), _settings())
    assert d.speed == pytest.approx(0.2 + 0.25 * 0.8)
    assert d.reason == "auto"


def test_does_not_vent_when_outside_is_absolutely_wetter():
    """Innen 20/80 = 13.8 g/m3, außen 30/60 = 18.2 g/m3.

    Die alte Logik hätte gelüftet, weil 60 % < 80 % – und damit Feuchte
    hereingeholt.
    """
    fan = FanController()
    d = fan.calculate_speed(_air(20.0, 80.0), _air(30.0, 60.0), _settings(control_mode="humidity"))
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_margin_blocks_marginal_differences():
    """24 °C liegt über dem Schutzabstand, damit wirklich die Marge greift.

    Innen 24/80 = 17.41 g/m3, außen 24/79 = 17.19 g/m3 – 0.22 g/m3 Unterschied
    liegt unter der Marge von 0.5 und ist als Messrauschen zu werten.
    """
    fan = FanController()
    d = fan.calculate_speed(
        _air(24.0, 80.0), _air(24.0, 79.0), _settings(control_mode="humidity")
    )
    assert d.speed == 0.0


def test_difference_above_the_margin_vents():
    """Gegenprobe: 2.18 g/m3 Unterschied liegt über der Marge."""
    fan = FanController()
    d = fan.calculate_speed(
        _air(24.0, 80.0), _air(24.0, 70.0), _settings(control_mode="humidity")
    )
    assert d.speed == pytest.approx(0.2 + 0.75 * 0.8)
    assert d.reason == "auto"


def test_humidity_branch_blocked_below_temperature_guard():
    """21 °C liegt unter 25 - 3 – entfeuchten würde weiter auskühlen."""
    fan = FanController()
    d = fan.calculate_speed(
        _air(21.0, 85.0), _air(5.0, 90.0), _settings(control_mode="humidity")
    )
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_humidity_branch_allowed_with_wider_guard():
    fan = FanController()
    d = fan.calculate_speed(
        _air(21.0, 85.0), _air(5.0, 90.0),
        _settings(control_mode="humidity", humidity_temp_guard=5.0),
    )
    assert d.speed > 0.0
    assert d.reason == "auto"


def test_temperature_branch_unchanged():
    fan = FanController()
    d = fan.calculate_speed(
        _air(27.5, 50.0), _air(18.0, 50.0), _settings(control_mode="temperature")
    )
    assert d.speed == pytest.approx(0.2 + 0.5 * 0.8)


def test_temperature_branch_needs_cooler_outside_air():
    fan = FanController()
    d = fan.calculate_speed(
        _air(27.5, 50.0), _air(30.0, 50.0), _settings(control_mode="temperature")
    )
    assert d.speed == 0.0


def test_combined_and_takes_the_minimum():
    fan = FanController()
    d = fan.calculate_speed(
        _air(30.0, 70.0), _air(10.0, 40.0), _settings(control_mode="combined_and")
    )
    # Temperatur: (30-25)/5 = 1.0, Feuchte: (70-65)/20 = 0.25 -> min = 0.25
    assert d.speed == pytest.approx(0.2 + 0.25 * 0.8)


def test_combined_or_takes_the_maximum():
    fan = FanController()
    d = fan.calculate_speed(
        _air(30.0, 70.0), _air(10.0, 40.0), _settings(control_mode="combined_or")
    )
    assert d.speed == pytest.approx(1.0)


def test_missing_inside_data():
    fan = FanController()
    d = fan.calculate_speed(None, _air(10.0, 40.0), _settings())
    assert d.speed == 0.0
    assert d.reason == "no_inside_data"


def test_missing_outside_data():
    fan = FanController()
    d = fan.calculate_speed(_air(30.0, 70.0), None, _settings())
    assert d.speed == 0.0
    assert d.reason == "no_outside_data"


def test_frost_protection():
    fan = FanController()
    d = fan.calculate_speed(_air(4.0, 90.0), _air(0.0, 50.0), _settings())
    assert d.speed == 0.0
    assert d.reason == "frost"
