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
    # Bonsai-Schutzhaus fuer heimische Arten: keine Zieltemperatur zum
    # Heizen, sondern die Grenze, ab der gegen Hitzestau gelueftet wird.
    "target_temperature": 22.0,
    "target_humidity": 65.0,
    "control_mode": "combined_or",  # temperature | humidity | combined_or | combined_and
    "fan_gpio_pin": 18,
    "fan_min_speed": 0.2,
    "fan_max_speed": 1.0,
    "temp_control_range": 4.0,       # volle Drehzahl 4 K ueber dem Ziel –
                                     # ein kleines Haus heizt schnell auf
    "humidity_control_range": 20.0,  # full speed at +20% above target
    "fan_update_interval": 10,        # seconds
    "ble_scan_interval": 30,          # seconds
    "ble_scan_duration": 10,          # seconds per scan
    "timelapse_active": False,
    "timelapse_interval": 300,        # seconds between frames
    "timelapse_fps": 25,
    "timelapse_deflicker": True,      # Helligkeitssprünge beim Compilen ausgleichen
    "camera_index": 0,
    "fan_deadband": 0.1,             # hysteresis: min raw speed (0..1) to start fan
    "fan_manual_override": False,
    "fan_manual_speed": 0.0,
    "fan_min_temperature": 2.0,   # Frostschutz. Die Wurzeln im flachen Topf
                                  # sind weit empfindlicher als der Stamm.
    "humidity_abs_margin": 0.5,   # g/m³ Mindestunterschied für Feuchtelüftung
    # Weit gefasst, weil Auskuehlen im Winterhaus erwuenscht ist: entfeuchtet
    # wird bis 2 °C, dort uebernimmt der Frostschutz. Mit einem engen Abstand
    # liefe die Entfeuchtung genau dann nie, wenn Dauernaesse gefaehrlich wird.
    "humidity_temp_guard": 20.0,  # K unter Ziel, ab da keine Feuchtelüftung
    "humidity_metric": "vpd",     # relative | vpd – wonach die Feuchte geregelt wird
    "target_vpd": 0.80,           # kPa Dampfdruckdefizit, Ziel im Modus "vpd" (Bonsai)
    "vpd_control_range": 0.40,    # kPa unter dem Ziel bis zur vollen Drehzahl
    "fan_start_threshold": 0.10,  # Rohwert (0..1), ab dem der Lüfter anläuft
    "fan_stop_threshold": 0.03,   # Rohwert, unter dem er wieder abschaltet
    "fan_min_runtime": 120,       # Sekunden Mindestlaufzeit
    "fan_min_pause": 60,          # Sekunden Mindestpause
    "fan_kickstart_duration": 0.6,  # Sekunden 100-%-Anlaufpuls, 0 = aus
    "sensor_max_age": 300,   # Sekunden, ab wann Messwerte als veraltet gelten
    "settings_migrated_thresholds": False,
    "update_check_interval_days": 7,  # 0 = deaktiviert
    "timelapse_path": "timelapse",
    "timelapse_share_enabled": False,
    "camera_capture_width": 0,   # 0 = camera default
    "camera_capture_height": 0,
    "capture_mode": "still",     # still | clip
    "clip_duration": 5,          # seconds per clip
    "clip_fps": 10,              # fps of recorded clip
    "regulation_enabled": True,  # central on/off for fan regulation
    "camera_count": 1,           # number of camera slots (1-4)
    # Zeitplan der Kamera 0. Kameras 1–3 bekommen bewusst keine Defaults:
    # parse_config liefert dort dieselben Werte über seine eigenen Vorgaben.
    "cam_0_schedule_mode": "interval",   # interval | times
    "cam_0_schedule_start": None,        # "HH:MM" – ab wann das Intervall läuft
    "cam_0_schedule_end": None,          # "HH:MM" – optionales Fensterende
    "cam_0_schedule_times": [],          # ["08:00", "12:00"] im Modus "times"
    "cam_0_schedule_grace": 300,         # Kulanzfenster in Sekunden
    "cam_0_date_from": None,             # "YYYY-MM-DD" – Beginn des Zeitraums
    "cam_0_date_to": None,               # "YYYY-MM-DD" – Ende, einschließlich
    "cam_0_oneshots": [],                # ["2026-04-01 08:00"] – Einzeltermine
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

        # Einmalige Übernahme: wer fan_deadband angepasst hatte, behält den
        # Wert als neue Einschaltschwelle.
        migrated = await self.get_setting("settings_migrated_thresholds", False)
        if not migrated:
            deadband = await self.get_setting("fan_deadband", 0.1)
            if deadband is not None and abs(float(deadband) - 0.1) > 1e-9:
                await self._conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'fan_start_threshold'",
                    (json.dumps(float(deadband)),),
                )
                logger.info(f"fan_deadband {deadband} als fan_start_threshold übernommen")
            await self._conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("settings_migrated_thresholds", json.dumps(True)),
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

    async def get_readings_range(self, role: str, from_ts: str, to_ts: str) -> list[dict]:
        async with self._conn.execute(
            """
            SELECT timestamp, temperature, humidity, battery
            FROM sensor_readings
            WHERE role = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (role, from_ts, to_ts),
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

    async def get_fan_events_range(self, from_ts: str, to_ts: str) -> list[dict]:
        async with self._conn.execute(
            """
            SELECT timestamp, speed, reason
            FROM fan_events
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (from_ts, to_ts),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def close(self):
        if self._conn:
            await self._conn.close()
