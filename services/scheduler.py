"""
Background scheduler: runs BLE scanning, fan control, timelapse capture,
and sensor logging as independent asyncio tasks.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import state as _state
from services.camera import camera_setup_kwargs
from services import schedule

logger = logging.getLogger(__name__)

# Längster Schlaf am Stück. Begrenzt, damit Einstellungsänderungen auch bei
# stundenlangen Intervallen zeitnah greifen.
SETTINGS_POLL_SECONDS = 60.0

# Pause, wenn nichts zu tun ist (Aufnahme inaktiv oder kein Zeitpunkt planbar).
IDLE_POLL_SECONDS = 30.0


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
                        decision = self._fan.calculate_speed(inside, outside, settings)
                        speed = decision.speed
                        self._fan.set_speed(speed)
                        reason = decision.reason
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

    async def _sleep_until(self, wake: asyncio.Event, target: datetime) -> bool:
        """Bis `target` schlafen. True, wenn das Wake-Event ausgelöst hat.

        Wird in Häppchen geschlafen, damit Einstellungsänderungen greifen und
        ein Sprung der Systemuhr nicht zu einem stundenlangen Schlaf führt.
        """
        while self._running:
            if wake.is_set():
                wake.clear()
                return True
            remaining = (target - datetime.now()).total_seconds()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(
                    wake.wait(), timeout=min(remaining, SETTINGS_POLL_SECONDS)
                )
            except asyncio.TimeoutError:
                continue
            wake.clear()
            return True
        return False

    async def _sleep_for(self, wake: asyncio.Event, seconds: float) -> bool:
        return await self._sleep_until(
            wake, datetime.now() + timedelta(seconds=seconds)
        )

    async def _timelapse_loop(self, cam_idx: int):
        """Timelapse-Aufnahme einer Kamera nach ihrem Zeitplan."""
        last_cam_config = None
        last_capture: datetime | None = None
        wake = _state.get_timelapse_wake(cam_idx)

        while self._running:
            try:
                settings = await self._db.get_all_settings()
                tl_path  = settings.get("timelapse_path") or "timelapse"
                cam      = _state.get_camera(cam_idx)

                def cam_get(name, default, legacy=None):
                    key = f"cam_{cam_idx}_{name}"
                    if settings.get(key) is not None:
                        return settings[key]
                    if cam_idx == 0 and legacy is not None and settings.get(legacy) is not None:
                        return settings[legacy]
                    return default

                active        = cam_get("timelapse_active", False, "timelapse_active")
                capture_mode  = cam_get("capture_mode", "still", "capture_mode")
                clip_duration = float(cam_get("clip_duration", 5, "clip_duration"))
                clip_fps      = int(cam_get("clip_fps", 10, "clip_fps"))

                # Setup nur bei Konfigurationsänderung
                kwargs = camera_setup_kwargs(settings, cam_idx, tl_path)
                if kwargs != last_cam_config:
                    cam.setup(**kwargs)
                    last_cam_config = kwargs

                if not active:
                    if cam.is_capturing:
                        cam.stop_session()
                    last_capture = None
                    await self._sleep_for(wake, IDLE_POLL_SECONDS)
                    continue

                if not cam.is_capturing:
                    cam.start_session()

                cfg = schedule.parse_config(settings, cam_idx)
                target = schedule.next_due(datetime.now(), cfg, last_capture)
                if target is None:
                    logger.debug(f"cam{cam_idx}: kein Aufnahmezeitpunkt planbar")
                    await self._sleep_for(wake, IDLE_POLL_SECONDS)
                    continue

                if await self._sleep_until(wake, target):
                    continue  # Einstellungen geändert – Zeitpunkt neu berechnen
                if not self._running:
                    break  # Herunterfahren, nicht als verfallene Aufnahme werten

                now = datetime.now()
                if schedule.is_due(now, target, cfg.grace_seconds):
                    if capture_mode == "clip":
                        await asyncio.to_thread(cam.capture_clip, clip_duration, clip_fps)
                    else:
                        await asyncio.to_thread(cam.capture_frame)
                else:
                    logger.info(
                        f"cam{cam_idx}: Aufnahme {target:%Y-%m-%d %H:%M} verfallen "
                        f"(Kulanzfenster {cfg.grace_seconds:.0f}s überschritten)"
                    )
                # In beiden Fällen: Rhythmus ab jetzt fortsetzen, nicht nachholen.
                last_capture = now

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
