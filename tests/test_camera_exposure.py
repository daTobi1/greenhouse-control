import numpy as np

from services import exposure
from services.camera import (
    CameraService,
    _PROP_AUTO_EXPOSURE,
    _PROP_EXPOSURE,
)


class FakeCap:
    """Kamera-Attrappe: Bildhelligkeit ist proportional zur Belichtungszeit."""

    def __init__(self, start_exposure=100.0, scale=0.5, manual_ok=True):
        self._props = {
            _PROP_EXPOSURE: start_exposure,
            _PROP_AUTO_EXPOSURE: 3.0,
        }
        self._scale = scale
        self._manual_ok = manual_ok
        self.reads = 0

    def read(self):
        self.reads += 1
        value = min(255.0, self._props[_PROP_EXPOSURE] * self._scale)
        return True, np.full((48, 64, 3), value, dtype=np.uint8)

    def get(self, pid):
        return self._props.get(pid, 0.0)

    def set(self, pid, val):
        if pid == _PROP_AUTO_EXPOSURE and not self._manual_ok:
            return False
        self._props[pid] = float(val)
        return True


def _service(target=120.0, tol=12.0):
    cs = CameraService(camera_id=0)
    cs._target_brightness = target
    cs._brightness_tol = tol
    return cs


def test_pulls_dark_frame_towards_target():
    cs = _service()
    cap = FakeCap(start_exposure=100.0)  # Start: Helligkeit 50
    frame = cs._capture_balanced(cap)
    assert exposure.within_tolerance(exposure.measure_brightness(frame), 120.0, 12.0)


def test_pulls_bright_frame_towards_target():
    cs = _service()
    cap = FakeCap(start_exposure=600.0)  # Start: Helligkeit 255 (Anschlag)
    frame = cs._capture_balanced(cap)
    assert exposure.measure_brightness(frame) < 200.0


def test_frame_already_on_target_is_returned_unchanged():
    cs = _service()
    cap = FakeCap(start_exposure=240.0)  # Start: Helligkeit 120
    cs._capture_balanced(cap)
    assert cap.get(_PROP_EXPOSURE) == 240.0, "Keine Korrektur bei erreichtem Ziel"


def test_target_zero_disables_regulation():
    cs = _service(target=0.0)
    cap = FakeCap(start_exposure=100.0)
    frame = cs._capture_balanced(cap)
    assert exposure.measure_brightness(frame) == 50.0
    assert cap.reads == 1


def test_software_gain_fallback_when_manual_unavailable():
    cs = _service()
    cap = FakeCap(start_exposure=100.0, manual_ok=False)
    frame = cs._capture_balanced(cap)
    # Hardware-Regelung nicht möglich, Software-Gain auf 1.3 begrenzt: 50 → 65
    assert 60.0 <= exposure.measure_brightness(frame) <= 70.0


def test_iterations_are_bounded():
    cs = _service(tol=0.0)  # nie erreichbar
    cap = FakeCap(start_exposure=100.0)
    cs._capture_balanced(cap)
    assert cap.reads <= 12, "Regelschleife muss hart begrenzt sein"
