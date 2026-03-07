from fastapi import APIRouter, Query, BackgroundTasks

import state

router = APIRouter()


@router.get("/current")
async def get_current():
    """Current sensor readings for inside and outside."""
    return {
        "inside":  state.switchbot_service.get_sensor_data("inside"),
        "outside": state.switchbot_service.get_sensor_data("outside"),
    }


@router.get("/history")
async def get_history(hours: int = Query(default=24, ge=1, le=168)):
    """Historical sensor readings (up to 7 days)."""
    inside  = await state.db.get_readings("inside",  hours)
    outside = await state.db.get_readings("outside", hours)
    return {"inside": inside, "outside": outside}


@router.post("/discover")
async def discover_sensors():
    """
    Scan for nearby SwitchBot Bluetooth devices (10 seconds).
    Returns MAC addresses suitable for the settings panel.
    """
    devices = await state.switchbot_service.discover_devices(duration=10.0)
    return {"devices": devices}
