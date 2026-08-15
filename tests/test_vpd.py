import pytest

from services import psychrometrics as psy


# Referenzwerte aus der Magnus-Formel, Toleranz 2 %.
@pytest.mark.parametrize(
    "temp, expected",
    [
        (0.0, 0.611),
        (20.0, 2.339),
        (24.0, 2.984),
        (30.0, 4.243),
    ],
)
def test_saturation_pressure_matches_tables(temp, expected):
    assert psy.saturation_pressure(temp) == pytest.approx(expected, rel=0.02)


@pytest.mark.parametrize(
    "temp, rh, expected",
    [
        (24.0, 68.0, 0.95),   # üblicher Sollwert im Gewächshaus
        (15.0, 80.0, 0.34),   # kühler Morgen
        (30.0, 80.0, 0.85),   # heißer Sommertag
        (24.0, 40.0, 1.79),   # zu trocken
        (18.0, 95.0, 0.10),   # Nacht, dicht am Taupunkt
    ],
)
def test_vpd_matches_reference_values(temp, rh, expected):
    assert psy.vpd(temp, rh) == pytest.approx(expected, abs=0.02)


def test_vpd_is_zero_at_saturation():
    assert psy.vpd(22.0, 100.0) == pytest.approx(0.0, abs=0.001)


def test_same_relative_humidity_gives_very_different_vpd():
    """Der Grund, warum die relative Feuchte als Sollwert allein nicht taugt."""
    kalt = psy.vpd(15.0, 80.0)
    warm = psy.vpd(30.0, 80.0)
    assert warm > 2 * kalt


def test_vpd_rises_as_air_dries():
    assert psy.vpd(24.0, 40.0) > psy.vpd(24.0, 60.0) > psy.vpd(24.0, 80.0)


def test_vpd_rises_with_temperature_at_equal_relative_humidity():
    assert psy.vpd(30.0, 70.0) > psy.vpd(20.0, 70.0)


def test_vpd_never_negative():
    # rel_hum wird auf 100 begrenzt; ein Messfehler darf kein negatives VPD ergeben.
    assert psy.vpd(20.0, 120.0) >= 0.0
