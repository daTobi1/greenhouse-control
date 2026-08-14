import re
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

import state
from services.camera import camera_setup_kwargs

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

    # Re-setup cameras when per-camera settings change
    tl_path = settings.get("timelapse_path", "timelapse")
    camera_count = int(settings.get("camera_count", 1))
    cam_keys_pattern = re.compile(
        r"cam_(\d+)_(device_index|capture_width|capture_height|fourcc"
        r"|target_brightness|brightness_tol|warmup_seconds)"
    )
    legacy_keys = {"timelapse_path", "camera_index", "camera_capture_width", "camera_capture_height"}

    affected_cams: set[int] = set()
    if any(k in updates for k in legacy_keys):
        affected_cams.add(0)
    for k in updates:
        m = cam_keys_pattern.match(k)
        if m:
            affected_cams.add(int(m.group(1)))

    for i in affected_cams:
        if i >= camera_count:
            continue
        cam = state.get_camera(i)
        cam.setup(**camera_setup_kwargs(settings, i, tl_path))

    # Initialize new camera instances when camera_count increases
    if "camera_count" in updates:
        for i in range(camera_count):
            if i not in state.camera_services:
                cam = state.get_camera(i)
                cam.setup(**camera_setup_kwargs(settings, i, tl_path))

    return settings
