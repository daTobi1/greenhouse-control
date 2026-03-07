from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

import state

router = APIRouter()


@router.get("")
async def get_settings():
    """Return all settings."""
    return await state.db.get_all_settings()


@router.put("")
async def update_settings(updates: dict[str, Any]):
    """Update one or more settings."""
    await state.db.update_settings(updates)
    settings = await state.db.get_all_settings()
    # Re-setup camera when timelapse path or camera index changes
    if any(k in updates for k in ("timelapse_path", "camera_index")):
        tl_path = settings.get("timelapse_path", "timelapse")
        state.camera_service.setup(
            frames_dir=f"{tl_path}/frames",
            output_dir=f"{tl_path}/output",
            camera_index=int(settings.get("camera_index", 0)),
        )
    return settings
