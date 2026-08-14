import inspect

import pytest

from services.camera import (
    CameraBusy,
    CameraService,
    CameraUnavailable,
    camera_setup_kwargs,
)


# ----------------------------------------------------------------------
# camera_setup_kwargs
# ----------------------------------------------------------------------

def test_returned_keys_match_setup_signature():
    """Die Keys müssen exakt zu setup()'s Parametern (ohne self) passen,
    sonst schlägt cam.setup(**kwargs) fehl."""
    params = [
        name for name in inspect.signature(CameraService.setup).parameters
        if name != "self"
    ]
    kwargs = camera_setup_kwargs({}, 0, "timelapse")
    assert set(kwargs.keys()) == set(params)


def test_per_camera_key_wins_over_legacy():
    settings = {"cam_0_device_index": 3, "camera_index": 9}
    kwargs = camera_setup_kwargs(settings, 0, "timelapse")
    assert kwargs["camera_index"] == 3


def test_camera_slot_never_falls_back_to_legacy():
    """Nur Kamera 0 kennt die alten, kameraunabhängigen Schlüssel."""
    settings = {"camera_index": 9, "camera_capture_width": 640}
    kwargs = camera_setup_kwargs(settings, 2, "timelapse")
    assert kwargs["camera_index"] == 2  # default == cam, nicht 9
    assert kwargs["capture_width"] == 0  # default 0, nicht 640


def test_null_per_camera_value_falls_back_to_legacy_for_cam0():
    """PUT /api/settings mit JSON null darf nicht als 'gesetzt' zählen."""
    settings = {"cam_0_device_index": None, "camera_index": 5}
    kwargs = camera_setup_kwargs(settings, 0, "timelapse")
    assert kwargs["camera_index"] == 5


def test_null_per_camera_value_falls_back_to_default_for_cam2():
    settings = {"cam_2_device_index": None}
    kwargs = camera_setup_kwargs(settings, 2, "timelapse")
    assert kwargs["camera_index"] == 2


def test_null_legacy_value_falls_back_to_default_for_cam0():
    settings = {"camera_index": None}
    kwargs = camera_setup_kwargs(settings, 0, "timelapse")
    assert kwargs["camera_index"] == 0


def test_null_values_do_not_raise_on_float_and_int_conversion():
    """Vor dem Fix führte dies zu TypeError: int()/float() konnten None
    nicht konvertieren – das crashte Startup, Settings-PUT und den
    Timelapse-Loop."""
    settings = {
        "cam_0_device_index": None,
        "cam_0_capture_width": None,
        "cam_0_capture_height": None,
        "cam_0_fourcc": None,
        "cam_0_target_brightness": None,
        "cam_0_brightness_tol": None,
        "cam_0_warmup_seconds": None,
    }
    kwargs = camera_setup_kwargs(settings, 0, "timelapse")
    assert kwargs["camera_index"] == 0
    assert kwargs["capture_width"] == 0
    assert kwargs["capture_height"] == 0
    assert kwargs["target_brightness"] == 120.0
    assert kwargs["brightness_tol"] == 12.0
    assert kwargs["warmup_seconds"] == 1.5


# ----------------------------------------------------------------------
# capture_preview: CameraBusy propagates, CameraUnavailable is swallowed
# ----------------------------------------------------------------------

class _RaisingCapture:
    """Context manager stand-in for _open_capture that raises on __enter__."""

    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        raise self._exc

    def __exit__(self, exc_type, exc, tb):
        return False


def test_capture_preview_swallows_camera_unavailable():
    cs = CameraService(camera_id=0)
    cs._open_capture = lambda timeout=None: _RaisingCapture(
        CameraUnavailable("Kamera 0 lässt sich nicht öffnen")
    )
    assert cs.capture_preview() is None


def test_capture_preview_propagates_camera_busy():
    cs = CameraService(camera_id=0)
    cs._open_capture = lambda timeout=None: _RaisingCapture(
        CameraBusy("Kamera 0 ist belegt")
    )
    with pytest.raises(CameraBusy):
        cs.capture_preview()
