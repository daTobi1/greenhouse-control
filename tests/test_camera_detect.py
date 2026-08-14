import pytest

from services import camera as camera_mod
from services.camera import CameraService

FORMATS = [
    {"fourcc": "MJPG", "width": 1920, "height": 1080, "fps": [30.0, 15.0]},
    {"fourcc": "MJPG", "width": 1280, "height": 720, "fps": [30.0]},
    {"fourcc": "YUYV", "width": 1280, "height": 720, "fps": [10.0]},
    {"fourcc": "YUYV", "width": 640, "height": 480, "fps": [30.0]},
]


@pytest.fixture(autouse=True)
def _clear_cache():
    camera_mod.clear_detect_cache()
    yield
    camera_mod.clear_detect_cache()


@pytest.fixture
def with_v4l2(monkeypatch):
    monkeypatch.setattr(camera_mod.v4l2, "available", lambda: True)
    monkeypatch.setattr(
        camera_mod.v4l2,
        "list_devices",
        lambda **kw: [
            {"index": 0, "device": "/dev/video0", "name": "HD Pro Webcam C920"},
            {"index": 2, "device": "/dev/video2", "name": "USB Camera"},
        ],
    )
    monkeypatch.setattr(camera_mod.v4l2, "list_formats", lambda device: list(FORMATS))


def test_cameras_use_real_names(with_v4l2):
    cs = CameraService(camera_id=0)
    assert cs.detect_cameras() == [
        {"index": 0, "name": "HD Pro Webcam C920"},
        {"index": 2, "name": "USB Camera"},
    ]


def test_resolutions_deduplicated_and_sorted(with_v4l2):
    cs = CameraService(camera_id=0)
    res = cs.detect_resolutions(0)
    assert [(r["width"], r["height"]) for r in res] == [
        (1920, 1080),
        (1280, 720),
        (640, 480),
    ]


def test_resolution_labels_use_known_names(with_v4l2):
    cs = CameraService(camera_id=0)
    labels = {(r["width"], r["height"]): r["label"] for r in cs.detect_resolutions(0)}
    assert "Full HD" in labels[(1920, 1080)]
    assert "720p" in labels[(1280, 720)]


def test_fps_filtered_by_resolution(with_v4l2):
    cs = CameraService(camera_id=0)
    assert cs.detect_fps(0, 1920, 1080) == [15, 30]
    assert cs.detect_fps(0, 1280, 720) == [10, 30]


def test_fps_without_resolution_returns_union(with_v4l2):
    cs = CameraService(camera_id=0)
    assert cs.detect_fps(0) == [10, 15, 30]


def test_formats_deduplicated(with_v4l2):
    cs = CameraService(camera_id=0)
    assert cs.detect_formats(0) == ["MJPG", "YUYV"]


def test_results_are_cached(with_v4l2, monkeypatch):
    calls = []
    monkeypatch.setattr(
        camera_mod.v4l2,
        "list_formats",
        lambda device: calls.append(device) or list(FORMATS),
    )
    cs = CameraService(camera_id=0)
    cs.detect_resolutions(0)
    cs.detect_resolutions(0)
    assert len(calls) == 1


def test_refresh_bypasses_cache(with_v4l2, monkeypatch):
    calls = []
    monkeypatch.setattr(
        camera_mod.v4l2,
        "list_formats",
        lambda device: calls.append(device) or list(FORMATS),
    )
    cs = CameraService(camera_id=0)
    cs.detect_resolutions(0)
    cs.detect_resolutions(0, refresh=True)
    assert len(calls) == 2


def test_falls_back_when_v4l2_unavailable(monkeypatch):
    monkeypatch.setattr(camera_mod.v4l2, "available", lambda: False)
    monkeypatch.setattr(camera_mod, "CV2_AVAILABLE", False)
    cs = CameraService(camera_id=0)
    assert cs.detect_resolutions(0) == []
    assert cs.detect_formats(0) == []
