import re
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

    # Re-setup cameras when per-camera settings change
    tl_path = settings.get("timelapse_path", "timelapse")
    camera_count = int(settings.get("camera_count", 1))
    cam_keys_pattern = re.compile(r"cam_(\d+)_(device_index|capture_width|capture_height)")
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
        if i == 0:
            dev_idx = int(settings.get("cam_0_device_index", settings.get("camera_index", 0)))
            cap_w   = int(settings.get("cam_0_capture_width", settings.get("camera_capture_width", 0)))
            cap_h   = int(settings.get("cam_0_capture_height", settings.get("camera_capture_height", 0)))
        else:
            dev_idx = int(settings.get(f"cam_{i}_device_index", i))
            cap_w   = int(settings.get(f"cam_{i}_capture_width", 0))
            cap_h   = int(settings.get(f"cam_{i}_capture_height", 0))
        cam.setup(
            frames_dir=f"{tl_path}/cam{i}/frames",
            output_dir=f"{tl_path}/cam{i}/output",
            camera_index=dev_idx,
            capture_width=cap_w,
            capture_height=cap_h,
        )

    # Initialize new camera instances when camera_count increases
    if "camera_count" in updates:
        for i in range(camera_count):
            if i not in state.camera_services:
                cam = state.get_camera(i)
                dev_idx = int(settings.get(f"cam_{i}_device_index", i))
                cap_w   = int(settings.get(f"cam_{i}_capture_width", 0))
                cap_h   = int(settings.get(f"cam_{i}_capture_height", 0))
                cam.setup(
                    frames_dir=f"{tl_path}/cam{i}/frames",
                    output_dir=f"{tl_path}/cam{i}/output",
                    camera_index=dev_idx,
                    capture_width=cap_w,
                    capture_height=cap_h,
                )

    return settings
