"""Zugriff auf die V4L2-Gerätedaten über sysfs und v4l2-ctl.

Auf Nicht-Linux-Systemen liefern alle Funktionen leere Ergebnisse; die Aufrufer
fallen dann auf OpenCV-Probing zurück.
"""

import logging
import re
import shutil
import subprocess
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


_FMT_RE      = re.compile(r"^\s*\[\d+\]:\s*'(\w{4})'")
_SIZE_RE     = re.compile(r"^\s*Size:\s*Discrete\s+(\d+)x(\d+)")
_INTERVAL_RE = re.compile(r"^\s*Interval:\s*Discrete\s+[\d.]+s\s+\(([\d.]+)\s*fps\)")

_LIST_FORMATS_TIMEOUT = 5.0


def parse_formats(text: str) -> list[dict]:
    """Ausgabe von `v4l2-ctl --list-formats-ext` in Einträge zerlegen.

    Ein Eintrag je Format/Auflösungs-Kombination:
    {"fourcc": "MJPG", "width": 1920, "height": 1080, "fps": [30.0, 15.0]}

    Stepwise-Größen werden übergangen – sie melden keine diskreten Auflösungen
    und sind bei USB-Webcams sehr selten.
    """
    entries: list[dict] = []
    fourcc: str | None = None
    current: dict | None = None

    for line in text.splitlines():
        m = _FMT_RE.match(line)
        if m:
            fourcc = m.group(1)
            current = None
            continue

        m = _SIZE_RE.match(line)
        if m:
            if fourcc is None:
                continue
            current = {
                "fourcc": fourcc,
                "width": int(m.group(1)),
                "height": int(m.group(2)),
                "fps": [],
            }
            entries.append(current)
            continue

        m = _INTERVAL_RE.match(line)
        if m and current is not None:
            current["fps"].append(float(m.group(1)))

    return entries


def list_formats(device: str) -> list[dict]:
    """Formatmatrix eines Geräts, z. B. list_formats("/dev/video0").

    Startet keinen Stream und kollidiert daher nicht mit einer laufenden
    Aufnahme. Bei jedem Fehler wird eine leere Liste zurückgegeben, damit der
    Aufrufer auf OpenCV-Probing zurückfallen kann.
    """
    if not available():
        return []
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-formats-ext", "-d", device],
            capture_output=True,
            text=True,
            timeout=_LIST_FORMATS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(f"v4l2-ctl failed for {device}: {exc}")
        return []

    if result.returncode != 0:
        logger.warning(f"v4l2-ctl returned {result.returncode} for {device}")
        return []

    return parse_formats(result.stdout)
