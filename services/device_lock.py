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
