import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import state
from services.scheduler import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

_scheduler: Scheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler

    # Database
    await state.db.init()

    # Read all settings once
    settings = await state.db.get_all_settings()

    # Camera instances (one per configured camera slot)
    tl_path = settings.get("timelapse_path") or "timelapse"
    camera_count = int(settings.get("camera_count", 1))
    for i in range(camera_count):
        cam = state.get_camera(i)
        # Per-camera settings, with fallback to legacy keys for camera 0
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

    # Fan: read GPIO pin from DB and initialise
    gpio_pin = int(settings.get("fan_gpio_pin") or 18)
    state.fan_controller.setup(gpio_pin)
    state.fan_controller._configured_pin = gpio_pin

    # Background scheduler
    _scheduler = Scheduler(
        switchbot=state.switchbot_service,
        fan_controller=state.fan_controller,
        db=state.db,
    )
    await _scheduler.start()

    yield

    state.fan_controller.stop()
    await _scheduler.stop()
    await state.db.close()


app = FastAPI(title="Greenhouse Control", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
from api import sensors, fans, timelapse, settings as settings_api, update, filesystem, system, wifi, tailscale  # noqa: E402

app.include_router(sensors.router,      prefix="/api/sensors",    tags=["sensors"])
app.include_router(fans.router,         prefix="/api/fans",       tags=["fans"])
app.include_router(timelapse.router,    prefix="/api/timelapse",  tags=["timelapse"])
app.include_router(settings_api.router, prefix="/api/settings",   tags=["settings"])
app.include_router(update.router,       prefix="/api/update",     tags=["update"])
app.include_router(filesystem.router,   prefix="/api/fs",         tags=["filesystem"])
app.include_router(system.router,       prefix="/api/system",     tags=["system"])
app.include_router(wifi.router,         prefix="/api/wifi",       tags=["wifi"])
app.include_router(tailscale.router,   prefix="/api/tailscale",  tags=["tailscale"])

# Static dashboard (must be last)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
