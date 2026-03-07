# Greenhouse Control

Raspberry Pi basierte Gewächshaus-Steuerung mit Web-Dashboard, automatischer Lüfterregelung über MOSFET-PWM, SwitchBot BLE-Sensoren und Timelapse-Kamera.

---

## Features

- **Web-Dashboard** – erreichbar im lokalen Netzwerk oder per VPN (Tailscale, WireGuard)
- **Lüfterregelung** – proportionale Regelung via PWM/MOSFET, Abluft-Prinzip
- **SwitchBot Integration** – direkte Bluetooth-Verbindung, kein Cloud-API nötig
- **Zwei Sensoren** – frei wählbar welcher Innen-/Außensensor ist
- **Regelungsarten** – Temperatur, Feuchtigkeit oder kombiniert
- **Timelapse** – USB-Kamera, konfigurierbares Intervall, automatische Kameraerkennung, ffmpeg-Kompilierung mit Download
- **Verlaufsdiagramme** – Temperatur, Feuchtigkeit, Lüfterdrehzahl – jeweils mit unabhängiger Zeitbereichswahl
- **Trend-Indikatoren** – zeigen ob Werte steigen, fallen oder stabil sind

---

## Hardware

| Komponente | Beschreibung |
|---|---|
| Raspberry Pi | 3B+ / 4 / 5 (Raspberry Pi OS Bookworm/Bullseye) |
| SwitchBot IP65 | Hygro-Thermometer (2x) – Bluetooth LE |
| MOSFET | z.B. IRLZ44N – PWM-Ansteuerung des Lüfters |
| Lüfter | 1x (12V oder 5V, je nach Schaltung) |
| USB-Kamera | beliebige UVC-kompatible Webcam |

### Schaltung Lüfter (MOSFET)

```
GPIO18 (BCM) ──[1kΩ]── Gate (MOSFET)
GND          ──────── Source (MOSFET)
                       Drain ── Lüfter (–)
                       Lüfter (+) ── 12V
                       12V GND ── Pi GND
```

GPIO-Pin ist im Dashboard konfigurierbar.

---

## Installation

```bash
# 1. Repo klonen
git clone https://github.com/daTobi1/greenhouse-control.git
cd greenhouse-control

# 2. Installationsscript ausführen
bash install.sh
```

Das Script installiert alle Abhängigkeiten, richtet den systemd-Service ein und startet ihn automatisch beim Booten.

```bash
# Service manuell starten
sudo systemctl start greenhouse

# Status prüfen
sudo systemctl status greenhouse

# Logs verfolgen
sudo journalctl -u greenhouse -f
```

Dashboard aufrufen:
```
http://<Pi-IP-Adresse>:8080
```

---

## Manueller Start (Entwicklung)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Auf Windows/Mac läuft die App automatisch im **Mock-Mode** – GPIO und Kamera werden simuliert, alle API-Endpunkte sind voll funktionsfähig.

---

## Projektstruktur

```
greenhouse-control/
├── main.py                  # FastAPI App + Lifespan
├── state.py                 # Globale Service-Instanzen
├── requirements.txt
├── install.sh               # Automatisches Setup für Raspberry Pi
├── greenhouse.service       # systemd Unit-File
│
├── db/
│   └── database.py          # SQLite (Sensordaten, Einstellungen, Lüfter-Events)
│
├── services/
│   ├── switchbot.py         # BLE-Scanner + SwitchBot-Protokoll-Parser
│   ├── fan_controller.py    # PWM via RPi.GPIO (Mock-Mode wenn kein Pi)
│   ├── camera.py            # Frame-Capture (OpenCV) + ffmpeg-Kompilierung
│   └── scheduler.py         # Asyncio-Tasks: BLE, Lüfter, Timelapse, Logging
│
├── api/
│   ├── sensors.py           # GET /current, /history, POST /discover
│   ├── fans.py              # GET /status, POST /manual, /auto
│   ├── timelapse.py         # start/stop/compile/preview/cameras/sessions
│   └── settings.py          # GET/PUT alle Einstellungen
│
└── static/
    ├── index.html           # Single-Page Dashboard
    ├── css/style.css        # Dark-Theme, responsives Grid
    └── js/app.js            # Polling (10s), Chart.js, SVG-Gauge
```

---

## API-Übersicht

| Methode | Endpunkt | Beschreibung |
|---|---|---|
| GET | `/api/sensors/current` | Aktuelle Sensor-Werte (innen + außen) |
| GET | `/api/sensors/history?hours=24` | Verlaufsdaten |
| POST | `/api/sensors/discover` | SwitchBot-Geräte in der Nähe suchen |
| GET | `/api/fans/status` | Lüfter-Status und Drehzahl |
| POST | `/api/fans/manual` | Manuelle Drehzahl setzen `{"speed": 0.75}` |
| POST | `/api/fans/auto` | Zurück in Automatik |
| GET | `/api/settings` | Alle Einstellungen lesen |
| PUT | `/api/settings` | Einstellungen aktualisieren |
| GET | `/api/timelapse/cameras` | Verfügbare Kameras erkennen |
| POST | `/api/timelapse/start` | Timelapse-Session starten |
| POST | `/api/timelapse/stop` | Session stoppen |
| POST | `/api/timelapse/compile/{session}` | Video aus Frames kompilieren |
| GET | `/api/timelapse/preview` | Live-Vorschau (JPEG) |

Interaktive API-Dokumentation: `http://<Pi-IP>:8080/docs`

---

## Einstellungen

Alle Einstellungen werden im Dashboard unter dem Zahnrad-Symbol konfiguriert und in der SQLite-Datenbank gespeichert.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `inside_sensor_mac` | – | MAC-Adresse des Innen-Sensors |
| `outside_sensor_mac` | – | MAC-Adresse des Außen-Sensors |
| `target_temperature` | 25.0 °C | Ziel-Temperatur |
| `target_humidity` | 65 % | Ziel-Feuchtigkeit |
| `control_mode` | combined | `temperature` / `humidity` / `combined` |
| `fan_gpio_pin` | 18 | GPIO-Pin (BCM) für PWM |
| `fan_min_speed` | 0.2 | Mindest-Drehzahl wenn Lüfter läuft (0–1) |
| `fan_max_speed` | 1.0 | Maximale Drehzahl (0–1) |
| `temp_control_range` | 5.0 °C | Temperaturdifferenz für volle Drehzahl |
| `humidity_control_range` | 20 % | Feuchtigkeitsdifferenz für volle Drehzahl |
| `ble_scan_interval` | 30 s | Pause zwischen BLE-Scans |
| `timelapse_interval` | 3600 s | Abstand zwischen Timelapse-Frames |
| `timelapse_fps` | 25 | Bildrate des kompilierten Videos |

---

## Sensor-Einrichtung

1. Dashboard öffnen → Zahnrad-Symbol
2. **"Sensoren suchen"** klicken (10-Sekunden BLE-Scan)
3. Gefundene Geräte erscheinen mit MAC-Adresse und Signalstärke
4. Per **"Innen"** / **"Außen"** Button die Sensoren zuweisen
5. Speichern – Daten erscheinen nach dem nächsten BLE-Scan (max. 30s)

---

## Regelungslogik

Der Lüfter arbeitet im **Abluft-Prinzip** (schiebt Luft aus dem Gewächshaus):

- Lüfter läuft **nur**, wenn die Außenluft die Innenluft verbessern würde
  - Temperatur: Außen kühler als innen **und** innen über Zieltemperatur
  - Feuchtigkeit: Außen trockener als innen **und** innen über Ziel-Feuchtigkeit
- Drehzahl skaliert proportional zwischen `fan_min` und `fan_max`
- Unter Mindest-Drehzahl wird der Lüfter komplett ausgeschaltet

---

## Abhängigkeiten

| Paket | Zweck |
|---|---|
| `fastapi` + `uvicorn` | Web-Framework + ASGI-Server |
| `aiosqlite` | Async SQLite |
| `bleak` | Bluetooth LE (SwitchBot) |
| `RPi.GPIO` | GPIO-PWM (nur Raspberry Pi) |
| `opencv-python-headless` | Kamera-Zugriff |
| `ffmpeg` | Timelapse-Kompilierung (System-Paket) |

---

## Lizenz

MIT
