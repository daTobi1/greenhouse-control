from datetime import datetime, timedelta

from services.switchbot import SwitchBotService

NOW = datetime(2026, 8, 14, 12, 0, 0)


def _service(age_seconds: float | None = 0.0, role: str = "inside"):
    svc = SwitchBotService()
    if age_seconds is not None:
        svc._sensor_data[role] = {
            "temperature": 22.5,
            "humidity": 60,
            "battery": 90,
            "timestamp": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        }
    return svc


def test_age_of_fresh_reading():
    assert _service(12.0).data_age("inside", now=NOW) == 12.0


def test_age_of_missing_role_is_none():
    assert _service().data_age("outside", now=NOW) is None


def test_age_without_timestamp_is_none():
    svc = SwitchBotService()
    svc._sensor_data["inside"] = {"temperature": 20.0}
    assert svc.data_age("inside", now=NOW) is None


def test_age_with_broken_timestamp_is_none():
    svc = SwitchBotService()
    svc._sensor_data["inside"] = {"temperature": 20.0, "timestamp": "kaputt"}
    assert svc.data_age("inside", now=NOW) is None


def test_fresh_data_is_returned():
    svc = _service(60.0)
    assert svc.get_sensor_data("inside", max_age_s=300, now=NOW) is not None


def test_stale_data_is_withheld():
    svc = _service(600.0)
    assert svc.get_sensor_data("inside", max_age_s=300, now=NOW) is None


def test_boundary_is_inclusive():
    svc = _service(300.0)
    assert svc.get_sensor_data("inside", max_age_s=300, now=NOW) is not None


def test_without_max_age_stale_data_is_still_returned():
    """Bestehende Aufrufer dürfen sich nicht ändern."""
    svc = _service(99999.0)
    assert svc.get_sensor_data("inside") is not None


def test_data_without_timestamp_counts_as_stale():
    svc = SwitchBotService()
    svc._sensor_data["inside"] = {"temperature": 20.0}
    assert svc.get_sensor_data("inside", max_age_s=300, now=NOW) is None
