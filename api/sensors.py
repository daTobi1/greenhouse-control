from fastapi import APIRouter, Query, BackgroundTasks

import state
from services import psychrometrics

router = APIRouter()


def _enrich(role: str, max_age: float) -> dict | None:
    """Messwert um Alter, Frischestatus und Feuchtekennwerte ergänzen."""
    data = state.switchbot_service.get_sensor_data(role)
    if not data:
        return None

    age = state.switchbot_service.data_age(role)
    result = dict(data)
    result["age_seconds"] = round(age, 1) if age is not None else None
    result["stale"] = age is None or age > max_age

    temp = data.get("temperature")
    hum = data.get("humidity")
    if temp is not None and hum is not None:
        result["abs_humidity"] = round(psychrometrics.abs_humidity(temp, hum), 2)
        result["dew_point"] = round(psychrometrics.dew_point(temp, hum), 1)

    return result


@router.get("/current")
async def get_current():
    """Current sensor readings for inside and outside."""
    settings = await state.db.get_all_settings()
    max_age = float(settings.get("sensor_max_age") or 300)
    return {
        "inside":  _enrich("inside", max_age),
        "outside": _enrich("outside", max_age),
        "max_age_seconds": max_age,
    }


@router.get("/history")
async def get_history(
    hours: int = Query(default=24, ge=1, le=720),
    max_points: int = Query(default=0, ge=0, le=2000),
    from_ts: str = Query(default=None),
    to_ts: str = Query(default=None),
):
    """Historical sensor readings (up to 30 days)."""
    if from_ts and to_ts:
        inside  = await state.db.get_readings_range("inside", from_ts, to_ts)
        outside = await state.db.get_readings_range("outside", from_ts, to_ts)
    else:
        inside  = await state.db.get_readings("inside",  hours)
        outside = await state.db.get_readings("outside", hours)
    if max_points > 0:
        inside  = _downsample(inside, max_points)
        outside = _downsample(outside, max_points)
    return {"inside": inside, "outside": outside}


def _downsample(rows: list, max_points: int) -> list:
    if len(rows) <= max_points:
        return rows
    step = len(rows) / max_points
    return [rows[int(i * step)] for i in range(max_points)]


@router.post("/discover")
async def discover_sensors():
    """
    Scan for nearby SwitchBot Bluetooth devices (10 seconds).
    Returns MAC addresses suitable for the settings panel.
    """
    devices = await state.switchbot_service.discover_devices(duration=10.0)
    return {"devices": devices}
