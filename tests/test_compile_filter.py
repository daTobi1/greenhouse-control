from services.camera import CameraService


def test_filter_without_deflicker():
    assert CameraService.build_video_filter(25, deflicker=False) == "fps=25"


def test_filter_with_deflicker():
    assert (
        CameraService.build_video_filter(25, deflicker=True)
        == "fps=25,deflicker=size=7:mode=am"
    )


def test_deflicker_runs_after_fps():
    """Erst auf die Zielbildrate bringen, dann angleichen."""
    parts = CameraService.build_video_filter(30, deflicker=True).split(",")
    assert parts[0].startswith("fps=")
    assert parts[1].startswith("deflicker=")
