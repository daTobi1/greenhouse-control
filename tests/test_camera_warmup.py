import numpy as np

from services.camera import CameraService


class CountingCap:
    """Zählt read()-Aufrufe und liefert immer ein gültiges Frame."""

    def __init__(self, fail_after: int | None = None):
        self.reads = 0
        self.sets: list[tuple] = []
        self._fail_after = fail_after

    def read(self):
        self.reads += 1
        if self._fail_after is not None and self.reads > self._fail_after:
            return False, None
        return True, np.zeros((8, 8, 3), dtype=np.uint8)

    def get(self, pid):
        return 0.0

    def set(self, pid, val):
        self.sets.append((pid, val))
        return True


def test_warmup_runs_without_configured_props():
    """Ohne gesetzte Properties wurde bisher gar kein Frame verworfen."""
    cs = CameraService(camera_id=0)
    cap = CountingCap()
    cs._apply_props(cap, warmup_seconds=0.05)
    assert cap.reads > 0


def test_warmup_respects_max_frames():
    cs = CameraService(camera_id=0)
    cap = CountingCap()
    discarded = cs._warmup(cap, seconds=60.0, max_frames=7)
    assert discarded == 7
    assert cap.reads == 7


def test_warmup_stops_on_read_failure():
    cs = CameraService(camera_id=0)
    cap = CountingCap(fail_after=3)
    discarded = cs._warmup(cap, seconds=60.0, max_frames=50)
    assert discarded == 3


def test_warmup_applies_props_first():
    cs = CameraService(camera_id=0)
    cs.set_properties({"contrast": 200})
    cap = CountingCap()
    cs._apply_props(cap, warmup_seconds=0.01)
    assert cap.sets, "Properties müssen vor dem Warm-up gesetzt werden"
