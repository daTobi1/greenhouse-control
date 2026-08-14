import pytest

from services import camera as camera_mod
from services import device_lock
from services.camera import CameraService, CameraUnavailable


def test_fourcc_default_is_mjpg():
    cs = CameraService(camera_id=0)
    assert cs._fourcc == "MJPG"


def test_setup_accepts_fourcc(tmp_path):
    cs = CameraService(camera_id=0)
    cs.setup(
        frames_dir=str(tmp_path / "f"),
        output_dir=str(tmp_path / "o"),
        camera_index=2,
        fourcc="YUYV",
    )
    assert cs._fourcc == "YUYV"
    assert cs._camera_index == 2


def test_empty_fourcc_falls_back_to_mjpg(tmp_path):
    cs = CameraService(camera_id=0)
    cs.setup(frames_dir=str(tmp_path / "f"), output_dir=str(tmp_path / "o"), fourcc="")
    assert cs._fourcc == "MJPG"


# --- Verhalten von _open_capture ---

pytestmark_cv2 = pytest.mark.skipif(
    not camera_mod.CV2_AVAILABLE, reason="OpenCV nicht verfügbar"
)

DEVICE_INDEX = 97  # eigener Index, damit kein anderer Test denselben Lock nutzt


class RecordingCapture:
    def __init__(self, index, opened=True):
        self.index = index
        self.sets: list[tuple] = []
        self.released = False
        self._opened = opened

    def isOpened(self):
        return self._opened

    def set(self, pid, value):
        self.sets.append((pid, value))
        return True

    def release(self):
        self.released = True


def _service(index=DEVICE_INDEX, width=1280, height=720):
    cs = CameraService(camera_id=0)
    cs._camera_index = index
    cs._capture_width = width
    cs._capture_height = height
    return cs


@pytestmark_cv2
def test_fourcc_is_set_before_width_and_height(monkeypatch):
    """Zuerst die Breite zu setzen lässt die Kamera kurz auf einem ungültigen
    Format stehen – der Treiber snapped dann auf etwas anderes."""
    created = []
    monkeypatch.setattr(
        camera_mod.cv2,
        "VideoCapture",
        lambda idx: created.append(RecordingCapture(idx)) or created[-1],
    )
    cs = _service()
    with cs._open_capture():
        pass

    order = [pid for pid, _ in created[0].sets]
    assert camera_mod.cv2.CAP_PROP_FOURCC in order
    assert order.index(camera_mod.cv2.CAP_PROP_FOURCC) < order.index(
        camera_mod.cv2.CAP_PROP_FRAME_WIDTH
    )
    assert order.index(camera_mod.cv2.CAP_PROP_FRAME_WIDTH) < order.index(
        camera_mod.cv2.CAP_PROP_FRAME_HEIGHT
    )


@pytestmark_cv2
def test_device_lock_is_held_and_released(monkeypatch):
    monkeypatch.setattr(
        camera_mod.cv2, "VideoCapture", lambda idx: RecordingCapture(idx)
    )
    lock = device_lock.get(DEVICE_INDEX)
    cs = _service()
    with cs._open_capture():
        assert lock.locked(), "Gerät muss während der Nutzung gesperrt sein"
    assert not lock.locked(), "Lock muss danach wieder frei sein"


@pytestmark_cv2
def test_device_lock_released_when_capture_fails(monkeypatch):
    monkeypatch.setattr(
        camera_mod.cv2, "VideoCapture", lambda idx: RecordingCapture(idx, opened=False)
    )
    lock = device_lock.get(DEVICE_INDEX)
    cs = _service()
    with pytest.raises(CameraUnavailable):
        with cs._open_capture():
            pass
    assert not lock.locked(), "Lock muss auch nach einem Fehler frei sein"


@pytestmark_cv2
def test_device_lock_released_when_body_raises(monkeypatch):
    captures = []
    monkeypatch.setattr(
        camera_mod.cv2,
        "VideoCapture",
        lambda idx: captures.append(RecordingCapture(idx)) or captures[-1],
    )
    lock = device_lock.get(DEVICE_INDEX)
    cs = _service()
    with pytest.raises(RuntimeError):
        with cs._open_capture():
            raise RuntimeError("Aufnahme abgebrochen")
    assert not lock.locked()
    assert captures[0].released, "Kamera muss auch bei Fehler freigegeben werden"
