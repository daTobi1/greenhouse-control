"""capture_clip muss den VideoWriter nach einem echten Frame dimensionieren.

cap.get(CAP_PROP_FRAME_WIDTH) liefert bei vielen Treibern nur den gesetzten
Wunschwert. Passt er nicht zu den gelieferten Frames, verwirft der VideoWriter
jeden write() stumm und die Clip-Datei bleibt leer.
"""

import numpy as np
import pytest

from services import camera as camera_mod
from services.camera import CameraService

pytestmark = pytest.mark.skipif(
    not camera_mod.CV2_AVAILABLE, reason="OpenCV nicht verfügbar"
)


class LyingCap:
    """Meldet 1920x1080, liefert aber 640x480-Frames."""

    def __init__(self, width=640, height=480):
        self._shape = (height, width, 3)

    def read(self):
        return True, np.zeros(self._shape, dtype=np.uint8)

    def get(self, pid):
        if pid == camera_mod.cv2.CAP_PROP_FRAME_WIDTH:
            return 1920.0
        if pid == camera_mod.cv2.CAP_PROP_FRAME_HEIGHT:
            return 1080.0
        return 0.0

    def set(self, pid, val):
        return True


class DeadCap(LyingCap):
    def read(self):
        return False, None


class FakeWriter:
    instances: list["FakeWriter"] = []

    def __init__(self, path, fourcc, fps, size):
        self.path = path
        self.size = size
        self.frames: list[tuple] = []
        FakeWriter.instances.append(self)

    def write(self, frame):
        self.frames.append(frame.shape[:2])

    def release(self):
        with open(self.path, "wb") as f:
            f.write(b"clip")


@pytest.fixture
def clip_service(tmp_path, monkeypatch):
    FakeWriter.instances = []
    monkeypatch.setattr(camera_mod.cv2, "VideoWriter", FakeWriter)

    def _make(cap):
        cs = CameraService(camera_id=0)
        cs.setup(frames_dir=str(tmp_path / "frames"), output_dir=str(tmp_path / "out"))
        cs._target_brightness = 0.0
        cs._warmup_seconds = 0.0
        cs.start_session("s")
        monkeypatch.setattr(
            CameraService,
            "_open_capture",
            lambda self, timeout=None: _yield(cap),
        )
        return cs

    return _make


class _yield:
    """Minimaler Kontextmanager-Ersatz für _open_capture."""

    def __init__(self, cap):
        self._cap = cap

    def __enter__(self):
        return self._cap

    def __exit__(self, *exc):
        return False


def test_writer_uses_real_frame_size(clip_service):
    cs = clip_service(LyingCap())
    cs.capture_clip(duration=0.05, clip_fps=10)
    assert FakeWriter.instances, "VideoWriter wurde nicht angelegt"
    assert FakeWriter.instances[0].size == (640, 480)


def test_first_frame_is_written_not_discarded(clip_service):
    cs = clip_service(LyingCap())
    cs.capture_clip(duration=0.05, clip_fps=10)
    assert FakeWriter.instances[0].frames, "Erstes Frame darf nicht verworfen werden"
    assert all(shape == (480, 640) for shape in FakeWriter.instances[0].frames)


def test_returns_none_when_first_read_fails(clip_service):
    cs = clip_service(DeadCap())
    assert cs.capture_clip(duration=0.05, clip_fps=10) is None
    assert not FakeWriter.instances, "Ohne Frame darf kein Writer entstehen"
