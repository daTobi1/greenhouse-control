"""Zugriff auf die V4L2-Gerätedaten über sysfs und v4l2-ctl.

Auf Nicht-Linux-Systemen liefern alle Funktionen leere Ergebnisse; die Aufrufer
fallen dann auf OpenCV-Probing zurück.
"""

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

SYSFS_ROOT = Path("/sys/class/video4linux")

_NODE_RE = re.compile(r"^video(\d+)$")


def available() -> bool:
    """True, wenn v4l2-ctl im PATH liegt."""
    return shutil.which("v4l2-ctl") is not None


def _node_index(node_name: str) -> int | None:
    m = _NODE_RE.match(node_name)
    return int(m.group(1)) if m else None


def list_devices(sysfs_root: Path = SYSFS_ROOT) -> list[dict]:
    """Alle Capture-Nodes als [{index, device, name}], nach Index sortiert.

    Nodes mit sysfs-`index` != 0 sind Metadata-/Nebennodes desselben Geräts und
    werden übersprungen, damit jede Kamera genau einmal erscheint.
    """
    if not sysfs_root.exists():
        return []

    devices: list[dict] = []
    for node in sysfs_root.iterdir():
        idx = _node_index(node.name)
        if idx is None:
            continue
        try:
            if (node / "index").read_text().strip() != "0":
                continue
            name = (node / "name").read_text().strip()
        except OSError:
            continue
        devices.append({"index": idx, "device": f"/dev/{node.name}", "name": name})

    return sorted(devices, key=lambda d: d["index"])
