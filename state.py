"""Global service instances shared across the application."""
import asyncio

from db.database import Database
from services.switchbot import SwitchBotService
from services.fan_controller import FanController
from services.camera import CameraService

db = Database("greenhouse.db")
switchbot_service = SwitchBotService()
fan_controller = FanController()

# Multiple camera instances, keyed by slot index (0, 1, 2, …)
camera_services: dict[int, CameraService] = {}

def get_camera(cam: int = 0) -> CameraService:
    """Return camera service for given slot, creating it if needed."""
    if cam not in camera_services:
        camera_services[cam] = CameraService(camera_id=cam)
    return camera_services[cam]

# Event to wake up the timelapse scheduler loop immediately
timelapse_wake = asyncio.Event()
