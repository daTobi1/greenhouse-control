from pathlib import Path

from services import v4l2


def _make_node(root: Path, node: str, name: str, index: str) -> None:
    d = root / node
    d.mkdir(parents=True)
    (d / "name").write_text(name + "\n")
    (d / "index").write_text(index + "\n")


def test_lists_only_capture_nodes(tmp_path):
    """video1 ist der Metadata-Node derselben Kamera und darf nicht erscheinen."""
    _make_node(tmp_path, "video0", "HD Pro Webcam C920", "0")
    _make_node(tmp_path, "video1", "HD Pro Webcam C920", "1")
    _make_node(tmp_path, "video2", "USB Camera", "0")

    devices = v4l2.list_devices(sysfs_root=tmp_path)

    assert devices == [
        {"index": 0, "device": "/dev/video0", "name": "HD Pro Webcam C920"},
        {"index": 2, "device": "/dev/video2", "name": "USB Camera"},
    ]


def test_missing_sysfs_returns_empty(tmp_path):
    assert v4l2.list_devices(sysfs_root=tmp_path / "gibtsnicht") == []


def test_node_without_index_file_is_skipped(tmp_path):
    d = tmp_path / "video0"
    d.mkdir()
    (d / "name").write_text("Kaputt\n")
    assert v4l2.list_devices(sysfs_root=tmp_path) == []


def test_sorted_by_device_index(tmp_path):
    _make_node(tmp_path, "video10", "Zehn", "0")
    _make_node(tmp_path, "video2", "Zwei", "0")
    assert [d["index"] for d in v4l2.list_devices(sysfs_root=tmp_path)] == [2, 10]
