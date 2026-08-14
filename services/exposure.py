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

# Korrektionen unter 2 % lohnen die Rechenzeit nicht.
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
