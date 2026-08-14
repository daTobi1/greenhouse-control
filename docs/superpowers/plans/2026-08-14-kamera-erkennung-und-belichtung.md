# Kamera: Erkennung und Belichtung – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auflösungserkennung zuverlässig machen und Timelapse-Bilder gleichmäßig belichten.

**Architecture:** Die Formatliste kommt künftig aus `v4l2-ctl --list-formats-ext` statt aus Rateversuchen mit OpenCV; OpenCV-Probing bleibt als Fallback für Windows. Ein Lock pro Geräteindex verhindert, dass Erkennung und Aufnahme sich gegenseitig die exklusiv vergebene V4L2-Kamera wegnehmen. Die Belichtung wird über eine geschlossene Regelschleife auf eine Ziel-Helligkeit gezogen; Rest-Schwankungen glättet ein Deflicker-Filter beim Compilen. Die gesamte Mess- und Parselogik liegt in reinen Funktionen ohne Hardware-Abhängigkeit und ist damit vollständig testbar.

**Tech Stack:** Python 3.14, OpenCV (cv2 4.13), numpy, v4l-utils (`v4l2-ctl`), ffmpeg, FastAPI, Vanilla JS

**Spec:** `docs/superpowers/specs/2026-08-14-timelapse-zeitplan-belichtung-regelung-design.md` (Blöcke B und C, Abschnitte 4 und 5)

## Global Constraints

- Zielplattform ist Raspberry Pi OS (Debian), Entwicklung unter Windows. Code muss auf beiden laufen: alles unter `/sys` und `/dev` sowie `v4l2-ctl` sind nur unter Linux vorhanden und brauchen einen Fallback.
- `v4l-utils` wird von `install.sh:137` bereits mitinstalliert. Keine neue Systemabhängigkeit.
- Neue Laufzeit-Abhängigkeiten sind nicht erlaubt. `requirements.txt` bleibt unverändert.
- Alle Settings-Schlüssel liegen als JSON in der bestehenden `settings`-Tabelle. Keine Schema-Migration.
- Reine Logik gehört in eigene Module ohne `cv2`-Import auf Modulebene, damit sie ohne Hardware testbar bleibt.
- UI-Texte sind Deutsch, ohne Emojis.
- Testkommandos im Plan stehen als `python -m pytest`. Konkret ist das unter Windows `venv/Scripts/python.exe -m pytest` und auf dem Pi `venv/bin/python -m pytest`.
- Jede Task endet mit einem Commit. Commit-Nachrichten Englisch, Conventional Commits.

---

### Task 1: Test-Setup

**Files:**
- Create: `requirements-dev.txt`
- Create: `pytest.ini`
- Create: `conftest.py`
- Create: `tests/test_setup.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nichts
- Produces: lauffähiges `python -m pytest` mit Projektwurzel im `sys.path`, sodass `import services.x` in Tests funktioniert

- [ ] **Step 1: Dev-Abhängigkeiten anlegen**

`requirements-dev.txt` — bewusst getrennt von `requirements.txt`, damit `install.sh` pytest nicht auf den Pi installiert:

```
# Nur für die Entwicklung – wird von install.sh NICHT installiert.
pytest>=8.0.0
numpy>=1.26.0
```

- [ ] **Step 2: pytest konfigurieren**

`pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v --tb=short
```

`conftest.py` im Projektstamm (leer, aber notwendig). pytest legt das Verzeichnis der obersten `conftest.py` auf den `sys.path`; ohne diese Datei scheitert `import services.v4l2` in den Tests:

```python
"""Leer – Existenz genügt, damit pytest die Projektwurzel in sys.path legt."""
```

- [ ] **Step 3: Smoke-Test schreiben**

`tests/test_setup.py`:

```python
def test_project_root_importable():
    """Stellt sicher, dass conftest.py die Projektwurzel in den sys.path legt."""
    import services  # noqa: F401


def test_numpy_available():
    import numpy as np
    assert np.zeros((2, 2)).sum() == 0
```

- [ ] **Step 4: Installieren und ausführen**

Run: `python -m pip install -r requirements-dev.txt`
Run: `python -m pytest`
Expected: 2 passed

- [ ] **Step 5: .gitignore ergänzen**

An `.gitignore` anhängen:

```
.pytest_cache/
```

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt pytest.ini conftest.py tests/test_setup.py .gitignore
git commit -m "test: add pytest setup with project root on sys.path"
```

---

### Task 2: v4l2 – Geräteliste

**Files:**
- Create: `services/v4l2.py`
- Create: `tests/test_v4l2_devices.py`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `v4l2.available() -> bool`
  - `v4l2.list_devices(sysfs_root: Path = Path("/sys/class/video4linux")) -> list[dict]` mit Einträgen `{"index": int, "device": str, "name": str}`

Hintergrund: Ein USB-Gerät legt auf dem Pi mehrere `/dev/video*`-Nodes an (Capture plus Metadata, bei H.264-Kameras bis zu vier). Der sysfs-Eintrag `index` ist die Nummer des Nodes **innerhalb** des Geräts; nur `index == 0` ist der Capture-Node. Danach wird gefiltert, damit jede Kamera genau einmal erscheint.

- [ ] **Step 1: Failing test schreiben**

`tests/test_v4l2_devices.py`:

```python
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
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_v4l2_devices.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'services.v4l2'`

- [ ] **Step 3: Implementieren**

`services/v4l2.py`:

```python
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
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_v4l2_devices.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/v4l2.py tests/test_v4l2_devices.py
git commit -m "feat(v4l2): list capture devices from sysfs"
```

---

### Task 3: v4l2 – Formatmatrix parsen

**Files:**
- Modify: `services/v4l2.py`
- Create: `tests/test_v4l2_formats.py`

**Interfaces:**
- Consumes: `v4l2.available()` aus Task 2
- Produces:
  - `v4l2.parse_formats(text: str) -> list[dict]` — rein, Einträge `{"fourcc": str, "width": int, "height": int, "fps": list[float]}`
  - `v4l2.list_formats(device: str) -> list[dict]` — ruft `v4l2-ctl --list-formats-ext -d <device>` auf und gibt `parse_formats` zurück

Der Parser wird von der Subprozess-Ausführung getrennt, damit er ohne Hardware gegen eine Textfixture testbar ist.

- [ ] **Step 1: Failing test schreiben**

`tests/test_v4l2_formats.py`:

```python
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
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_v4l2_formats.py -v`
Expected: FAIL mit `AttributeError: module 'services.v4l2' has no attribute 'parse_formats'`

- [ ] **Step 3: Implementieren**

An `services/v4l2.py` anhängen (und `import subprocess` oben ergänzen):

```python
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
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_v4l2_formats.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add services/v4l2.py tests/test_v4l2_formats.py
git commit -m "feat(v4l2): parse format matrix from v4l2-ctl output"
```

---

### Task 4: Gerätelock

**Files:**
- Create: `services/device_lock.py`
- Create: `tests/test_device_lock.py`

**Interfaces:**
- Consumes: nichts
- Produces: `device_lock.get(device_index: int) -> threading.Lock`

**Abweichung von der Spec:** Spec §5.4 sieht den Lock in `state.py` vor. Das erzeugt einen Zirkelimport, weil `state.py` bereits `from services.camera import CameraService` macht und `camera.py` den Lock brauchen würde. Der Lock kommt deshalb in ein eigenes, abhängigkeitsfreies Modul. `get_timelapse_wake` (Plan 2) bleibt in `state.py` — dort gibt es das Problem nicht.

- [ ] **Step 1: Failing test schreiben**

`tests/test_device_lock.py`:

```python
import threading

from services import device_lock


def test_same_index_returns_same_lock():
    assert device_lock.get(0) is device_lock.get(0)


def test_different_index_returns_different_lock():
    assert device_lock.get(0) is not device_lock.get(1)


def test_concurrent_creation_yields_one_lock():
    """Zwei Threads dürfen für denselben Index nicht zwei Locks erzeugen."""
    results = []
    barrier = threading.Barrier(2)

    def grab():
        barrier.wait()
        results.append(device_lock.get(99))

    threads = [threading.Thread(target=grab) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results[0] is results[1]


def test_lock_is_usable():
    lock = device_lock.get(42)
    assert lock.acquire(timeout=1)
    try:
        assert not lock.acquire(blocking=False)
    finally:
        lock.release()
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_device_lock.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'services.device_lock'`

- [ ] **Step 3: Implementieren**

`services/device_lock.py`:

```python
"""Ein Lock je V4L2-Geräteindex.

V4L2 vergibt eine Kamera exklusiv. Erkennung, Vorschau und Timelapse-Aufnahme
greifen auf dieselben Geräte zu und würden sich sonst gegenseitig verdrängen:
die Aufnahme scheitert stumm, oder die Erkennung liefert eine leere Liste.

Der Lock hängt am Geräteindex, nicht am Kamera-Slot – zwei Slots können auf
dasselbe Gerät zeigen.

Eigenes Modul statt state.py, weil state.py services.camera importiert und
sonst ein Zirkelimport entstünde.
"""

import threading

_locks: dict[int, threading.Lock] = {}
_guard = threading.Lock()


def get(device_index: int) -> threading.Lock:
    """Lock für den Geräteindex; wird beim ersten Zugriff angelegt."""
    with _guard:
        lock = _locks.get(device_index)
        if lock is None:
            lock = threading.Lock()
            _locks[device_index] = lock
        return lock
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_device_lock.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/device_lock.py tests/test_device_lock.py
git commit -m "feat: add per-device V4L2 lock registry"
```

---

### Task 5: Helligkeitsmessung und Korrekturfaktoren

**Files:**
- Create: `services/exposure.py`
- Create: `tests/test_exposure.py`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `exposure.measure_brightness(frame) -> float` — mittlere Luma 0…255, Bildmitte doppelt gewichtet
  - `exposure.within_tolerance(measured: float, target: float, tolerance: float) -> bool`
  - `exposure.correction_factor(measured: float, target: float, damping: float = 0.8) -> float` — Multiplikator für die Belichtungszeit, begrenzt auf `[0.25, 4.0]`
  - `exposure.software_gain(measured: float, target: float) -> float | None` — Faktor für `cv2.convertScaleAbs`, begrenzt auf `[0.7, 1.3]`, `None` wenn keine Korrektur nötig

Kein `cv2`-Import: das Modul arbeitet nur auf numpy-Arrays und liefert Zahlen. Die `convertScaleAbs`-Anwendung bleibt in `camera.py`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_exposure.py`:

```python
import numpy as np
import pytest

from services import exposure


def _solid(value, shape=(48, 64, 3)):
    return np.full(shape, value, dtype=np.uint8)


# --- measure_brightness ---

@pytest.mark.parametrize("value", [0, 128, 255])
def test_measure_solid_gray(value):
    assert measured_close(exposure.measure_brightness(_solid(value)), value)


def measured_close(a, b, tol=1.0):
    return abs(a - b) <= tol


def test_measure_grayscale_frame():
    frame = np.full((48, 64), 100, dtype=np.uint8)
    assert measured_close(exposure.measure_brightness(frame), 100)


def test_center_weighted_higher_than_edge_weighted():
    """Gleich viele helle Pixel, einmal in der Mitte, einmal am Rand."""
    center = _solid(0)
    center[12:36, 16:48] = 255

    edge = _solid(0)
    edge[0:12, :] = 255
    edge[36:48, :] = 255

    assert exposure.measure_brightness(center) > exposure.measure_brightness(edge)


# --- within_tolerance ---

def test_within_tolerance_boundaries():
    assert exposure.within_tolerance(120, 120, 12)
    assert exposure.within_tolerance(132, 120, 12)
    assert exposure.within_tolerance(108, 120, 12)
    assert not exposure.within_tolerance(133, 120, 12)
    assert not exposure.within_tolerance(107, 120, 12)


# --- correction_factor ---

def test_correction_on_target_is_neutral():
    assert exposure.correction_factor(120, 120) == pytest.approx(1.0)


def test_correction_too_dark_increases():
    assert exposure.correction_factor(60, 120) > 1.0


def test_correction_too_bright_decreases():
    assert exposure.correction_factor(240, 120) < 1.0


def test_correction_is_damped():
    """Ungedämpft wäre der Faktor 2.0; mit damping=0.8 nur 1.8."""
    assert exposure.correction_factor(60, 120) == pytest.approx(1.8)


def test_correction_clamped_upper():
    assert exposure.correction_factor(5, 250) == 4.0


def test_correction_clamped_lower():
    assert exposure.correction_factor(255, 10) == 0.25


def test_correction_black_frame_does_not_raise():
    assert exposure.correction_factor(0, 120) == 4.0


# --- software_gain ---

def test_software_gain_clamped():
    assert exposure.software_gain(50, 120) == pytest.approx(1.3)
    assert exposure.software_gain(250, 60) == pytest.approx(0.7)


def test_software_gain_none_when_close_enough():
    assert exposure.software_gain(120, 121) is None


def test_software_gain_none_on_black_frame():
    assert exposure.software_gain(0, 120) is None
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_exposure.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'services.exposure'`

- [ ] **Step 3: Implementieren**

`services/exposure.py`:

```python
"""Helligkeitsmessung und Belichtungskorrektur für Timelapse-Aufnahmen.

Reine Rechenlogik ohne OpenCV-Import: arbeitet auf numpy-Arrays und liefert
Zahlen. Die Anwendung der Faktoren auf die Kamera bzw. auf ein Bild bleibt in
services/camera.py.
"""

import numpy as np

# Grenzen für den Belichtungsmultiplikator. Weiter als Faktor 4 in einem
# Schritt zu springen führt bei fast schwarzen Frames zu Überschwingen.
_FACTOR_MIN, _FACTOR_MAX = 0.25, 4.0

# Software-Korrektur ist stark begrenzt: stärkeres Aufhellen hebt sichtbar
# Rauschen an. Kameras ohne steuerbare Belichtung bleiben damit eingeschränkt.
_GAIN_MIN, _GAIN_MAX = 0.7, 1.3

# Unterhalb dieser Helligkeit ist das Bild praktisch schwarz; ein
# Verhältnis target/measured wäre dort sinnlos groß.
_BLACK_LEVEL = 1.0

# Korrekturen unter 2 % lohnen die Rechenzeit nicht.
_GAIN_EPSILON = 0.02


def measure_brightness(frame) -> float:
    """Mittlere Luma 0…255, Bildmitte doppelt gewichtet.

    Die Gewichtung verhindert, dass ein heller Himmel oder eine Lampe am
    Bildrand die Messung dominiert und das eigentliche Motiv absaufen lässt.
    Erwartet BGR (OpenCV-Reihenfolge) oder ein einkanaliges Graubild.
    """
    arr = np.asarray(frame)
    if arr.ndim == 3:
        gray = (
            0.114 * arr[:, :, 0]
            + 0.587 * arr[:, :, 1]
            + 0.299 * arr[:, :, 2]
        )
    else:
        gray = arr.astype(np.float64)

    h, w = gray.shape[:2]
    center = gray[h // 4 : h - h // 4, w // 4 : w - w // 4]
    if center.size == 0:
        return float(gray.mean())

    return float((gray.mean() + 2.0 * center.mean()) / 3.0)


def within_tolerance(measured: float, target: float, tolerance: float) -> bool:
    return abs(measured - target) <= tolerance


def correction_factor(measured: float, target: float, damping: float = 0.8) -> float:
    """Multiplikator für die Belichtungszeit.

    Gedämpft, weil die Kennlinie Belichtungszeit → Helligkeit nicht linear ist
    und ein voller Sprung regelmäßig überschießt.
    """
    if measured <= _BLACK_LEVEL:
        return _FACTOR_MAX
    factor = 1.0 + damping * (target / measured - 1.0)
    return max(_FACTOR_MIN, min(_FACTOR_MAX, factor))


def software_gain(measured: float, target: float) -> float | None:
    """Faktor für cv2.convertScaleAbs, oder None wenn nichts zu tun ist.

    Fallback für Kameras, deren Belichtung sich nicht steuern lässt.
    """
    if measured <= _BLACK_LEVEL:
        return None
    factor = max(_GAIN_MIN, min(_GAIN_MAX, target / measured))
    return None if abs(factor - 1.0) < _GAIN_EPSILON else factor
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_exposure.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add services/exposure.py tests/test_exposure.py
git commit -m "feat: add brightness measurement and exposure correction math"
```

---

### Task 6: Neue Kamera-Properties

**Files:**
- Modify: `services/camera.py:37-52`

**Interfaces:**
- Consumes: nichts
- Produces: Konstanten `_PROP_BRIGHTNESS = 10`, `_PROP_GAIN = 14`, `_PROP_EXPOSURE = 15`, `_PROP_AUTO_EXPOSURE = 21`, `_PROP_GAMMA = 22`; erweiterte `CAMERA_PROPERTIES`-Liste

`detect_properties()` probt Bereiche bereits generisch über `CAMERA_PROPERTIES`; die neuen Einträge werden dadurch automatisch mitgeprobt. `main.py:52-59` lädt gespeicherte Werte ebenfalls generisch. Es ist also nur die Datenstruktur zu erweitern.

- [ ] **Step 1: Konstanten ergänzen**

In `services/camera.py` bei den vorhandenen `_PROP_*`-Konstanten:

```python
# OpenCV property IDs (numeric for compatibility across builds)
_PROP_BRIGHTNESS     = 10
_PROP_CONTRAST       = 11
_PROP_GAIN           = 14
_PROP_EXPOSURE       = 15
_PROP_AUTO_EXPOSURE  = 21
_PROP_GAMMA          = 22
_PROP_ZOOM           = 27
_PROP_FOCUS          = 28
_PROP_AUTOFOCUS      = 39
_PROP_AUTO_WB        = 44
_PROP_WB_TEMPERATURE = 45

# V4L2: 1 = manuell, 3 = Blendenpriorität (automatisch).
_V4L2_EXPOSURE_MANUAL = 1.0
```

- [ ] **Step 2: CAMERA_PROPERTIES erweitern**

Die neuen Einträge vor die bestehenden setzen, damit Belichtung im Dashboard oben steht. Die `min`/`max`-Werte sind nur Vorgaben für den Fall, dass das Proben scheitert; `detect_properties()` überschreibt sie mit den echten Treiberbereichen:

```python
CAMERA_PROPERTIES = [
    {"key": "auto_exposure", "prop": _PROP_AUTO_EXPOSURE,  "label": "Auto-Belichtung",    "type": "bool"},
    {"key": "exposure",      "prop": _PROP_EXPOSURE,       "label": "Belichtung",         "type": "range", "min": 1,    "max": 5000,  "step": 1,   "auto_key": "auto_exposure"},
    {"key": "gain",          "prop": _PROP_GAIN,           "label": "Verstärkung",        "type": "range", "min": 0,    "max": 255,   "step": 1},
    {"key": "brightness",    "prop": _PROP_BRIGHTNESS,     "label": "Helligkeit",         "type": "range", "min": 0,    "max": 255,   "step": 1},
    {"key": "gamma",         "prop": _PROP_GAMMA,          "label": "Gamma",              "type": "range", "min": 1,    "max": 500,   "step": 1},
    {"key": "auto_wb",       "prop": _PROP_AUTO_WB,        "label": "Auto-Weissabgleich", "type": "bool"},
    {"key": "white_balance", "prop": _PROP_WB_TEMPERATURE, "label": "Weissabgleich",      "type": "range", "min": 2000, "max": 10000, "step": 100, "unit": "K", "auto_key": "auto_wb"},
    {"key": "contrast",      "prop": _PROP_CONTRAST,       "label": "Kontrast",           "type": "range", "min": 0,    "max": 255,   "step": 1},
    {"key": "autofocus",     "prop": _PROP_AUTOFOCUS,      "label": "Auto-Fokus",         "type": "bool"},
    {"key": "focus",         "prop": _PROP_FOCUS,          "label": "Fokus",              "type": "range", "min": 0,    "max": 255,   "step": 1,   "auto_key": "autofocus"},
    {"key": "zoom",          "prop": _PROP_ZOOM,           "label": "Zoom",               "type": "range", "min": 100,  "max": 800,   "step": 10},
]
```

- [ ] **Step 3: `_apply_props` um die neuen Auto-Toggles erweitern**

In `_apply_props` die beiden Stellen ergänzen, an denen Auto-Toggles behandelt werden (`camera.py:106-118`):

```python
        # Auto toggles first
        for pid in (_PROP_AUTO_WB, _PROP_AUTOFOCUS, _PROP_AUTO_EXPOSURE):
            if pid in self._cam_props:
                cap.set(pid, self._cam_props[pid])
        # Manual values (skip if corresponding auto is on)
        for pid, val in self._cam_props.items():
            if pid in (_PROP_AUTO_WB, _PROP_AUTOFOCUS, _PROP_AUTO_EXPOSURE):
                continue
            if pid == _PROP_WB_TEMPERATURE and self._cam_props.get(_PROP_AUTO_WB, 0) > 0.5:
                continue
            if pid == _PROP_FOCUS and self._cam_props.get(_PROP_AUTOFOCUS, 0) > 0.5:
                continue
            if pid == _PROP_EXPOSURE and self._cam_props.get(_PROP_AUTO_EXPOSURE, 0) > 1.5:
                continue
            cap.set(pid, val)
```

Die Schwelle für `_PROP_AUTO_EXPOSURE` ist 1.5 statt 0.5, weil V4L2 hier 1 (manuell) und 3 (automatisch) verwendet, nicht 0/1.

- [ ] **Step 4: Import-Check**

Run: `python -c "from services.camera import CAMERA_PROPERTIES; print(len(CAMERA_PROPERTIES))"`
Expected: `11`

- [ ] **Step 5: Commit**

```bash
git add services/camera.py
git commit -m "feat(camera): add exposure, gain, brightness and gamma properties"
```

---

### Task 7: FOURCC und Gerätelock in allen Capture-Pfaden

**Files:**
- Modify: `services/camera.py` — `__init__`, `setup`, `capture_frame`, `capture_clip`, `capture_preview`, `detect_properties`
- Create: `tests/test_camera_open.py`

**Interfaces:**
- Consumes: `device_lock.get()` aus Task 4
- Produces:
  - `CameraService._open_capture()` — Contextmanager, der den Gerätelock nimmt, `cv2.VideoCapture` öffnet, FOURCC → Breite → Höhe setzt und am Ende sauber freigibt
  - `CameraService._fourcc: str` (Default `"MJPG"`), gesetzt über `setup(fourcc=...)`

Ursache, die hier behoben wird: FOURCC wurde nie gesetzt. USB-Webcams liefern hohe Auflösungen fast ausschließlich als MJPG; OpenCV öffnet mit V4L2 standardmäßig YUYV, wo USB 2.0 die meisten Kameras auf 640×480 begrenzt. Zusätzlich muss FOURCC **vor** Breite und Höhe gesetzt werden, sonst snapped der Treiber auf ein Format, das im alten Pixelformat noch möglich war.

- [ ] **Step 1: Failing test schreiben**

`tests/test_camera_open.py`:

```python
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
```

Der Marker-Import in Zeile 1 ist absichtlich: er schlägt fehl, solange Step 3 nicht umgesetzt ist.

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_camera_open.py -v`
Expected: FAIL mit `ImportError: cannot import name '_PROP_FOURCC_ORDER_MARKER'`

- [ ] **Step 3: Implementieren**

Oben in `services/camera.py` ergänzen:

```python
from contextlib import contextmanager

from services import device_lock

DEFAULT_FOURCC = "MJPG"

# Zeitfenster, das Erkennung und Vorschau auf eine belegte Kamera warten.
# Die Aufnahme wartet unbegrenzt, sie hat Vorrang.
LOCK_TIMEOUT = 2.0

# Reiner Marker: dokumentiert, dass FOURCC vor Breite/Höhe gesetzt werden muss.
# Wird von tests/test_camera_open.py importiert.
_PROP_FOURCC_ORDER_MARKER = "fourcc-before-size"


class CameraBusy(RuntimeError):
    """Die Kamera ist gerade von einem anderen Zugriff belegt."""
```

In `__init__` ergänzen:

```python
        self._fourcc: str = DEFAULT_FOURCC
        self._warmup_seconds: float = 1.5
        self._target_brightness: float = 120.0
        self._brightness_tol: float = 12.0
```

`setup()` um den Parameter erweitern:

```python
    def setup(
        self,
        frames_dir: str = "timelapse/frames",
        output_dir: str = "timelapse/output",
        camera_index: int = 0,
        capture_width: int = 0,
        capture_height: int = 0,
        fourcc: str = DEFAULT_FOURCC,
    ):
        self._frames_dir = Path(frames_dir)
        self._output_dir = Path(output_dir)
        self._camera_index = camera_index
        self._capture_width = capture_width
        self._capture_height = capture_height
        self._fourcc = fourcc or DEFAULT_FOURCC
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
```

Den Contextmanager ergänzen:

```python
    @contextmanager
    def _open_capture(self, timeout: float | None = None):
        """Kamera unter Gerätelock öffnen und konfiguriert bereitstellen.

        timeout=None wartet unbegrenzt (Aufnahme hat Vorrang). Mit einem
        Timeout wird CameraBusy geworfen, wenn die Kamera belegt ist – der
        Aufrufer kann daraus eine verständliche Meldung machen, statt eine
        leere Liste zu liefern.

        Reihenfolge beim Setzen ist zwingend FOURCC, Breite, Höhe: setzt man
        zuerst die Breite, steht die Kamera kurzzeitig auf einem ungültigen
        Format und der Treiber snapped auf etwas anderes.
        """
        lock = device_lock.get(self._camera_index)
        acquired = lock.acquire() if timeout is None else lock.acquire(timeout=timeout)
        if not acquired:
            raise CameraBusy(f"Kamera {self._camera_index} ist belegt")

        cap = None
        try:
            cap = cv2.VideoCapture(self._camera_index)
            if not cap.isOpened():
                raise CameraBusy(f"Kamera {self._camera_index} lässt sich nicht öffnen")

            if self._fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._fourcc))
            if self._capture_width > 0 and self._capture_height > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._capture_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._capture_height)

            yield cap
        finally:
            if cap is not None:
                cap.release()
            lock.release()
```

- [ ] **Step 4: `capture_frame` auf den Contextmanager umstellen**

`capture_frame` ersetzen (die Belichtungsregelung folgt in Task 9):

```python
    def capture_frame(self) -> str | None:
        """Capture one frame into the current session directory."""
        if not CV2_AVAILABLE:
            return None
        if not self._session:
            logger.warning("capture_frame called without an active session")
            return None

        session_dir = self._frames_dir / self._session
        try:
            with self._open_capture() as cap:
                self._apply_props(cap)
                ret, frame = cap.read()
        except CameraBusy as exc:
            logger.error(f"capture_frame: {exc}")
            return None

        if not ret:
            logger.error("Failed to read frame from camera")
            return None

        filename = session_dir / f"cam{self._camera_id}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        cv2.imwrite(str(filename), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        self._frame_count += 1
        logger.debug(f"Frame captured → {filename.name}")
        return str(filename)
```

- [ ] **Step 5: `capture_clip` und `capture_preview` umstellen**

`capture_clip` ersetzen:

```python
    def capture_clip(self, duration: float = 5.0, clip_fps: int = 10) -> str | None:
        """Record a short video clip into the current session directory."""
        if not CV2_AVAILABLE:
            return None
        if not self._session:
            logger.warning("capture_clip called without an active session")
            return None

        session_dir = self._frames_dir / self._session
        clip_path = session_dir / f"cam{self._camera_id}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"

        try:
            with self._open_capture() as cap:
                self._apply_props(cap)

                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                out = cv2.VideoWriter(str(clip_path), fourcc, float(clip_fps), (w, h))

                try:
                    frame_interval = 1.0 / clip_fps
                    end_time = time.monotonic() + duration
                    next_frame = time.monotonic()

                    while time.monotonic() < end_time:
                        now = time.monotonic()
                        if now >= next_frame:
                            ret, frame = cap.read()
                            if ret:
                                out.write(frame)
                            next_frame += frame_interval
                        else:
                            time.sleep(min(0.005, next_frame - now))
                finally:
                    out.release()
        except CameraBusy as exc:
            logger.error(f"capture_clip: {exc}")
            clip_path.unlink(missing_ok=True)
            return None

        if clip_path.exists() and clip_path.stat().st_size > 0:
            self._frame_count += 1
            logger.debug(f"Clip {self._frame_count} captured → {clip_path.name}")
            return str(clip_path)

        clip_path.unlink(missing_ok=True)
        return None
```

Der `VideoWriter` wird in einem eigenen `try/finally` freigegeben, damit er auch bei einem Lesefehler mitten in der Aufnahme geschlossen wird. Die Kamera selbst gibt der Contextmanager frei; das bisherige separate `cap.release()` entfällt.

`capture_preview`:

```python
    def capture_preview(self) -> bytes | None:
        """Return a JPEG-encoded preview image for the dashboard."""
        if not CV2_AVAILABLE:
            return None
        try:
            with self._open_capture(timeout=LOCK_TIMEOUT) as cap:
                self._apply_props(cap)
                ret, frame = cap.read()
        except CameraBusy:
            return None
        if not ret:
            return None
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes()
```

`detect_properties` erhält denselben Lock. Da es einen fremden `camera_index` bekommt, wird der Lock dort direkt genommen:

```python
    def detect_properties(self, camera_index: int) -> list[dict]:
        """Probe camera for supported properties and their value ranges."""
        if not CV2_AVAILABLE:
            return []
        lock = device_lock.get(camera_index)
        if not lock.acquire(timeout=LOCK_TIMEOUT):
            raise CameraBusy(f"Kamera {camera_index} ist belegt")
        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return []
            # ... bestehender Rumpf unverändert ...
        finally:
            lock.release()
```

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest tests/test_camera_open.py -v`
Expected: 3 passed

Run: `python -m pytest`
Expected: alle bisherigen Tests weiterhin grün

- [ ] **Step 7: Commit**

```bash
git add services/camera.py tests/test_camera_open.py
git commit -m "fix(camera): set FOURCC before resolution and guard captures with a device lock"
```

---

### Task 8: Zeitbasiertes Warm-up

**Files:**
- Modify: `services/camera.py` — `_apply_props`
- Create: `tests/test_camera_warmup.py`

**Interfaces:**
- Consumes: `CameraService._warmup_seconds` aus Task 7
- Produces: `CameraService._warmup(cap, seconds, max_frames=60) -> int` — gibt die Anzahl verworfener Frames zurück

Zwei Ursachen werden hier behoben. Erstens steigt `_apply_props` bei leerem `_cam_props` sofort aus, wodurch **kein einziger** Frame verworfen wird — das erste Bild direkt nach dem Öffnen ist bei USB-Kameras nahezu immer falsch belichtet. Zweitens sind 5 Frames zu wenig: UVC-Kameras brauchen für die Einregelung der Automatik 1–2 Sekunden, 5 Frames bei 30 fps sind 0,17 s.

- [ ] **Step 1: Failing test schreiben**

`tests/test_camera_warmup.py`:

```python
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
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_camera_warmup.py -v`
Expected: FAIL — `test_warmup_runs_without_configured_props` scheitert mit `assert 0 > 0`, die `_warmup`-Tests mit `AttributeError`

- [ ] **Step 3: Implementieren**

`_apply_props` in `services/camera.py` ersetzen:

```python
    def _apply_props(self, cap, warmup_seconds: float | None = None):
        """Gespeicherte Properties setzen und die Kamera einregeln lassen.

        Das Warm-up läuft immer, auch ohne konfigurierte Properties: das erste
        Frame nach dem Öffnen einer USB-Kamera ist praktisch nie korrekt
        belichtet.
        """
        if self._cam_props:
            # Auto toggles first
            for pid in (_PROP_AUTO_WB, _PROP_AUTOFOCUS, _PROP_AUTO_EXPOSURE):
                if pid in self._cam_props:
                    cap.set(pid, self._cam_props[pid])
            # Manual values (skip if corresponding auto is on)
            for pid, val in self._cam_props.items():
                if pid in (_PROP_AUTO_WB, _PROP_AUTOFOCUS, _PROP_AUTO_EXPOSURE):
                    continue
                if pid == _PROP_WB_TEMPERATURE and self._cam_props.get(_PROP_AUTO_WB, 0) > 0.5:
                    continue
                if pid == _PROP_FOCUS and self._cam_props.get(_PROP_AUTOFOCUS, 0) > 0.5:
                    continue
                if pid == _PROP_EXPOSURE and self._cam_props.get(_PROP_AUTO_EXPOSURE, 0) > 1.5:
                    continue
                cap.set(pid, val)

        seconds = self._warmup_seconds if warmup_seconds is None else warmup_seconds
        self._warmup(cap, seconds)

    def _warmup(self, cap, seconds: float, max_frames: int = 60) -> int:
        """Frames verwerfen, bis die Kamera eingeregelt ist.

        Zeitbasiert statt frame-basiert, weil die Bildrate je nach Auflösung
        und Pixelformat stark schwankt. max_frames begrenzt den Aufwand bei
        sehr hohen Bildraten und bricht bei hängender Kamera ab.
        """
        if seconds <= 0:
            return 0
        deadline = time.monotonic() + seconds
        discarded = 0
        while time.monotonic() < deadline and discarded < max_frames:
            ok, _ = cap.read()
            if not ok:
                break
            discarded += 1
        return discarded
```

- [ ] **Step 4: Test ausführen, Erfolg bestätigen**

Run: `python -m pytest tests/test_camera_warmup.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/camera.py tests/test_camera_warmup.py
git commit -m "fix(camera): always run a time-based warm-up before capturing"
```

---

### Task 9: Belichtungsregelung

**Files:**
- Modify: `services/camera.py` — `capture_frame`, neue Methoden `_exposure_manual`, `_capture_balanced`
- Create: `tests/test_camera_exposure.py`

**Interfaces:**
- Consumes: `exposure.measure_brightness`, `exposure.within_tolerance`, `exposure.correction_factor`, `exposure.software_gain` aus Task 5; `_PROP_EXPOSURE`, `_PROP_AUTO_EXPOSURE`, `_V4L2_EXPOSURE_MANUAL` aus Task 6
- Produces: `CameraService._capture_balanced(cap) -> np.ndarray | None`

Wichtig: Ist `_target_brightness > 0`, übernimmt die Regelung die Belichtung und schaltet die Kamera selbst auf manuell. Der `auto_exposure`-Schalter in der UI ist dann wirkungslos — das wird im Dashboard (Task 13) als Hinweis angezeigt. Damit gibt es keine zwei konkurrierenden Bedienelemente für dieselbe Größe.

- [ ] **Step 1: Failing test schreiben**

`tests/test_camera_exposure.py`:

```python
import numpy as np

from services import exposure
from services.camera import (
    CameraService,
    _PROP_AUTO_EXPOSURE,
    _PROP_EXPOSURE,
)


class FakeCap:
    """Kamera-Attrappe: Bildhelligkeit ist proportional zur Belichtungszeit."""

    def __init__(self, start_exposure=100.0, scale=0.5, manual_ok=True):
        self._props = {
            _PROP_EXPOSURE: start_exposure,
            _PROP_AUTO_EXPOSURE: 3.0,
        }
        self._scale = scale
        self._manual_ok = manual_ok
        self.reads = 0

    def read(self):
        self.reads += 1
        value = min(255.0, self._props[_PROP_EXPOSURE] * self._scale)
        return True, np.full((48, 64, 3), value, dtype=np.uint8)

    def get(self, pid):
        return self._props.get(pid, 0.0)

    def set(self, pid, val):
        if pid == _PROP_AUTO_EXPOSURE and not self._manual_ok:
            return False
        self._props[pid] = float(val)
        return True


def _service(target=120.0, tol=12.0):
    cs = CameraService(camera_id=0)
    cs._target_brightness = target
    cs._brightness_tol = tol
    return cs


def test_pulls_dark_frame_towards_target():
    cs = _service()
    cap = FakeCap(start_exposure=100.0)  # Start: Helligkeit 50
    frame = cs._capture_balanced(cap)
    assert exposure.within_tolerance(exposure.measure_brightness(frame), 120.0, 12.0)


def test_pulls_bright_frame_towards_target():
    cs = _service()
    cap = FakeCap(start_exposure=600.0)  # Start: Helligkeit 255 (Anschlag)
    frame = cs._capture_balanced(cap)
    assert exposure.measure_brightness(frame) < 200.0


def test_frame_already_on_target_is_returned_unchanged():
    cs = _service()
    cap = FakeCap(start_exposure=240.0)  # Start: Helligkeit 120
    cs._capture_balanced(cap)
    assert cap.get(_PROP_EXPOSURE) == 240.0, "Keine Korrektur bei erreichtem Ziel"


def test_target_zero_disables_regulation():
    cs = _service(target=0.0)
    cap = FakeCap(start_exposure=100.0)
    frame = cs._capture_balanced(cap)
    assert exposure.measure_brightness(frame) == 50.0
    assert cap.reads == 1


def test_software_gain_fallback_when_manual_unavailable():
    cs = _service()
    cap = FakeCap(start_exposure=100.0, manual_ok=False)
    frame = cs._capture_balanced(cap)
    # Hardware-Regelung nicht möglich, Software-Gain auf 1.3 begrenzt: 50 → 65
    assert 60.0 <= exposure.measure_brightness(frame) <= 70.0


def test_iterations_are_bounded():
    cs = _service(tol=0.0)  # nie erreichbar
    cap = FakeCap(start_exposure=100.0)
    cs._capture_balanced(cap)
    assert cap.reads <= 12, "Regelschleife muss hart begrenzt sein"
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_camera_exposure.py -v`
Expected: FAIL mit `AttributeError: 'CameraService' object has no attribute '_capture_balanced'`

- [ ] **Step 3: Implementieren**

Oben in `services/camera.py` ergänzen:

```python
from services import exposure

# Mehr als drei Korrekturschritte lohnen nicht: die Regelung ist gedämpft und
# konvergiert in zwei Schritten, jeder weitere kostet nur Kamerazeit.
_EXPOSURE_ITERATIONS = 3

# Nach einer Belichtungsänderung braucht der Sensor zwei Frames, bis der neue
# Wert wirklich im Bild ankommt.
_EXPOSURE_SETTLE_FRAMES = 2
```

Neue Methoden in `CameraService`:

```python
    def _exposure_manual(self, cap) -> bool:
        """Auto-Belichtung abschalten. True, wenn der Treiber es übernimmt."""
        cap.set(_PROP_AUTO_EXPOSURE, _V4L2_EXPOSURE_MANUAL)
        return abs(cap.get(_PROP_AUTO_EXPOSURE) - _V4L2_EXPOSURE_MANUAL) < 0.5

    def _capture_balanced(self, cap):
        """Frame lesen und auf die Ziel-Helligkeit regeln.

        Rückgabe ist das Frame mit der geringsten Abweichung zum Ziel, oder
        None wenn kein Frame gelesen werden konnte. Bei _target_brightness <= 0
        wird das erste Frame unverändert zurückgegeben.
        """
        ok, frame = cap.read()
        if not ok:
            return None

        target = self._target_brightness
        if target <= 0:
            return frame

        measured = exposure.measure_brightness(frame)
        best, best_err = frame, abs(measured - target)

        if not self._exposure_manual(cap):
            # Kamera lässt sich nicht manuell belichten – begrenzte
            # Software-Korrektur als Notbehelf.
            factor = exposure.software_gain(measured, target)
            if factor is None:
                return best
            logger.debug(f"cam{self._camera_id}: software gain {factor:.2f}")
            return cv2.convertScaleAbs(best, alpha=factor, beta=0)

        for _ in range(_EXPOSURE_ITERATIONS):
            if exposure.within_tolerance(measured, target, self._brightness_tol):
                return best

            current = cap.get(_PROP_EXPOSURE)
            if current <= 0:
                break

            cap.set(_PROP_EXPOSURE, current * exposure.correction_factor(measured, target))
            if abs(cap.get(_PROP_EXPOSURE) - current) < 1e-6:
                # Treiber hat den Wert nicht übernommen – weitere Versuche
                # würden nur Zeit kosten.
                break

            for _ in range(_EXPOSURE_SETTLE_FRAMES):
                cap.read()
            ok, frame = cap.read()
            if not ok:
                break

            measured = exposure.measure_brightness(frame)
            err = abs(measured - target)
            if err < best_err:
                best, best_err = frame, err

        if best_err > self._brightness_tol:
            logger.info(
                f"cam{self._camera_id}: Ziel-Helligkeit {target:.0f} nicht erreicht "
                f"(Abweichung {best_err:.0f})"
            )
        return best
```

- [ ] **Step 4: `capture_frame` auf `_capture_balanced` umstellen**

In `capture_frame` den `cap.read()`-Aufruf ersetzen:

```python
        try:
            with self._open_capture() as cap:
                self._apply_props(cap)
                frame = self._capture_balanced(cap)
        except CameraBusy as exc:
            logger.error(f"capture_frame: {exc}")
            return None

        if frame is None:
            logger.error("Failed to read frame from camera")
            return None
```

- [ ] **Step 5: Tests ausführen**

Run: `python -m pytest tests/test_camera_exposure.py -v`
Expected: 6 passed

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 6: Commit**

```bash
git add services/camera.py tests/test_camera_exposure.py
git commit -m "feat(camera): regulate exposure towards a target brightness"
```

---

### Task 10: Erkennung über v4l2 mit korrigiertem OpenCV-Fallback

**Files:**
- Modify: `services/camera.py` — `detect_cameras`, `detect_resolutions`, `detect_fps`, neu `detect_formats`
- Create: `tests/test_camera_detect.py`

**Interfaces:**
- Consumes: `v4l2.available`, `v4l2.list_devices`, `v4l2.list_formats` aus Tasks 2–3
- Produces:
  - `detect_cameras() -> list[dict]` mit `{"index": int, "name": str}`
  - `detect_resolutions(camera_index, refresh=False) -> list[dict]` mit `{"width", "height", "label"}`
  - `detect_fps(camera_index, width=0, height=0, refresh=False) -> list[int]`
  - `detect_formats(camera_index, refresh=False) -> list[str]` — z. B. `["MJPG", "YUYV"]`
  - `clear_detect_cache() -> None`

Zwei Ursachen werden behoben. Erstens gab `cap.get(CAP_PROP_FRAME_WIDTH)` bei vielen V4L2-Treibern nur den gesetzten Wunschwert zurück, ohne dass je ein Bild in dieser Auflösung geliefert wurde — die Prüfung erzeugte falsch positive Einträge. Zweitens hing das Ergebnis von der zuvor getesteten Auflösung ab, weil Breite und Höhe nacheinander gesetzt wurden. Der Fallback liest deshalb jetzt ein echtes Frame und prüft gegen `frame.shape`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_camera_detect.py`:

```python
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
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_camera_detect.py -v`
Expected: FAIL mit `AttributeError: module 'services.camera' has no attribute 'clear_detect_cache'`

- [ ] **Step 3: Cache und Hilfsfunktionen implementieren**

Oben in `services/camera.py` ergänzen:

```python
from services import v4l2

_RESOLUTION_LABELS = {(w, h): label for w, h, label in COMMON_RESOLUTIONS if label}

_detect_cache: dict[tuple, object] = {}


def clear_detect_cache() -> None:
    """Erkennungs-Cache leeren – nach Kamerawechsel im laufenden Betrieb."""
    _detect_cache.clear()


def _cached(key: tuple, producer, refresh: bool = False):
    if refresh:
        _detect_cache.pop(key, None)
    if key not in _detect_cache:
        _detect_cache[key] = producer()
    return _detect_cache[key]


def _resolution_label(width: int, height: int) -> str:
    known = _RESOLUTION_LABELS.get((width, height))
    return f"{width}×{height}" + (f" ({known})" if known else "")


def _formats_for(camera_index: int, refresh: bool = False) -> list[dict]:
    return _cached(
        ("formats", camera_index),
        lambda: v4l2.list_formats(f"/dev/video{camera_index}"),
        refresh,
    )
```

- [ ] **Step 4: Erkennungsmethoden ersetzen**

```python
    def detect_cameras(self, refresh: bool = False) -> list[dict]:
        """Verfügbare Kameras. Primär aus sysfs, sonst OpenCV-Probing.

        sysfs ist nicht nur schneller, sondern liefert auch echte Gerätenamen
        und blendet Metadata-Nodes aus, die beim Öffnen scheitern würden.
        """
        def _produce():
            if v4l2.available():
                devices = v4l2.list_devices()
                if devices:
                    return [{"index": d["index"], "name": d["name"]} for d in devices]
            return self._detect_cameras_opencv()

        return _cached(("cameras",), _produce, refresh)

    def _detect_cameras_opencv(self) -> list[dict]:
        if not CV2_AVAILABLE:
            return []
        cameras = []
        for i in range(10):
            lock = device_lock.get(i)
            if not lock.acquire(timeout=0.2):
                continue  # belegt – vermutlich läuft dort eine Aufnahme
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    ret, _ = cap.read()
                    cap.release()
                    if ret:
                        cameras.append({"index": i, "name": f"Kamera {i}"})
            finally:
                lock.release()
        return cameras

    def detect_resolutions(self, camera_index: int, refresh: bool = False) -> list[dict]:
        """Vom Treiber gemeldete Auflösungen, absteigend nach Pixelzahl."""
        formats = _formats_for(camera_index, refresh)
        if formats:
            seen: set[tuple] = set()
            result = []
            for f in formats:
                key = (f["width"], f["height"])
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    {
                        "width": f["width"],
                        "height": f["height"],
                        "label": _resolution_label(f["width"], f["height"]),
                    }
                )
            result.sort(key=lambda r: r["width"] * r["height"], reverse=True)
            return result

        return _cached(
            ("res_cv", camera_index),
            lambda: self._detect_resolutions_opencv(camera_index),
            refresh,
        )

    def detect_formats(self, camera_index: int, refresh: bool = False) -> list[str]:
        """Pixelformate des Treibers, z. B. ["MJPG", "YUYV"]."""
        formats = _formats_for(camera_index, refresh)
        seen: list[str] = []
        for f in formats:
            if f["fourcc"] not in seen:
                seen.append(f["fourcc"])
        return seen

    def detect_fps(
        self, camera_index: int, width: int = 0, height: int = 0, refresh: bool = False
    ) -> list[int]:
        """Bildraten, die der Treiber für diese Auflösung meldet.

        Ohne Auflösung wird die Vereinigung über alle Formate gebildet.
        """
        formats = _formats_for(camera_index, refresh)
        if formats:
            values: set[int] = set()
            for f in formats:
                if width > 0 and height > 0 and (f["width"], f["height"]) != (width, height):
                    continue
                values.update(int(round(v)) for v in f["fps"])
            if values:
                return sorted(values)

        return _cached(
            ("fps_cv", camera_index, width, height),
            lambda: self._detect_fps_opencv(camera_index, width, height),
            refresh,
        )
```

- [ ] **Step 5: OpenCV-Fallback korrigieren**

Der Fallback prüft jetzt gegen ein echtes Frame statt gegen `cap.get()` und setzt FOURCC zuerst:

```python
    def _detect_resolutions_opencv(self, camera_index: int) -> list[dict]:
        """Fallback für Systeme ohne v4l2-ctl (Windows-Entwicklungsmaschine).

        Zwingend: FOURCC vor Breite vor Höhe, und Prüfung gegen ein wirklich
        gelesenes Frame. cap.get() gibt bei vielen Treibern nur den gesetzten
        Wunschwert zurück und erzeugt so falsch positive Treffer.
        """
        if not CV2_AVAILABLE:
            return []
        lock = device_lock.get(camera_index)
        if not lock.acquire(timeout=LOCK_TIMEOUT):
            return []
        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return []
            supported = []
            seen: set[tuple] = set()
            for w, h, label in COMMON_RESOLUTIONS:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*DEFAULT_FOURCC))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                ah, aw = frame.shape[:2]
                if (aw, ah) == (w, h) and (aw, ah) not in seen:
                    seen.add((aw, ah))
                    supported.append(
                        {"width": w, "height": h, "label": _resolution_label(w, h)}
                    )
            cap.release()
            supported.sort(key=lambda r: r["width"] * r["height"], reverse=True)
            return supported
        finally:
            lock.release()

    def _detect_fps_opencv(self, camera_index: int, width: int, height: int) -> list[int]:
        if not CV2_AVAILABLE:
            return []
        lock = device_lock.get(camera_index)
        if not lock.acquire(timeout=LOCK_TIMEOUT):
            return []
        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return []
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*DEFAULT_FOURCC))
            if width > 0 and height > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            supported: list[int] = []
            for fps in COMMON_FPS:
                cap.set(cv2.CAP_PROP_FPS, fps)
                if round(cap.get(cv2.CAP_PROP_FPS)) == fps and fps not in supported:
                    supported.append(fps)
            cap.release()
            return sorted(supported)
        finally:
            lock.release()
```

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest tests/test_camera_detect.py -v`
Expected: 9 passed

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 7: Commit**

```bash
git add services/camera.py tests/test_camera_detect.py
git commit -m "fix(camera): detect resolutions via v4l2 and verify fallback with real frames"
```

---

### Task 11: Deflicker beim Compilen

**Files:**
- Modify: `services/camera.py:306-368` — `compile_timelapse`
- Modify: `api/timelapse.py:95-115` — `compile_session`
- Modify: `db/database.py:12-44` — `DEFAULT_SETTINGS`
- Create: `tests/test_compile_filter.py`

**Interfaces:**
- Consumes: nichts
- Produces:
  - `CameraService.build_video_filter(fps: int, deflicker: bool) -> str` — reine Funktion, testbar ohne ffmpeg
  - `CameraService.compile_timelapse(session, fps=25, deflicker=True)`

Selbst mit eingeregelter Belichtung schwankt die Helligkeit von Bild zu Bild, weil die Kamera für jede Aufnahme neu geöffnet wird. Der Deflicker-Filter gleicht das über ein gleitendes Fenster aus. Im Clip-Modus wird per Stream-Copy zusammengefügt; dort ist kein Filter anwendbar.

- [ ] **Step 1: Failing test schreiben**

`tests/test_compile_filter.py`:

```python
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
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_compile_filter.py -v`
Expected: FAIL mit `AttributeError: type object 'CameraService' has no attribute 'build_video_filter'`

- [ ] **Step 3: Implementieren**

In `services/camera.py` oben ergänzen:

```python
# Fenstergröße des Deflicker-Filters. 7 Frames glätten Belichtungssprünge,
# ohne echte Helligkeitsverläufe über den Tag flachzubügeln.
DEFLICKER_WINDOW = 7
```

Als statische Methode in `CameraService`:

```python
    @staticmethod
    def build_video_filter(fps: int, deflicker: bool) -> str:
        """ffmpeg -vf Filterkette für den Standbild-Modus."""
        chain = [f"fps={fps}"]
        if deflicker:
            chain.append(f"deflicker=size={DEFLICKER_WINDOW}:mode=am")
        return ",".join(chain)
```

In `compile_timelapse` die Signatur und den Still-Zweig anpassen:

```python
    def compile_timelapse(self, session: str, fps: int = 25, deflicker: bool = True) -> str | None:
```

```python
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(list_file),
                    "-vf", self.build_video_filter(fps, deflicker),
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-crf", "23",
                    str(output_file),
                ]
```

- [ ] **Step 4: Einstellung ergänzen und durchreichen**

In `db/database.py` zu `DEFAULT_SETTINGS` hinzufügen:

```python
    "timelapse_deflicker": True,   # Helligkeitssprünge beim Compilen ausgleichen
```

In `api/timelapse.py` in `compile_session`:

```python
    settings = await state.db.get_all_settings()
    fps = int(settings.get(f"cam_{cam}_timelapse_fps", settings.get("timelapse_fps", 25)))
    deflicker = bool(settings.get("timelapse_deflicker", True))

    _compile_jobs[session] = "running"

    def _compile():
        result = cs.compile_timelapse(session, fps=fps, deflicker=deflicker)
        _compile_jobs[session] = "done" if result else "error"
```

- [ ] **Step 5: Tests ausführen**

Run: `python -m pytest tests/test_compile_filter.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add services/camera.py api/timelapse.py db/database.py tests/test_compile_filter.py
git commit -m "feat(timelapse): apply deflicker filter when compiling still frames"
```

---

### Task 12: Einstellungen verdrahten

**Files:**
- Modify: `services/camera.py` — `setup()` um Belichtungsparameter erweitern
- Modify: `main.py:34-59`
- Modify: `services/scheduler.py:164-231` — `_timelapse_loop`
- Modify: `api/settings.py:26`
- Modify: `api/timelapse.py` — `/status`, `/resolutions`, `/fps`, neu `/formats`

**Interfaces:**
- Consumes: `CameraService.setup(..., fourcc=...)` aus Task 7, `detect_formats` aus Task 10
- Produces:
  - `CameraService.setup(frames_dir, output_dir, camera_index, capture_width, capture_height, fourcc, target_brightness, brightness_tol, warmup_seconds)`
  - `GET /api/timelapse/formats?camera=N` → `{"formats": ["MJPG", "YUYV"]}`
  - `refresh`-Query-Parameter auf `/cameras`, `/resolutions`, `/fps`, `/formats`
  - HTTP 409 statt leerer Liste, wenn die Kamera belegt ist

- [ ] **Step 1: `setup()` erweitern**

```python
    def setup(
        self,
        frames_dir: str = "timelapse/frames",
        output_dir: str = "timelapse/output",
        camera_index: int = 0,
        capture_width: int = 0,
        capture_height: int = 0,
        fourcc: str = DEFAULT_FOURCC,
        target_brightness: float = 120.0,
        brightness_tol: float = 12.0,
        warmup_seconds: float = 1.5,
    ):
        self._frames_dir = Path(frames_dir)
        self._output_dir = Path(output_dir)
        self._camera_index = camera_index
        self._capture_width = capture_width
        self._capture_height = capture_height
        self._fourcc = fourcc or DEFAULT_FOURCC
        self._target_brightness = float(target_brightness)
        self._brightness_tol = float(brightness_tol)
        self._warmup_seconds = float(warmup_seconds)
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: Gemeinsamen Settings-Leser einführen**

Die Ableitung der Kamera-Parameter steht identisch in `main.py`, `api/settings.py` und `services/scheduler.py`. Sie kommt in eine Funktion in `services/camera.py`:

```python
def camera_setup_kwargs(settings: dict, cam: int, tl_path: str) -> dict:
    """Setup-Parameter einer Kamera aus den Settings ableiten.

    Kamera 0 fällt auf die alten, kameraunabhängigen Schlüssel zurück, damit
    bestehende Installationen ihre Einstellungen behalten.
    """
    def cam_get(name: str, default, legacy: str | None = None):
        key = f"cam_{cam}_{name}"
        if key in settings:
            return settings[key]
        if cam == 0 and legacy is not None and legacy in settings:
            return settings[legacy]
        return default

    return {
        "frames_dir": f"{tl_path}/cam{cam}/frames",
        "output_dir": f"{tl_path}/cam{cam}/output",
        "camera_index": int(cam_get("device_index", cam, "camera_index")),
        "capture_width": int(cam_get("capture_width", 0, "camera_capture_width")),
        "capture_height": int(cam_get("capture_height", 0, "camera_capture_height")),
        "fourcc": str(cam_get("fourcc", DEFAULT_FOURCC)),
        "target_brightness": float(cam_get("target_brightness", 120.0)),
        "brightness_tol": float(cam_get("brightness_tol", 12.0)),
        "warmup_seconds": float(cam_get("warmup_seconds", 1.5)),
    }
```

- [ ] **Step 3: Aufrufer umstellen**

In `main.py` den Block ab Zeile 36 ersetzen:

```python
    from services.camera import camera_setup_kwargs  # oben zum Import ergänzen

    for i in range(camera_count):
        cam = state.get_camera(i)
        cam.setup(**camera_setup_kwargs(settings, i, tl_path))
        cam_props: dict[str, float] = {}
        for pdef in CAMERA_PROPERTIES:
            val = settings.get(f"cam_{i}_prop_{pdef['key']}")
            if val is not None:
                cam_props[pdef["key"]] = float(val)
        if cam_props:
            cam.set_properties(cam_props)
```

In `api/settings.py` die beiden `cam.setup(...)`-Blöcke ebenso ersetzen und das Muster in Zeile 26 erweitern:

```python
    cam_keys_pattern = re.compile(
        r"cam_(\d+)_(device_index|capture_width|capture_height|fourcc"
        r"|target_brightness|brightness_tol|warmup_seconds)"
    )
```

In `services/scheduler.py._timelapse_loop` den Konfigurationsblock ersetzen:

```python
                kwargs = camera_setup_kwargs(settings, cam_idx, tl_path)
                if kwargs != last_cam_config:
                    cam.setup(**kwargs)
                    last_cam_config = kwargs
```

Die restlichen per-Kamera-Werte im Loop (`active`, `interval`, `capture_mode`, `clip_duration`, `clip_fps`) bleiben zunächst unverändert; Plan 2 baut diesen Abschnitt um.

- [ ] **Step 4: API erweitern**

In `api/timelapse.py` oben ergänzen:

```python
from services.camera import CameraBusy
```

Die drei Erkennungs-Endpunkte um `refresh` und 409-Behandlung erweitern, dazu den neuen Endpunkt:

```python
@router.get("/cameras")
async def detect_cameras(refresh: bool = Query(False)):
    """Verfügbare Kameras. refresh=1 umgeht den Cache."""
    cs = _cam(0)
    try:
        cameras = await asyncio.to_thread(cs.detect_cameras, refresh)
    except CameraBusy as exc:
        raise HTTPException(409, str(exc))
    return {"cameras": cameras}


@router.get("/resolutions")
async def detect_resolutions(camera: int = Query(0, ge=0), refresh: bool = Query(False)):
    cs = _cam(0)
    try:
        resolutions = await asyncio.to_thread(cs.detect_resolutions, camera, refresh)
    except CameraBusy as exc:
        raise HTTPException(409, str(exc))
    return {"resolutions": resolutions}


@router.get("/formats")
async def detect_formats(camera: int = Query(0, ge=0), refresh: bool = Query(False)):
    """Pixelformate, die der Treiber meldet."""
    cs = _cam(0)
    try:
        formats = await asyncio.to_thread(cs.detect_formats, camera, refresh)
    except CameraBusy as exc:
        raise HTTPException(409, str(exc))
    return {"formats": formats}


@router.get("/fps")
async def detect_fps(
    camera: int = Query(0, ge=0),
    width: int = Query(0),
    height: int = Query(0),
    refresh: bool = Query(False),
):
    cs = _cam(0)
    try:
        fps_list = await asyncio.to_thread(cs.detect_fps, camera, width, height, refresh)
    except CameraBusy as exc:
        raise HTTPException(409, str(exc))
    return {"fps": fps_list}
```

`get_camera_properties` und `camera_preview` ebenso in `try/except CameraBusy` klammern und 409 werfen.

In `get_status` die neuen Felder ergänzen:

```python
        "fourcc":            settings.get(f"cam_{cam}_fourcc", "MJPG"),
        "target_brightness": settings.get(f"cam_{cam}_target_brightness", 120),
        "brightness_tol":    settings.get(f"cam_{cam}_brightness_tol", 12),
        "warmup_seconds":    settings.get(f"cam_{cam}_warmup_seconds", 1.5),
```

- [ ] **Step 5: Anwendung starten und prüfen**

Run: `start_local.bat` (bzw. `venv/Scripts/uvicorn.exe main:app --host 127.0.0.1 --port 8080`)

Prüfen im Browser oder per curl:
- `http://localhost:8080/api/timelapse/status?cam=0` enthält `fourcc`, `target_brightness`, `brightness_tol`, `warmup_seconds`
- `http://localhost:8080/api/timelapse/formats?camera=0` antwortet mit einer Liste (unter Windows leer, da kein v4l2 — das ist korrekt)
- `http://localhost:8080/api/timelapse/resolutions?camera=0` liefert Auflösungen über den korrigierten OpenCV-Fallback

Erwartet: keine Exceptions im Log, Dashboard lädt.

- [ ] **Step 6: Tests ausführen**

Run: `python -m pytest`
Expected: alles grün

- [ ] **Step 7: Commit**

```bash
git add services/camera.py services/scheduler.py main.py api/settings.py api/timelapse.py
git commit -m "feat(camera): wire fourcc and exposure settings through setup and API"
```

---

### Task 13: Dashboard – generische Kamera-Regler

**Files:**
- Modify: `static/js/app.js:588-642` (fest verdrahteter Property-Block), `static/js/app.js:905-990` (Laden/Setzen der Properties)
- Modify: `static/css/style.css`

**Interfaces:**
- Consumes: `GET /api/timelapse/camera/properties?cam=N` (liefert bereits `key`, `label`, `type`, `value`, `min`, `max`, `step`, `unit`, `auto_key`, `supported`)
- Produces: `renderCamProps(ci, props)` — baut den Reglerblock aus der API-Antwort

Mit fünf neuen Properties wird der handgeschriebene HTML-Block unhaltbar und müsste doppelt gepflegt werden. Er wird deshalb aus der API-Antwort erzeugt, die alle nötigen Metadaten bereits mitliefert.

- [ ] **Step 1: Statischen Block durch Container ersetzen**

In `buildCameraSection(ci)` den gesamten Bereich von `<div class="cam-prop-row">` bis `<div id="cam-props-hint-${ci}" ...>` ersetzen durch:

```html
      <div id="cam-settings-${ci}" class="cam-settings hidden">
        <div class="control-row" data-tooltip="Angestrebte mittlere Bildhelligkeit. 0 schaltet die Regelung ab und überlässt die Belichtung der Kamera.">
          <label>Ziel-Helligkeit</label>
          <input type="range" id="cam-target-brightness-${ci}" min="0" max="255" step="1" value="120"
                 oninput="document.getElementById('cam-target-brightness-val-${ci}').textContent=this.value"
                 onchange="setExposureSetting(${ci})" />
          <span class="cam-prop-val" id="cam-target-brightness-val-${ci}">120</span>
        </div>
        <div class="control-row" data-tooltip="Zulässige Abweichung von der Ziel-Helligkeit.">
          <label>Toleranz</label>
          <input type="range" id="cam-brightness-tol-${ci}" min="1" max="60" step="1" value="12"
                 oninput="document.getElementById('cam-brightness-tol-val-${ci}').textContent=this.value"
                 onchange="setExposureSetting(${ci})" />
          <span class="cam-prop-val" id="cam-brightness-tol-val-${ci}">12</span>
        </div>
        <div class="control-row" data-tooltip="Wartezeit nach dem Öffnen der Kamera, bevor das Bild gespeichert wird. Zu kurz gewählt schwankt die Belichtung.">
          <label>Aufwärmzeit</label>
          <input type="range" id="cam-warmup-${ci}" min="0" max="5" step="0.1" value="1.5"
                 oninput="document.getElementById('cam-warmup-val-${ci}').textContent=this.value+'s'"
                 onchange="setExposureSetting(${ci})" />
          <span class="cam-prop-val" id="cam-warmup-val-${ci}">1,5s</span>
        </div>

        <div id="cam-exposure-note-${ci}" class="cam-props-hint hidden">
          Die Ziel-Helligkeit steuert die Belichtung selbst. Auto-Belichtung und
          Belichtung wirken erst, wenn die Ziel-Helligkeit auf 0 steht.
        </div>

        <div id="cam-props-${ci}"></div>
        <div id="cam-props-hint-${ci}" class="cam-props-hint hidden">Grau = von Kamera nicht unterstuetzt</div>
      </div>
```

- [ ] **Step 2: Generisches Rendern implementieren**

`renderCamProps` neu anlegen und in `loadCamProps(ci)` aufrufen:

```javascript
function renderCamProps(ci, props) {
  const host = document.getElementById(`cam-props-${ci}`);
  if (!host) return;

  const autoOn = {};
  props.filter(p => p.type === 'bool').forEach(p => { autoOn[p.key] = p.value > 0.5; });

  host.innerHTML = props.map(p => {
    const id  = `cam-prop-${p.key}-${ci}`;
    const dis = p.supported === false ? ' disabled' : '';
    const cls = p.supported === false ? ' unsupported' : '';

    if (p.type === 'bool') {
      const checked = p.value > 0.5 ? ' checked' : '';
      return `
        <div class="cam-prop-row${cls}">
          <label>${escHtml(p.label)}</label>
          <label class="toggle-switch">
            <input type="checkbox" id="${id}"${checked}${dis}
                   onchange="setCamProp(${ci},'${p.key}',this.checked?1:0)" />
            <span class="toggle-slider"></span>
          </label>
        </div>`;
    }

    // Manueller Regler ist wirkungslos, solange sein Auto-Schalter an ist.
    const lockedByAuto = p.auto_key && autoOn[p.auto_key];
    const unit  = p.unit || '';
    const value = Math.round(p.value);
    return `
      <div class="cam-prop-row${cls}${lockedByAuto ? ' locked' : ''}">
        <label>${escHtml(p.label)}</label>
        <input type="range" id="${id}"
               min="${p.min}" max="${p.max}" step="${p.step}" value="${value}"
               ${dis}${lockedByAuto ? ' disabled' : ''}
               oninput="document.getElementById('${id}-val').textContent=this.value+'${unit}'"
               onchange="setCamProp(${ci},'${p.key}',+this.value)" />
        <span class="cam-prop-val" id="${id}-val">${value}${unit}</span>
      </div>`;
  }).join('');

  const anyUnsupported = props.some(p => p.supported === false);
  document.getElementById(`cam-props-hint-${ci}`).classList.toggle('hidden', !anyUnsupported);
}
```

- [ ] **Step 3: `loadCamProps` und `setCamProp` anpassen**

`loadCamProps(ci)` ruft künftig nur noch `renderCamProps(ci, d.properties || [])` auf; der bisherige Code, der einzelne Element-IDs setzt, entfällt vollständig. Auf HTTP 409 wird eine Meldung gezeigt statt eines leeren Blocks:

```javascript
async function loadCamProps(ci) {
  try {
    const r = await fetch(`${API}/api/timelapse/camera/properties?cam=${ci}`);
    if (r.status === 409) {
      showToast('Kamera gerade in Benutzung – Einstellungen später laden');
      return;
    }
    const d = await r.json();
    renderCamProps(ci, d.properties || []);
    updateExposureNote(ci);
  } catch(e) {}
}
```

`setCamProp(ci, key, value)` bleibt inhaltlich unverändert, ruft am Ende aber `loadCamProps(ci)` auf, damit abhängige Regler (Auto-Schalter sperrt seinen manuellen Regler) sofort korrekt aussehen.

- [ ] **Step 4: Belichtungseinstellungen speichern**

```javascript
async function setExposureSetting(ci) {
  const body = {
    [`cam_${ci}_target_brightness`]: +document.getElementById(`cam-target-brightness-${ci}`).value,
    [`cam_${ci}_brightness_tol`]:    +document.getElementById(`cam-brightness-tol-${ci}`).value,
    [`cam_${ci}_warmup_seconds`]:    +document.getElementById(`cam-warmup-${ci}`).value,
  };
  await fetch(`${API}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  updateExposureNote(ci);
  showToast('Belichtung gespeichert');
}

function updateExposureNote(ci) {
  const target = +document.getElementById(`cam-target-brightness-${ci}`).value;
  document.getElementById(`cam-exposure-note-${ci}`).classList.toggle('hidden', target <= 0);
}
```

In `fetchTimelapse(ci)` die drei Regler aus dem Status befüllen:

```javascript
    const tb = document.getElementById(`cam-target-brightness-${ci}`);
    tb.value = d.target_brightness ?? 120;
    document.getElementById(`cam-target-brightness-val-${ci}`).textContent = tb.value;
    const bt = document.getElementById(`cam-brightness-tol-${ci}`);
    bt.value = d.brightness_tol ?? 12;
    document.getElementById(`cam-brightness-tol-val-${ci}`).textContent = bt.value;
    const wu = document.getElementById(`cam-warmup-${ci}`);
    wu.value = d.warmup_seconds ?? 1.5;
    document.getElementById(`cam-warmup-val-${ci}`).textContent = formatDE(+wu.value, 1) + 's';
    updateExposureNote(ci);
```

- [ ] **Step 5: CSS ergänzen**

An `static/css/style.css` anhängen:

```css
.cam-prop-row.locked {
  opacity: 0.45;
}
.cam-prop-row.unsupported label,
.cam-prop-row.unsupported .cam-prop-val {
  color: var(--text3);
}
```

- [ ] **Step 6: Manuell prüfen**

Run: `start_local.bat`, dann `http://localhost:8080` öffnen.

Prüfen:
- Kamera-Einstellungen aufklappen: die drei Belichtungsregler stehen oben, darunter die aus der API gerenderten Property-Regler.
- Ziel-Helligkeit auf 0 ziehen: der Hinweistext verschwindet.
- Ziel-Helligkeit auf 120: Hinweistext erscheint.
- Auto-Weissabgleich einschalten: der Weissabgleich-Regler wird ausgegraut.
- Seite neu laden: alle Werte bleiben erhalten.

- [ ] **Step 7: Commit**

```bash
git add static/js/app.js static/css/style.css
git commit -m "feat(ui): render camera property sliders from API and add exposure controls"
```

---

### Task 14: Dashboard – Format- und Auflösungsauswahl

**Files:**
- Modify: `static/js/app.js:515-575` (`buildCameraSection`), `:691-757` (`loadCamerasForSlot`, `loadResolutions`, `loadFps`), `:843-879` (`startTimelapse`)
- Modify: `static/index.html:355-380` (Einstellungsbereich)

**Interfaces:**
- Consumes: `GET /api/timelapse/formats`, `refresh`-Parameter und HTTP 409 aus Task 12
- Produces: Pixelformat-Auswahl pro Kamera, Deflicker-Schalter in den Einstellungen

- [ ] **Step 1: Format-Dropdown einbauen**

In `buildCameraSection(ci)` vor der Auflösungszeile einfügen:

```html
      <div class="control-row" data-tooltip="Pixelformat der Kamera. MJPG erlaubt bei USB-Kameras hohe Auflösungen; YUYV ist meist auf 640x480 begrenzt.">
        <label>Pixelformat</label>
        <select id="tl-fourcc-${ci}" onchange="loadResolutions(${ci}, document.getElementById('tl-cam-idx-${ci}').value)">
          <option value="MJPG">MJPG</option>
        </select>
      </div>
```

Der Refresh-Knopf neben der Kameraauswahl bekommt `loadCamerasForSlot(${ci}, true)`.

- [ ] **Step 2: Formate laden**

```javascript
async function loadFormats(ci, devIdx, refresh = false) {
  const sel = document.getElementById(`tl-fourcc-${ci}`);
  if (!sel) return;
  const prev = sel.value;
  try {
    const r = await fetch(`${API}/api/timelapse/formats?camera=${devIdx}${refresh ? '&refresh=1' : ''}`);
    if (r.status === 409) { showToast('Kamera gerade in Benutzung'); return; }
    const d = await r.json();
    const list = (d.formats && d.formats.length) ? d.formats : ['MJPG'];
    sel.innerHTML = list.map(f => `<option value="${escHtml(f)}">${escHtml(f)}</option>`).join('');
    if (list.includes(prev)) sel.value = prev;
  } catch(e) {}
}
```

- [ ] **Step 3: `loadResolutions` und `loadFps` um refresh und 409 erweitern**

```javascript
async function loadResolutions(ci, devIdx, refresh = false) {
  const sel = document.getElementById(`tl-resolution-${ci}`);
  const prev = sel.value;
  sel.disabled = true;
  try {
    const r = await fetch(`${API}/api/timelapse/resolutions?camera=${devIdx}${refresh ? '&refresh=1' : ''}`);
    if (r.status === 409) {
      showToast('Kamera gerade in Benutzung – Auflösungen unverändert');
      sel.disabled = false;
      return;
    }
    const d = await r.json();
    sel.innerHTML = '<option value="0x0">Kamera Standard</option>';
    (d.resolutions || []).forEach(res => {
      const opt = document.createElement('option');
      opt.value = `${res.width}x${res.height}`;
      opt.textContent = res.label;
      sel.appendChild(opt);
    });

    const sr = await fetch(`${API}/api/settings`);
    const s  = await sr.json();
    const wKey = ci === 0 ? (s.cam_0_capture_width ?? s.camera_capture_width ?? 0) : (s[`cam_${ci}_capture_width`] ?? 0);
    const hKey = ci === 0 ? (s.cam_0_capture_height ?? s.camera_capture_height ?? 0) : (s[`cam_${ci}_capture_height`] ?? 0);
    const saved = `${wKey}x${hKey}`;
    if ([...sel.options].some(o => o.value === saved))      sel.value = saved;
    else if ([...sel.options].some(o => o.value === prev))   sel.value = prev;
  } catch(e) {}
  sel.disabled = false;
  await loadFps(ci, devIdx, sel.value, refresh);
}
```

Wichtig gegenüber der bisherigen Fassung: Das Dropdown wird erst **nach** einer erfolgreichen Antwort geleert. Bisher wurde es sofort auf „Kamera Standard" zurückgesetzt und blieb bei einem Fehler leer — das war der sichtbare Teil des Problems.

`loadFps` analog um `refresh` erweitern und bei 409 die bestehende Liste stehen lassen.

- [ ] **Step 4: `loadCamerasForSlot` und `startTimelapse` anpassen**

```javascript
async function loadCamerasForSlot(ci, refresh = false) {
  const select = document.getElementById(`tl-cam-idx-${ci}`);
  const prev = select.value;
  select.disabled = true;
  await loadAllCameras(refresh);
  select.disabled = false;
  if ([...select.options].some(o => o.value === prev)) select.value = prev;
  await loadFormats(ci, select.value, refresh);
  await loadResolutions(ci, select.value, refresh);
}
```

`loadAllCameras(refresh)` hängt `&refresh=1` an `/api/timelapse/cameras` an.

In `initCameraSections()` nach `loadAllCameras()` für jede Kamera zusätzlich `loadFormats(i, ...)` aufrufen.

In `startTimelapse(ci)` das Format mitspeichern:

```javascript
      [`cam_${ci}_fourcc`]: document.getElementById(`tl-fourcc-${ci}`).value,
```

In `fetchTimelapse(ci)` das Dropdown aus dem Status setzen:

```javascript
    const fcSel = document.getElementById(`tl-fourcc-${ci}`);
    if (d.fourcc && [...fcSel.options].some(o => o.value === d.fourcc)) fcSel.value = d.fourcc;
```

- [ ] **Step 5: Deflicker-Schalter in den Einstellungen**

In `static/index.html` im Timelapse-Bereich der Einstellungen (bei `timelapse-share`) ergänzen:

```html
        <div class="control-row" data-tooltip="Gleicht Helligkeitssprünge zwischen aufeinanderfolgenden Bildern beim Erstellen des Videos aus. Nur im Standbild-Modus wirksam.">
          <label for="timelapse-deflicker">Helligkeit angleichen</label>
          <input type="checkbox" id="timelapse-deflicker" />
        </div>
```

In `app.js` in `loadSettings()`:

```javascript
    document.getElementById('timelapse-deflicker').checked = s.timelapse_deflicker ?? true;
```

und in `saveSettings()` im `body`-Objekt:

```javascript
    timelapse_deflicker: document.getElementById('timelapse-deflicker').checked,
```

- [ ] **Step 6: Manuell prüfen**

Run: `start_local.bat`, dann `http://localhost:8080` öffnen.

Prüfen:
- Pixelformat-Dropdown ist gefüllt (unter Windows nur „MJPG", auf dem Pi die echte Liste).
- Refresh-Knopf neben der Kameraauswahl lädt neu, ohne die getroffene Auswahl zu verlieren.
- Auflösungs-Dropdown behält seinen Wert, wenn die Anfrage fehlschlägt.
- Einstellungen: „Helligkeit angleichen" lässt sich speichern und übersteht einen Reload.

Auf dem Pi zusätzlich prüfen:
- Auflösungsliste enthält 1920×1080, sofern die Kamera es kann.
- „Kameras suchen" während einer laufenden Aufnahme meldet „Kamera gerade in Benutzung" statt eine leere Liste zu liefern, und die Aufnahme läuft weiter.

- [ ] **Step 7: Tests ausführen und committen**

Run: `python -m pytest`
Expected: alles grün

```bash
git add static/js/app.js static/index.html
git commit -m "feat(ui): add pixel format selection, cache refresh and busy-camera handling"
```

---

## Abschlussprüfung auf der Zielhardware

Nach Task 14 auf dem Raspberry Pi verifizieren — die Kernprobleme sind ohne echte Kamera nicht nachweisbar:

- [ ] `v4l2-ctl --list-formats-ext -d /dev/video0` von Hand ausführen und mit der Liste im Dashboard vergleichen. Sie müssen übereinstimmen.
- [ ] Timelapse mit 1-Minuten-Intervall über eine Stunde laufen lassen, quer über Sonnenauf- oder -untergang. Die gespeicherten JPEGs auf gleichmäßige Helligkeit prüfen.
- [ ] Während der laufenden Aufnahme im Dashboard „Kameras suchen" und die Vorschau anstoßen. Es darf kein Frame ausfallen (`frame_count` steigt lückenlos).
- [ ] Session compilen und im Video auf Helligkeitssprünge achten.
- [ ] `journalctl -u greenhouse -n 200` auf `Ziel-Helligkeit ... nicht erreicht` prüfen — häufige Meldungen deuten auf eine Kamera hin, deren Belichtung sich nicht steuern lässt.
