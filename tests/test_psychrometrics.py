import pytest

from services import psychrometrics as psy


# Referenzwerte aus psychrometrischen Tabellen, Toleranz 2 %.
@pytest.mark.parametrize(
    "temp, rh, expected",
    [
        (20.0, 50.0, 8.65),
        (25.0, 70.0, 16.10),
        (10.0, 90.0, 8.46),
        (30.0, 50.0, 15.17),
        (0.0, 100.0, 4.85),
    ],
)
def test_abs_humidity_matches_tables(temp, rh, expected):
    assert psy.abs_humidity(temp, rh) == pytest.approx(expected, rel=0.02)


def test_abs_humidity_scales_linearly_with_rh():
    assert psy.abs_humidity(20.0, 100.0) == pytest.approx(2 * psy.abs_humidity(20.0, 50.0))


def test_abs_humidity_rises_with_temperature():
    assert psy.abs_humidity(30.0, 60.0) > psy.abs_humidity(20.0, 60.0)


def test_cold_saturated_air_holds_less_than_warm_moderate_air():
    """Der Fall, den die alte relative Betrachtung falsch bewertet hat."""
    assert psy.abs_humidity(10.0, 90.0) < psy.abs_humidity(25.0, 70.0)


def test_abs_humidity_handles_negative_temperature():
    assert 2.0 < psy.abs_humidity(-5.0, 80.0) < 3.5


def test_abs_humidity_zero_rh_does_not_raise():
    assert psy.abs_humidity(20.0, 0.0) >= 0.0


@pytest.mark.parametrize(
    "temp, rh, expected",
    [
        (20.0, 50.0, 9.3),
        (25.0, 60.0, 16.7),
        (30.0, 80.0, 26.2),
    ],
)
def test_dew_point_matches_tables(temp, rh, expected):
    assert psy.dew_point(temp, rh) == pytest.approx(expected, abs=0.3)


def test_dew_point_equals_temperature_at_saturation():
    assert psy.dew_point(18.0, 100.0) == pytest.approx(18.0, abs=0.05)


def test_dew_point_below_temperature():
    assert psy.dew_point(22.0, 40.0) < 22.0


def test_dew_point_zero_rh_does_not_raise():
    assert psy.dew_point(20.0, 0.0) < 0.0
