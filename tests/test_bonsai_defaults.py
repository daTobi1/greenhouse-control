"""Die Vorgabewerte beschreiben ein Schutzhaus für heimische Bonsai.

Diese Bäume brauchen eine kalte Winterruhe. Das Haus schützt vor Frost, Wind
und Dauernässe, soll aber weder heizen noch die Ruhe unterbrechen. Getestet
wird deshalb nicht die einzelne Zahl, sondern das Verhalten, das sich aus dem
Zusammenspiel ergibt.
"""

import pytest

from db.database import DEFAULT_SETTINGS
from services.fan_controller import FanController
from services import psychrometrics as psy


def _profil(**over):
    s = dict(DEFAULT_SETTINGS)
    s.update(over)
    return s


def _air(temp, hum):
    return {"temperature": temp, "humidity": hum}


KALT_TROCKEN = _air(0.0, 70.0)     # 3,4 g/m3
MILD_TROCKEN = _air(12.0, 55.0)    # 5,8 g/m3


# --- Sommer: früh lüften, bevor sich das Haus aufheizt ---

def test_ventilates_above_the_temperature_target():
    fan = FanController()
    d = fan.calculate_speed(_air(26.0, 60.0), MILD_TROCKEN, _profil(), now=0.0)
    assert d.speed > 0.0
    assert d.reason == "auto"


def test_full_speed_four_kelvin_above_the_target():
    """Ein kleines Haus heizt schnell auf; volle Drehzahl darf nicht erst
    weit oben kommen."""
    fan = FanController()
    ziel = DEFAULT_SETTINGS["target_temperature"]
    d = fan.calculate_speed(
        _air(ziel + DEFAULT_SETTINGS["temp_control_range"], 60.0),
        MILD_TROCKEN, _profil(), now=0.0,
    )
    assert d.speed == pytest.approx(1.0)
    assert DEFAULT_SETTINGS["temp_control_range"] <= 4.0


# --- Winter: gegen Dauernässe lüften, aber nie in den Frost ---

def test_dehumidifies_in_the_cold_house():
    """Der wichtigste Fall der Winterruhe: 6 °C bei 95 % rF, draußen deutlich
    trockener. Mit einem engen Schutzabstand liefe die Entfeuchtung nie."""
    fan = FanController()
    d = fan.calculate_speed(_air(6.0, 95.0), KALT_TROCKEN, _profil(), now=0.0)
    assert d.speed > 0.0
    assert d.reason == "auto"


def test_frost_protection_takes_over_at_the_bottom():
    fan = FanController()
    grenze = DEFAULT_SETTINGS["fan_min_temperature"]
    d = fan.calculate_speed(_air(grenze - 0.5, 95.0), KALT_TROCKEN, _profil(), now=0.0)
    assert d.speed == 0.0
    assert d.reason == "frost"


def test_the_humidity_branch_reaches_down_to_the_frost_limit():
    """Schutzabstand und Frostschutz greifen ineinander, ohne Lücke und ohne
    Überschneidung: entfeuchtet wird bis genau dorthin, wo der Frostschutz
    übernimmt."""
    untergrenze = (DEFAULT_SETTINGS["target_temperature"]
                   - DEFAULT_SETTINGS["humidity_temp_guard"])
    assert untergrenze == pytest.approx(DEFAULT_SETTINGS["fan_min_temperature"])


def test_no_ventilation_when_the_outside_air_is_wetter():
    """Milder Nieselregen draußen bringt nichts herein: 8 °C bei 100 % sind
    8,3 g/m³ gegen 6,9 g/m³ drinnen.

    Nicht zu verwechseln mit kaltem Nebel – 4 °C bei 100 % enthalten nur
    6,4 g/m³ und sind trotz gesättigter Luft trockener als das Hausklima.
    Genau diese Verwechslung machte die alte Regelung mit relativer Feuchte.
    """
    fan = FanController()
    nieselregen = _air(8.0, 100.0)   # 8,3 g/m3
    d = fan.calculate_speed(_air(6.0, 95.0), nieselregen, _profil(), now=0.0)
    assert d.speed == 0.0
    assert d.reason == "idle"


def test_cold_fog_is_still_drier_than_the_house():
    """Gegenprobe zum vorigen Fall – hier lüftet die Steuerung zu Recht."""
    fan = FanController()
    nebel = _air(4.0, 100.0)   # 6,4 g/m3
    d = fan.calculate_speed(_air(6.0, 95.0), nebel, _profil(), now=0.0)
    assert d.speed > 0.0


# --- Regelgröße ---

def test_humidity_is_controlled_by_vpd():
    assert DEFAULT_SETTINGS["humidity_metric"] == "vpd"


def test_target_vpd_suits_bonsai():
    """0,80 kPa: genug Verdunstung für kurze Internodien, nicht so viel, dass
    der kleine Ballen austrocknet."""
    assert DEFAULT_SETTINGS["target_vpd"] == 0.80


def test_target_vpd_is_a_sensible_humidity_at_room_temperature():
    ziel = DEFAULT_SETTINGS["target_vpd"]
    # Bei 20 °C entspricht das rund zwei Dritteln relativer Feuchte.
    rh = (1 - ziel / psy.saturation_pressure(20.0)) * 100
    assert 60 <= rh <= 72


def test_controller_defaults_match_the_database():
    """Die Rückfallwerte im Regler müssen dieselben sein wie in der Datenbank –
    sonst regelt eine Anlage ohne gesetzten Schlüssel nach anderen Zahlen, als
    im Dashboard stehen."""
    fan = FanController()
    ohne_alles = {}
    leer = fan.calculate_speed(_air(26.0, 60.0), MILD_TROCKEN, ohne_alles, now=0.0)
    mit = FanController().calculate_speed(_air(26.0, 60.0), MILD_TROCKEN, _profil(), now=0.0)
    assert leer.speed == pytest.approx(mit.speed)

    kalt_leer = FanController().calculate_speed(_air(6.0, 95.0), KALT_TROCKEN, {}, now=0.0)
    kalt_mit = FanController().calculate_speed(_air(6.0, 95.0), KALT_TROCKEN, _profil(), now=0.0)
    assert kalt_leer.speed == pytest.approx(kalt_mit.speed)
