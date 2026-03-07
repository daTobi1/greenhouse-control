from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import state

router = APIRouter()


class ManualSpeedRequest(BaseModel):
    speed: float = Field(..., ge=0.0, le=1.0, description="Fan speed 0.0 … 1.0")


@router.get("/status")
async def get_status():
    """Current fan speed and control mode."""
    settings = await state.db.get_all_settings()
    return {
        "speed":           state.fan_controller.current_speed,
        "speed_percent":   round(state.fan_controller.current_speed * 100, 1),
        "manual_override": settings.get("fan_manual_override", False),
        "manual_speed":    settings.get("fan_manual_speed", 0.0),
        "control_mode":    settings.get("control_mode", "combined"),
        "mock_mode":       state.fan_controller._mock,
    }


@router.post("/manual")
async def set_manual(req: ManualSpeedRequest):
    """Switch to manual mode and set fan speed."""
    await state.db.update_settings({
        "fan_manual_override": True,
        "fan_manual_speed":    req.speed,
    })
    state.fan_controller.set_speed(req.speed)
    return {"speed": req.speed, "manual_override": True}


@router.post("/auto")
async def set_auto():
    """Switch back to automatic control."""
    await state.db.update_settings({"fan_manual_override": False})
    return {"manual_override": False}


@router.get("/history")
async def get_history(hours: int = 24):
    """Fan speed history."""
    events = await state.db.get_fan_events(hours)
    return {"events": events}
