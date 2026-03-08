"""
Background scheduler: runs BLE scanning, fan control, timelapse capture,
and sensor logging as independent asyncio tasks.
"""

import asyncio
import logging

import state as _state

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, switchbot, fan_controller, camera_service, db):
        self._sb   = switchbot
        self._fan  = fan_controller
        self._cam  = camera_service
        self._db   = db
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self):
        self._running = True
        self._tasks = [
            asyncio.create_task(self._ble_loop(),       name="ble_scan"),
            asyncio.create_task(self._fan_loop(),       name="fan_control"),
            asyncio.create_task(self._timelapse_loop(), name="timelapse"),
            asyncio.create_task(self._log_loop(),       name="sensor_log"),
        ]
        logger.info("Scheduler started")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Scheduler stopped")

    # ------------------------------------------------------------------
    # BLE scan loop
    # ------------------------------------------------------------------

    async def _ble_loop(self):
        # Brief startup delay so settings are loaded
        await asyncio.sleep(2)
        while self._running:
            try:
                settings      = await self._db.get_all_settings()
                inside_mac    = settings.get("inside_sensor_mac", "")
                outside_mac   = settings.get("outside_sensor_mac", "")
                scan_interval = float(settings.get("ble_scan_interval") or 30)
                scan_duration = float(settings.get("ble_scan_duration") or 10)

                if inside_mac or outside_mac:
                    self._sb.set_known_devices(inside_mac, outside_mac)
                    await self._sb.scan_once(duration=scan_duration)

                await asyncio.sleep(scan_interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"BLE loop error: {exc}")
                await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Fan control loop
    # ------------------------------------------------------------------

    async def _fan_loop(self):
        configured_pin = None
        last_logged_speed = None
        while self._running:
            try:
                settings = await self._db.get_all_settings()
                gpio_pin = int(settings.get("fan_gpio_pin") or 18)
                interval = float(settings.get("fan_update_interval") or 10)

                if configured_pin != gpio_pin:
                    self._fan.setup(gpio_pin)
                    configured_pin = gpio_pin

                manual_override = settings.get("fan_manual_override", False)

                if manual_override:
                    speed = float(settings.get("fan_manual_speed", 0.0))
                    self._fan.set_speed(speed)
                    reason = "manual"
                else:
                    inside  = self._sb.get_sensor_data("inside")
                    outside = self._sb.get_sensor_data("outside")
                    if inside:
                        speed = self._fan.calculate_speed(inside, outside, settings)
                        self._fan.set_speed(speed)
                        reason = "auto"
                    else:
                        speed = None
                        reason = None

                # Nur loggen wenn sich die Geschwindigkeit geändert hat
                if speed is not None and speed != last_logged_speed:
                    await self._db.log_fan_event(speed, reason)
                    last_logged_speed = speed

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Fan control loop error: {exc}")
                await asyncio.sleep(10)

    # ------------------------------------------------------------------
    # Timelapse loop
    # ------------------------------------------------------------------

    async def _timelapse_loop(self):
        last_cam_config = None
        while self._running:
            try:
                settings     = await self._db.get_all_settings()
                active       = settings.get("timelapse_active", False)
                interval     = float(settings.get("timelapse_interval") or 300)
                tl_path      = settings.get("timelapse_path") or "timelapse"
                cam_idx      = int(settings.get("camera_index") or 0)
                cap_w        = int(settings.get("camera_capture_width") or 0)
                cap_h        = int(settings.get("camera_capture_height") or 0)
                capture_mode = settings.get("capture_mode", "still")
                clip_duration= float(settings.get("clip_duration") or 5)
                clip_fps     = int(settings.get("clip_fps") or 10)

                # Setup nur bei Konfigurationsänderung
                cam_config = (tl_path, cam_idx, cap_w, cap_h)
                if cam_config != last_cam_config:
                    self._cam.setup(
                        frames_dir=f"{tl_path}/frames",
                        output_dir=f"{tl_path}/output",
                        camera_index=cam_idx,
                        capture_width=cap_w,
                        capture_height=cap_h,
                    )
                    last_cam_config = cam_config

                if active:
                    if not self._cam.is_capturing:
                        self._cam.start_session()
                    if capture_mode == "clip":
                        await asyncio.to_thread(
                            self._cam.capture_clip, clip_duration, clip_fps
                        )
                    else:
                        await asyncio.to_thread(self._cam.capture_frame)
                else:
                    if self._cam.is_capturing:
                        self._cam.stop_session()

                # Wait for interval OR wake event (whichever comes first)
                _state.timelapse_wake.clear()
                try:
                    await asyncio.wait_for(
                        _state.timelapse_wake.wait(), timeout=interval
                    )
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Timelapse loop error: {exc}")
                await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Sensor log loop (every 60 s)
    # ------------------------------------------------------------------

    async def _log_loop(self):
        while self._running:
            try:
                for role in ("inside", "outside"):
                    data = self._sb.get_sensor_data(role)
                    if data and "temperature" in data:
                        await self._db.log_sensor_reading(role, data)

                await asyncio.sleep(60)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Sensor log loop error: {exc}")
                await asyncio.sleep(60)
