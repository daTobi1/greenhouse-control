"""
Background scheduler: runs BLE scanning, fan control, timelapse capture,
and sensor logging as independent asyncio tasks.
"""

import asyncio
import logging

import state as _state

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, switchbot, fan_controller, db):
        self._sb   = switchbot
        self._fan  = fan_controller
        self._db   = db
        self._tasks: list[asyncio.Task] = []
        self._tl_tasks: dict[int, asyncio.Task] = {}  # per-camera timelapse tasks
        self._running = False

    async def start(self):
        self._running = True
        self._tasks = [
            asyncio.create_task(self._ble_loop(),             name="ble_scan"),
            asyncio.create_task(self._fan_loop(),             name="fan_control"),
            asyncio.create_task(self._timelapse_manager(),    name="timelapse_mgr"),
            asyncio.create_task(self._log_loop(),             name="sensor_log"),
        ]
        logger.info("Scheduler started")

    async def stop(self):
        self._running = False
        for t in self._tl_tasks.values():
            t.cancel()
        for t in self._tasks:
            t.cancel()
        all_tasks = self._tasks + list(self._tl_tasks.values())
        await asyncio.gather(*all_tasks, return_exceptions=True)
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

                regulation_enabled = settings.get("regulation_enabled", True)

                if not regulation_enabled:
                    self._fan.set_speed(0.0)
                    speed = 0.0
                    reason = "disabled"
                    if speed != last_logged_speed:
                        await self._db.log_fan_event(speed, reason)
                        last_logged_speed = speed
                    await asyncio.sleep(interval)
                    continue

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
    # Timelapse manager – spawns/removes per-camera loops dynamically
    # ------------------------------------------------------------------

    async def _timelapse_manager(self):
        """Check camera_count periodically and manage per-camera loops."""
        while self._running:
            try:
                settings = await self._db.get_all_settings()
                camera_count = int(settings.get("camera_count", 1))

                # Start loops for new cameras
                for i in range(camera_count):
                    if i not in self._tl_tasks or self._tl_tasks[i].done():
                        cam = _state.get_camera(i)
                        self._tl_tasks[i] = asyncio.create_task(
                            self._timelapse_loop(i), name=f"timelapse_cam{i}"
                        )
                        logger.info(f"Timelapse loop started for camera {i}")

                # Cancel loops for removed cameras
                for i in list(self._tl_tasks):
                    if i >= camera_count:
                        self._tl_tasks[i].cancel()
                        del self._tl_tasks[i]
                        logger.info(f"Timelapse loop stopped for camera {i}")

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Timelapse manager error: {exc}")
                await asyncio.sleep(10)

    async def _timelapse_loop(self, cam_idx: int):
        """Timelapse capture loop for a single camera slot."""
        last_cam_config = None
        while self._running:
            try:
                settings     = await self._db.get_all_settings()
                tl_path      = settings.get("timelapse_path") or "timelapse"
                cam          = _state.get_camera(cam_idx)

                # Per-camera settings with fallback to legacy keys for cam 0
                if cam_idx == 0:
                    active       = settings.get("cam_0_timelapse_active", settings.get("timelapse_active", False))
                    interval     = float(settings.get("cam_0_timelapse_interval", settings.get("timelapse_interval", 300)))
                    dev_idx      = int(settings.get("cam_0_device_index", settings.get("camera_index", 0)))
                    cap_w        = int(settings.get("cam_0_capture_width", settings.get("camera_capture_width", 0)))
                    cap_h        = int(settings.get("cam_0_capture_height", settings.get("camera_capture_height", 0)))
                    capture_mode = settings.get("cam_0_capture_mode", settings.get("capture_mode", "still"))
                    clip_duration= float(settings.get("cam_0_clip_duration", settings.get("clip_duration", 5)))
                    clip_fps     = int(settings.get("cam_0_clip_fps", settings.get("clip_fps", 10)))
                else:
                    active       = settings.get(f"cam_{cam_idx}_timelapse_active", False)
                    interval     = float(settings.get(f"cam_{cam_idx}_timelapse_interval", 300))
                    dev_idx      = int(settings.get(f"cam_{cam_idx}_device_index", cam_idx))
                    cap_w        = int(settings.get(f"cam_{cam_idx}_capture_width", 0))
                    cap_h        = int(settings.get(f"cam_{cam_idx}_capture_height", 0))
                    capture_mode = settings.get(f"cam_{cam_idx}_capture_mode", "still")
                    clip_duration= float(settings.get(f"cam_{cam_idx}_clip_duration", 5))
                    clip_fps     = int(settings.get(f"cam_{cam_idx}_clip_fps", 10))

                # Setup nur bei Konfigurationsänderung
                cam_config = (tl_path, dev_idx, cap_w, cap_h)
                if cam_config != last_cam_config:
                    cam.setup(
                        frames_dir=f"{tl_path}/cam{cam_idx}/frames",
                        output_dir=f"{tl_path}/cam{cam_idx}/output",
                        camera_index=dev_idx,
                        capture_width=cap_w,
                        capture_height=cap_h,
                    )
                    last_cam_config = cam_config

                if active:
                    if not cam.is_capturing:
                        cam.start_session()
                    if capture_mode == "clip":
                        await asyncio.to_thread(
                            cam.capture_clip, clip_duration, clip_fps
                        )
                    else:
                        await asyncio.to_thread(cam.capture_frame)
                else:
                    if cam.is_capturing:
                        cam.stop_session()

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
                logger.error(f"Timelapse loop cam{cam_idx} error: {exc}")
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
