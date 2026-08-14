"""Feuchteumrechnung nach der Magnus-Formel.

Für die Entscheidung, ob Lüften die Feuchte im Gewächshaus senkt, ist der
Vergleich der *relativen* Feuchte unbrauchbar: kalte Außenluft mit 90 % rF
enthält deutlich weniger Wasser als warme Innenluft mit 70 % rF und trocknet
nach dem Aufwärmen kräftig. Verglichen werden muss die absolute Feuchte.

Die Konstanten gelten für Sättigung über Wasser. Unter dem Gefrierpunkt weicht
das leicht von der Sättigung über Eis ab – für die Regelentscheidung
unerheblich.
"""

import math

_MAGNUS_A = 17.67
_MAGNUS_B = 243.5

# 6.112 hPa Sättigungsdampfdruck bei 0 °C; 2.1674 = M_Wasser / R  in
# passenden Einheiten, damit das Ergebnis in g/m³ herauskommt.
_ES0 = 6.112
_GM3 = 2.1674

# 0 % rF würde im Logarithmus zu -inf führen.
_RH_MIN = 0.1


def _clamp_rh(rel_hum: float) -> float:
    return max(_RH_MIN, min(100.0, float(rel_hum)))


def abs_humidity(temp_c: float, rel_hum: float) -> float:
    """Absolute Feuchte in g/m³."""
    rh = _clamp_rh(rel_hum)
    saturation = _ES0 * math.exp(_MAGNUS_A * temp_c / (temp_c + _MAGNUS_B))
    return saturation * rh * _GM3 / (273.15 + temp_c)


def dew_point(temp_c: float, rel_hum: float) -> float:
    """Taupunkt in °C."""
    rh = _clamp_rh(rel_hum)
    alpha = math.log(rh / 100.0) + _MAGNUS_A * temp_c / (temp_c + _MAGNUS_B)
    return _MAGNUS_B * alpha / (_MAGNUS_A - alpha)
