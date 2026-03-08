"""Global service instances shared across the application."""
import asyncio

from db.database import Database
from services.switchbot import SwitchBotService
from services.fan_controller import FanController
from services.camera import CameraService

db = Database("greenhouse.db")
switchbot_service = SwitchBotService()
fan_controller = FanController()
camera_service = CameraService()

# Event to wake up the timelapse scheduler loop immediately
timelapse_wake = asyncio.Event()
