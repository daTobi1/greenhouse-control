"""Feuchteregelung nach Dampfdruckdefizit statt nach relativer Feuchte.

Die Fälle sind so gewählt, dass sie zwischen den beiden Regelgrößen wirklich
unterscheiden. Bei 24 °C / 90 % etwa liefern beide volle Drehzahl – so ein Fall
würde auch dann grün bleiben, wenn der VPD-Zweig gar nicht existiert, und
prüft deshalb nichts.
"""

import pytest

from services.fan_controller import FanController
from services import psychrometrics as psy


def _settings(**over):
    base = {
        "target_temperature": 25.0,
        "target_humidity": 65.0,
        "temp_control_range": 5.0,
        "humidity_control_range": 20.0,
        "fan_min_speed": 0.2,
        "fan_max_speed": 1.0,
        "control_mode": "humidity",
        "fan_min_temperature": 5.0,
        "humidity_abs_margin": 0.5,
        "humidity_temp_guard": 3.0,
        "fan_start_threshold": 0.10,
        "fan_stop_threshold": 0.03,
        "fan_min_runtime": 0.0,
        "fan_min_pause": 0.0,
        "humidity_metric": "vpd",
        "target_vpd": 0.95,
        "vpd_control_range": 0.40,
    }
    base.update(over)
    return base


def _air(temp, hum):
    return {"temperature": temp, "humidity": hum}


TROCKEN_AUSSEN = {"temperature": 10.0, "humidity": 60.0}   # 5,6 g/m3


def _speed(inside, **over):
    fan = FanController()
    return fan.calculate_speed(inside, TROCKEN_AUSSEN, _settings(**over), now=0.0)


def _erwartet(roh, fan_min=0.2, fan_max=1.0):
    return fan_min + roh * (fan_max - fan_min)


# --- Fälle, in denen sich die beiden Regelgrößen unterscheiden ---

def test_warm_and_humid_needs_far_less_than_the_relative_rule_says():
    """30 °C / 80 %: VPD 0,85 – fast am Ziel. Die relative Regel sähe 15 %
    über dem Sollwert und würde mit 0,75 Rohwert lüften."""
    d = _speed(_air(30.0, 80.0), target_temperature=30.0)
    roh = (0.95 - psy.vpd(30.0, 80.0)) / 0.40
    assert d.speed == pytest.approx(_erwartet(roh), abs=0.01)
    assert d.speed < _erwartet(0.75)


def test_cool_and_humid_needs_more_than_the_relative_rule_says():
    """15 °C / 80 %: VPD 0,34 – weit unter Ziel, volle Drehzahl. Die relative
    Regel käme bei denselben 80 % nur auf 0,75."""
    d = _speed(_air(15.0, 80.0), target_temperature=15.0)
    assert d.speed == pytest.approx(1.0)


def test_at_target_vpd_the_fan_stays_off_although_relative_humidity_is_high():
    """24 °C / 68 % ist genau das Ziel-VPD. Nach relativer Feuchte läge der
    Wert drei Punkte über dem Sollwert und der Lüfter liefe."""
    d = _speed(_air(24.0, 68.0), target_temperature=24.0)
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_combined_and_uses_the_vpd_share():
    """28 °C / 75 %: VPD praktisch am Ziel, Temperatur 0,6 über dem Bereich.
    Nach relativer Feuchte wäre der kleinere Anteil 0,5 und der Lüfter liefe;
    nach VPD ist er 0,01 und bleibt unter der Einschaltschwelle."""
    d = _speed(_air(28.0, 75.0), control_mode="combined_and")
    assert d.speed == 0.0
    assert d.reason == "idle"


# --- Verhalten, das unabhängig von der Regelgröße gelten muss ---

def test_does_not_vent_when_the_air_is_too_dry():
    """Ein Abluftlüfter kann nicht befeuchten."""
    d = _speed(_air(24.0, 40.0), target_temperature=24.0)
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_temperature_guard_still_applies():
    d = _speed(_air(21.0, 95.0))
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_absolute_humidity_guard_still_applies():
    """Außenluft mit mehr Wasser bringt nichts, egal wie das VPD steht."""
    fan = FanController()
    feucht_aussen = {"temperature": 30.0, "humidity": 90.0}   # 27 g/m3
    d = fan.calculate_speed(_air(24.0, 90.0), feucht_aussen,
                            _settings(target_temperature=24.0), now=0.0)
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_frost_protection_still_wins():
    fan = FanController()
    d = fan.calculate_speed(_air(4.0, 95.0), TROCKEN_AUSSEN, _settings(), now=0.0)
    assert d.reason == "frost"


# --- Verträglichkeit ---

def test_relative_mode_is_unchanged():
    d = _speed(_air(24.0, 68.0), target_temperature=24.0, humidity_metric="relative")
    # 68 % liegen drei Punkte über dem Ziel: (68-65)/20 = 0,15
    assert d.speed == pytest.approx(_erwartet(0.15), abs=0.01)


def test_missing_setting_falls_back_to_vpd():
    """Ohne gesetzten Schlüssel gilt die Anlagenvorgabe, und die ist VPD.

    Bestehende Installationen sind davon nicht betroffen: dort steht der
    Schlüssel bereits in der Datenbank und behält seinen Wert.
    """
    ohne = _settings(target_temperature=24.0)
    del ohne["humidity_metric"]
    fan = FanController()
    d = fan.calculate_speed(_air(24.0, 68.0), TROCKEN_AUSSEN, ohne, now=0.0)
    # 24 °C / 68 % ergibt genau die 0,95 kPa, die diese Testdatei als Ziel
    # setzt: kein Fehler, kein Lüften. Nach relativer Feuchte läge der Wert
    # drei Punkte über dem Sollwert und der Lüfter liefe.
    assert d.speed == 0.0


def test_unknown_metric_falls_back_to_relative():
    d = _speed(_air(24.0, 68.0), target_temperature=24.0, humidity_metric="unsinn")
    assert d.speed == pytest.approx(_erwartet(0.15), abs=0.01)


def test_default_target_matches_the_database_default():
    """Der Vorgabewert steht an vier Stellen – Datenbank, Regler, Feld und
    Anzeige. Laufen sie auseinander, regelt der Regler auf etwas anderes als
    im Dashboard steht."""
    from db.database import DEFAULT_SETTINGS

    ohne = _settings(target_temperature=30.0)
    del ohne["target_vpd"]

    # 30 °C / 80 % ergibt VPD 0,85: knapp ueber der Vorgabe 0,80, also zu
    # trocken – der Luefter bleibt aus. Mit der frueheren Vorgabe 0,95 waere
    # er angelaufen.
    fan = FanController()
    d = fan.calculate_speed(_air(30.0, 80.0), TROCKEN_AUSSEN, ohne, now=0.0)
    assert DEFAULT_SETTINGS["target_vpd"] == 0.80
    assert psy.vpd(30.0, 80.0) > DEFAULT_SETTINGS["target_vpd"]
    assert d.speed == 0.0
