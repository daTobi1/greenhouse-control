import json
import logging
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "inside_sensor_mac": "",
    "outside_sensor_mac": "",
    "target_temperature": 25.0,
    "target_humidity": 65.0,
    "control_mode": "combined",  # temperature | humidity | combined
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
    "fan_manual_override": False,
    "fan_manual_speed": 0.0,
    "update_check_interval_days": 7,  # 0 = deaktiviert
}


class Database:
    def __init__(self, path: str):
        self._path = path
        self._conn: aiosqlite.Connection | None = None

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
        async with self._conn.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
            return {r["key"]: json.loads(r["value"]) for r in rows}

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
