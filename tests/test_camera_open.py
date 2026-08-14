from services.camera import CameraService, _PROP_FOURCC_ORDER_MARKER  # noqa: F401


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
