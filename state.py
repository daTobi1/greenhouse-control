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

# Ein Wake-Event je Kamera-Slot. Ein gemeinsames Event funktioniert nicht:
# jeder Loop ruft clear() auf, bevor er wartet, und würde damit das Signal
# für einen anderen Loop verschlucken.
_timelapse_wakes: dict[int, asyncio.Event] = {}


def get_timelapse_wake(cam: int = 0) -> asyncio.Event:
    """Event, mit dem der Timelapse-Loop einer Kamera sofort aufwacht."""
    if cam not in _timelapse_wakes:
        _timelapse_wakes[cam] = asyncio.Event()
    return _timelapse_wakes[cam]
