from services import v4l2

SAMPLE = """ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture

\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 1920x1080
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\t\tInterval: Discrete 0.067s (15.000 fps)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 640x480
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.100s (10.000 fps)
"""


def test_parses_all_entries():
    formats = v4l2.parse_formats(SAMPLE)
    assert len(formats) == 4


def test_parses_fourcc_and_size():
    formats = v4l2.parse_formats(SAMPLE)
    assert formats[0]["fourcc"] == "MJPG"
    assert formats[0]["width"] == 1920
    assert formats[0]["height"] == 1080


def test_parses_multiple_intervals():
    formats = v4l2.parse_formats(SAMPLE)
    assert formats[0]["fps"] == [30.0, 15.0]


def test_second_format_block_gets_own_fourcc():
    formats = v4l2.parse_formats(SAMPLE)
    yuyv = [f for f in formats if f["fourcc"] == "YUYV"]
    assert len(yuyv) == 2
    assert yuyv[1]["fps"] == [10.0]


def test_size_without_intervals_yields_empty_fps():
    text = "\t[0]: 'MJPG' (Motion-JPEG)\n\t\tSize: Discrete 800x600\n"
    assert v4l2.parse_formats(text) == [
        {"fourcc": "MJPG", "width": 800, "height": 600, "fps": []}
    ]


def test_stepwise_sizes_are_ignored():
    """Stepwise-Formate melden keine diskreten Auflösungen und werden übergangen."""
    text = (
        "\t[0]: 'MJPG' (Motion-JPEG)\n"
        "\t\tSize: Stepwise 32x32 - 1920x1080 with step 2/2\n"
    )
    assert v4l2.parse_formats(text) == []


def test_empty_input():
    assert v4l2.parse_formats("") == []
