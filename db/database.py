import json
import logging
import time
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

_SETTINGS_CACHE_TTL = 5  # Sekunden

DEFAULT_SETTINGS = {
    "inside_sensor_mac": "",
    "outside_sensor_mac": "",
    "target_temperature": 25.0,
    "target_humidity": 65.0,
    "control_mode": "combined_or",  # temperature | humidity | combined_or | combined_and
    "fan_gpio_pin": 18,
    "fan_min_speed": 0.2,
    "fan_max_speed": 1.0,
    "temp_control_range": 5.0,       # full speed at +5°C above target
    "humidity_control_range": 20.0,  # full speed at +20% above target
    "fan_update_interval": 10,        # seconds
    "ble_scan_interval": 30,          # seconds
    "ble_scan_duration": 10,          # seconds per scan
    "timelapse_active": False,
    "timelapse_interval": 300,        # seconds between frames
    "timelapse_fps": 25,
    "camera_index": 0,
    "fan_deadband": 0.1,             # hysteresis: min raw speed (0..1) to start fan
    "fan_manual_override": False,
    "fan_manual_speed": 0.0,
    "update_check_interval_days": 7,  # 0 = deaktiviert
    "timelapse_path": "timelapse",
    "timelapse_share_enabled": False,
    "camera_capture_width": 0,   # 0 = camera default
    "camera_capture_height": 0,
    "capture_mode": "still",     # still | clip
    "clip_duration": 5,          # seconds per clip
    "clip_fps": 10,              # fps of recorded clip
}


class Database:
    def __init__(self, path: str):
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._settings_cache: dict | None = None
        self._settings_cache_ts: float = 0

    async def init(self):
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._seed_defaults()
        logger.info(f"Database initialized: {self._path}")

    async def _create_tables(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                role      TEXT    NOT NULL,
                temperature REAL,
                humidity    REAL,
                battery     INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_readings_ts   ON sensor_readings(timestamp);
            CREATE INDEX IF NOT EXISTS idx_readings_role ON sensor_readings(role);

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fan_events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT  NOT NULL,
                speed     REAL  NOT NULL,
                reason    TEXT
            );
        """)
        await self._conn.commit()

    async def _seed_defaults(self):
        for key, value in DEFAULT_SETTINGS.items():
            await self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            # Overwrite stored 'null' with the proper default
            await self._conn.execute(
                "UPDATE settings SET value = ? WHERE key = ? AND value = 'null'",
                (json.dumps(value), key),
            )
        # Migrate legacy "combined" → "combined_or"
        await self._conn.execute(
            "UPDATE settings SET value = '\"combined_or\"' WHERE key = 'control_mode' AND value = '\"combined\"'"
        )
        await self._conn.commit()

    # --- Sensor readings ---

    async def log_sensor_reading(self, role: str, data: dict):
        await self._conn.execute(
            "INSERT INTO sensor_readings (timestamp, role, temperature, humidity, battery) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data.get("timestamp", datetime.now().isoformat()),
                role,
                data.get("temperature"),
                data.get("humidity"),
                data.get("battery"),
            ),
        )
        await self._conn.commit()

    async def get_readings(self, role: str, hours: int = 24) -> list[dict]:
        async with self._conn.execute(
            """
            SELECT timestamp, temperature, humidity, battery
            FROM sensor_readings
            WHERE role = ?
              AND timestamp > datetime('now', ? || ' hours')
            ORDER BY timestamp ASC
            """,
            (role, f"-{hours}"),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def get_last_readings(self, role: str, limit: int = 1) -> list[dict]:
        async with self._conn.execute(
            "SELECT timestamp, temperature, humidity, battery "
            "FROM sensor_readings WHERE role = ? ORDER BY timestamp DESC LIMIT ?",
            (role, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # --- Settings ---

    async def get_all_settings(self) -> dict:
        now = time.monotonic()
        if self._settings_cache is not None and (now - self._settings_cache_ts) < _SETTINGS_CACHE_TTL:
            return dict(self._settings_cache)
        async with self._conn.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
            result = {r["key"]: json.loads(r["value"]) for r in rows}
        self._settings_cache = result
        self._settings_cache_ts = now
        return dict(result)

    async def get_setting(self, key: str, default=None):
        async with self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return json.loads(row["value"]) if row else default

    async def update_settings(self, updates: dict):
        for key, value in updates.items():
            await self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        await self._conn.commit()
        self._settings_cache = None  # Cache invalidieren

    # --- Fan events ---

    async def log_fan_event(self, speed: float, reason: str = None):
        await self._conn.execute(
            "INSERT INTO fan_events (timestamp, speed, reason) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), speed, reason),
        )
        await self._conn.commit()

    async def get_fan_events(self, hours: int = 24) -> list[dict]:
        async with self._conn.execute(
            """
            SELECT timestamp, speed, reason
            FROM fan_events
            WHERE timestamp > datetime('now', ? || ' hours')
            ORDER BY timestamp ASC
            """,
            (f"-{hours}",),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def close(self):
        if self._conn:
            await self._conn.close()
