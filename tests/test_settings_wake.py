"""Ein Zeitplanwechsel im Dashboard muss den Timelapse-Loop sofort wecken.

Ohne das Wecken greift eine geänderte Uhrzeit erst beim nächsten Poll des
Loops – bis zu SETTINGS_POLL_SECONDS später.
"""

import asyncio

import pytest

import state
from api.settings import update_settings


class StubDB:
    def __init__(self, settings):
        self._settings = settings

    async def update_settings(self, updates):
        self._settings.update(updates)

    async def get_all_settings(self):
        return dict(self._settings)


@pytest.fixture
def db(monkeypatch):
    stub = StubDB({"camera_count": 4, "timelapse_path": "timelapse"})
    monkeypatch.setattr(state, "db", stub)
    for cam in range(4):
        state.get_timelapse_wake(cam).clear()
    yield stub
    for cam in range(4):
        state.get_timelapse_wake(cam).clear()


def woken(updates: dict) -> set[int]:
    asyncio.run(update_settings(updates))
    return {cam for cam in range(4) if state.get_timelapse_wake(cam).is_set()}


def test_schedule_mode_wakes_its_camera(db):
    assert woken({"cam_0_schedule_mode": "times"}) == {0}


def test_schedule_times_wake_the_right_camera(db):
    assert woken({"cam_2_schedule_times": ["08:00"]}) == {2}


def test_start_and_end_wake(db):
    assert woken({"cam_1_schedule_start": "06:00", "cam_1_schedule_end": "20:00"}) == {1}


def test_interval_wakes(db):
    assert woken({"cam_3_timelapse_interval": 600}) == {3}


def test_dates_wake(db):
    assert woken({"cam_0_date_from": "2026-04-01", "cam_0_date_to": "2026-09-30"}) == {0}


def test_oneshots_wake(db):
    assert woken({"cam_2_oneshots": ["2026-04-01 08:00"]}) == {2}


def test_legacy_interval_wakes_camera_zero(db):
    """Der alte, kameraunabhängige Schlüssel gilt weiterhin für Kamera 0."""
    assert woken({"timelapse_interval": 600}) == {0}


def test_unrelated_setting_wakes_nobody(db):
    """Sonst würde jede Einstellungsänderung den Loop unnötig neu rechnen lassen."""
    assert woken({"cam_0_clip_fps": 12, "target_temperature": 24.0}) == set()
