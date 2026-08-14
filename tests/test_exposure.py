import numpy as np
import pytest

from services import exposure


def _solid(value, shape=(48, 64, 3)):
    return np.full(shape, value, dtype=np.uint8)


# --- measure_brightness ---

@pytest.mark.parametrize("value", [0, 128, 255])
def test_measure_solid_gray(value):
    assert measured_close(exposure.measure_brightness(_solid(value)), value)


def measured_close(a, b, tol=1.0):
    return abs(a - b) <= tol


def test_measure_grayscale_frame():
    frame = np.full((48, 64), 100, dtype=np.uint8)
    assert measured_close(exposure.measure_brightness(frame), 100)


def test_center_weighted_higher_than_edge_weighted():
    """Gleich viele helle Pixel, einmal in der Mitte, einmal am Rand."""
    center = _solid(0)
    center[12:36, 16:48] = 255

    edge = _solid(0)
    edge[0:12, :] = 255
    edge[36:48, :] = 255

    assert exposure.measure_brightness(center) > exposure.measure_brightness(edge)


def test_measure_pure_blue_channel():
    """Pure blue (BGR index 0). Uniform frame: center and edge weights cancel out."""
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # Blue channel
    expected = 255 * 0.114  # 29.07
    assert exposure.measure_brightness(frame) == pytest.approx(expected, abs=1.0)


def test_measure_pure_green_channel():
    """Pure green (BGR index 1). Uniform frame: center and edge weights cancel out."""
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 1] = 255  # Green channel
    expected = 255 * 0.587  # 149.685
    assert exposure.measure_brightness(frame) == pytest.approx(expected, abs=1.0)


def test_measure_pure_red_channel():
    """Pure red (BGR index 2). Uniform frame: center and edge weights cancel out."""
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:, :, 2] = 255  # Red channel
    expected = 255 * 0.299  # 76.245
    assert exposure.measure_brightness(frame) == pytest.approx(expected, abs=1.0)


# --- within_tolerance ---

def test_within_tolerance_boundaries():
    assert exposure.within_tolerance(120, 120, 12)
    assert exposure.within_tolerance(132, 120, 12)
    assert exposure.within_tolerance(108, 120, 12)
    assert not exposure.within_tolerance(133, 120, 12)
    assert not exposure.within_tolerance(107, 120, 12)


# --- correction_factor ---

def test_correction_on_target_is_neutral():
    assert exposure.correction_factor(120, 120) == pytest.approx(1.0)


def test_correction_too_dark_increases():
    assert exposure.correction_factor(60, 120) > 1.0


def test_correction_too_bright_decreases():
    assert exposure.correction_factor(240, 120) < 1.0


def test_correction_is_damped():
    """Ungedämpft wäre der Faktor 2.0; mit damping=0.8 nur 1.8."""
    assert exposure.correction_factor(60, 120) == pytest.approx(1.8)


def test_correction_clamped_upper():
    assert exposure.correction_factor(5, 250) == 4.0


def test_correction_clamped_lower():
    assert exposure.correction_factor(255, 10) == 0.25


def test_correction_black_frame_does_not_raise():
    assert exposure.correction_factor(0, 120) == 4.0


# --- software_gain ---

def test_software_gain_clamped():
    assert exposure.software_gain(50, 120) == pytest.approx(1.3)
    assert exposure.software_gain(250, 60) == pytest.approx(0.7)


def test_software_gain_none_when_close_enough():
    assert exposure.software_gain(120, 121) is None


def test_software_gain_none_on_black_frame():
    assert exposure.software_gain(0, 120) is None
