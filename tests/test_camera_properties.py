"""Wertedomäne der Schalter-Properties und die davon abhängige Unterdrückung.

`type: "bool"` heißt "Schalter", nicht "0 oder 1": V4L2s exposure_auto ist ein
Menü (3 = automatisch, 1 = manuell), 0 lehnt uvcvideo ab.
"""

import numpy as np
import pytest

from services.camera import (
    CAMERA_PROPERTIES,
    CameraService,
    _PROP_AUTO_EXPOSURE,
    _PROP_AUTO_WB,
    _PROP_EXPOSURE,
    _PROP_WB_TEMPERATURE,
)

BOOL_PROPS = [p for p in CAMERA_PROPERTIES if p["type"] == "bool"]


class RecordingCap:
    """Kamera-Attrappe, die jeden set()-Aufruf mitschreibt."""

    def __init__(self):
        self.sets: list[tuple] = []

    def read(self):
        return True, np.zeros((8, 8, 3), dtype=np.uint8)

    def get(self, pid):
        return 0.0

    def set(self, pid, val):
        self.sets.append((pid, float(val)))
        return True

    def values_for(self, pid):
        return [val for p, val in self.sets if p == pid]


# --- Deklarierte Wertedomäne ---

@pytest.mark.parametrize("pdef", BOOL_PROPS, ids=[p["key"] for p in BOOL_PROPS])
def test_bool_props_declare_both_values(pdef):
    assert "on_value" in pdef, f"{pdef['key']} ohne on_value"
    assert "off_value" in pdef, f"{pdef['key']} ohne off_value"


def test_auto_exposure_uses_v4l2_menu_values():
    pdef = next(p for p in CAMERA_PROPERTIES if p["key"] == "auto_exposure")
    assert pdef["on_value"] == 3
    assert pdef["off_value"] == 1


@pytest.mark.parametrize("key", ["auto_wb", "autofocus"])
def test_genuine_booleans_stay_zero_one(key):
    pdef = next(p for p in CAMERA_PROPERTIES if p["key"] == key)
    assert pdef["on_value"] == 1
    assert pdef["off_value"] == 0


# --- Unterdrückung manueller Werte hinter aktivem Auto-Schalter ---
#
# Ziel-Helligkeit 0: die Regelung ist aus, der Nutzer besitzt die Belichtung.
# Nur so sind die gespeicherten Belichtungs-Properties überhaupt wirksam.

def _service_without_regulation():
    cs = CameraService(camera_id=0)
    cs._target_brightness = 0.0
    return cs


def test_manual_exposure_suppressed_when_auto_exposure_on():
    cs = _service_without_regulation()
    cs.set_properties({"auto_exposure": 3, "exposure": 400})
    cap = RecordingCap()
    cs._apply_props(cap, warmup_seconds=0)
    assert cap.values_for(_PROP_AUTO_EXPOSURE) == [3.0]
    assert cap.values_for(_PROP_EXPOSURE) == []


def test_manual_exposure_applied_when_auto_exposure_off():
    cs = _service_without_regulation()
    cs.set_properties({"auto_exposure": 1, "exposure": 400})
    cap = RecordingCap()
    cs._apply_props(cap, warmup_seconds=0)
    assert cap.values_for(_PROP_AUTO_EXPOSURE) == [1.0]
    assert cap.values_for(_PROP_EXPOSURE) == [400.0]


def test_manual_wb_suppressed_when_auto_wb_on():
    cs = _service_without_regulation()
    cs.set_properties({"auto_wb": 1, "white_balance": 4200})
    cap = RecordingCap()
    cs._apply_props(cap, warmup_seconds=0)
    assert cap.values_for(_PROP_AUTO_WB) == [1.0]
    assert cap.values_for(_PROP_WB_TEMPERATURE) == []


def test_manual_wb_applied_when_auto_wb_off():
    cs = _service_without_regulation()
    cs.set_properties({"auto_wb": 0, "white_balance": 4200})
    cap = RecordingCap()
    cs._apply_props(cap, warmup_seconds=0)
    assert cap.values_for(_PROP_AUTO_WB) == [0.0]
    assert cap.values_for(_PROP_WB_TEMPERATURE) == [4200.0]


def test_auto_switch_is_set_before_its_manual_value():
    """Umgekehrte Reihenfolge würde der Automatikmodus sofort überschreiben."""
    cs = _service_without_regulation()
    cs.set_properties({"white_balance": 4200, "auto_wb": 0})
    cap = RecordingCap()
    cs._apply_props(cap, warmup_seconds=0)
    order = [pid for pid, _ in cap.sets]
    assert order.index(_PROP_AUTO_WB) < order.index(_PROP_WB_TEMPERATURE)
