import logging
import os
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

    # Camera directories
    state.camera_service.setup()

    # Fan: read GPIO pin from DB and initialise
    settings = await state.db.get_all_settings()
    gpio_pin = int(settings.get("fan_gpio_pin", 18))
    state.fan_controller.setup(gpio_pin)
    state.fan_controller._configured_pin = gpio_pin

    # Background scheduler
    _scheduler = Scheduler(
        switchbot=state.switchbot_service,
        fan_controller=state.fan_controller,
        camera_service=state.camera_service,
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
from api import sensors, fans, timelapse, settings as settings_api, update  # noqa: E402

app.include_router(sensors.router,      prefix="/api/sensors",   tags=["sensors"])
app.include_router(fans.router,         prefix="/api/fans",      tags=["fans"])
app.include_router(timelapse.router,    prefix="/api/timelapse", tags=["timelapse"])
app.include_router(settings_api.router, prefix="/api/settings",  tags=["settings"])
app.include_router(update.router,       prefix="/api/update",    tags=["update"])

# Timelapse videos
os.makedirs("timelapse/output", exist_ok=True)
app.mount("/timelapse", StaticFiles(directory="timelapse/output"), name="timelapse_videos")

# Static dashboard (must be last)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
