"""
SwitchBot BLE sensor integration.

Supports SwitchBot Meter, Meter Plus, and Outdoor Meter (IP65 / WoIOSensor).
Reads temperature, humidity, and battery level from BLE advertisements.

SwitchBot service data UUID: 0000fd3d-0000-1000-8000-00805f9b34fb
Service data format (bytes):
  [0] device type
  [1] status flags
  [2] battery level (0-100, bit7 masked)
  [3] temperature decimal part (lower nibble = 0.X)
  [4] temperature integer + sign (bit7=1 positive, bits6-0 = integer)
  [5] humidity (bits6-0)
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SWITCHBOT_SVC_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"

try:
    from bleak import BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    logger.warning("bleak not installed – BLE scanning unavailable")


def _parse_service_data(data: bytes) -> dict | None:
    """Parse SwitchBot Meter advertisement service data."""
    if len(data) < 6:
        return None
    battery = data[2] & 0x7F
    temp_dec = data[3] & 0x0F
    temp_int = data[4] & 0x7F
    temp_positive = bool(data[4] & 0x80)
    humidity = data[5] & 0x7F

    temperature = round(temp_int + temp_dec * 0.1, 1)
    if not temp_positive:
        temperature = -temperature

    return {
        "temperature": temperature,
        "humidity": humidity,
        "battery": battery,
        "timestamp": datetime.now().isoformat(),
    }


def _extract_service_data(adv_data) -> bytes | None:
    """Find SwitchBot service data in advertisement, trying multiple UUID forms."""
    svc = adv_data.service_data or {}
    # Try full UUID first
    if SWITCHBOT_SVC_UUID in svc:
        return svc[SWITCHBOT_SVC_UUID]
    # Fallback: any UUID containing fd3d
    for uuid, raw in svc.items():
        if "fd3d" in uuid.lower():
            return raw
    return None


class SwitchBotService:
    def __init__(self):
        self._sensor_data: dict[str, dict] = {}  # role -> parsed data
        self._known: dict[str, str] = {}          # mac_upper -> role

    def set_known_devices(self, inside_mac: str, outside_mac: str):
        self._known = {}
        if inside_mac:
            self._known[inside_mac.upper().strip()] = "inside"
        if outside_mac:
            self._known[outside_mac.upper().strip()] = "outside"

    def _on_advertisement(self, device, adv_data):
        mac = device.address.upper()

        # Discovery mode: log all SwitchBot devices
        if not self._known:
            raw = _extract_service_data(adv_data)
            if raw is not None:
                logger.info(
                    f"SwitchBot discovered: {mac}  name={device.name}  rssi={adv_data.rssi}"
                )
            return

        if mac not in self._known:
            return

        raw = _extract_service_data(adv_data)
        if raw is None:
            return

        parsed = _parse_service_data(raw)
        if parsed:
            role = self._known[mac]
            self._sensor_data[role] = {**parsed, "mac": mac, "rssi": adv_data.rssi}
            logger.debug(f"[{role}] temp={parsed['temperature']}°C  hum={parsed['humidity']}%  bat={parsed['battery']}%")

    async def scan_once(self, duration: float = 10.0):
        """Run a BLE scan for `duration` seconds and collect advertisements."""
        if not BLEAK_AVAILABLE:
            logger.warning("BLE not available")
            return
        try:
            scanner = BleakScanner(detection_callback=self._on_advertisement)
            await scanner.start()
            import asyncio
            await asyncio.sleep(duration)
            await scanner.stop()
        except Exception as exc:
            logger.error(f"BLE scan failed: {exc}")

    async def discover_devices(self, duration: float = 10.0) -> list[dict]:
        """Return all nearby SwitchBot devices (for MAC setup UI)."""
        if not BLEAK_AVAILABLE:
            return []

        found: list[dict] = []

        def _cb(device, adv_data):
            raw = _extract_service_data(adv_data)
            if raw is not None:
                entry = {
                    "mac": device.address,
                    "name": device.name or "SwitchBot",
                    "rssi": adv_data.rssi,
                }
                if not any(d["mac"] == device.address for d in found):
                    found.append(entry)

        scanner = BleakScanner(detection_callback=_cb)
        import asyncio
        await scanner.start()
        await asyncio.sleep(duration)
        await scanner.stop()
        return found

    def get_sensor_data(self, role: str) -> dict | None:
        return self._sensor_data.get(role)

    def get_all_data(self) -> dict:
        return dict(self._sensor_data)
