# Greenhouse Control

Raspberry Pi basierte Gewächshaus-Steuerung mit Web-Dashboard, automatischer Lüfterregelung über MOSFET-PWM, SwitchBot BLE-Sensoren und Timelapse-Kamera.

---

## Schnellstart

### Installation

```bash
curl -fsSL https://raw.githubusercontent.com/daTobi1/greenhouse-control/master/install.sh | bash
```

Das Script erkennt automatisch ob es auf einem Raspberry Pi läuft, installiert alle Abhängigkeiten, richtet einen systemd-Service ein und startet das Dashboard.

Dashboard aufrufen:
```
http://<Pi-IP-Adresse>```

### Deinstallation

```bash
curl -fsSL https://raw.githubusercontent.com/daTobi1/greenhouse-control/master/uninstall.sh | bash
```

Fragt vor dem Löschen des Verzeichnisses (inkl. Datenbank und Timelapse-Aufnahmen) nochmals nach.

---

## Features

- **Web-Dashboard** – erreichbar im lokalen Netzwerk oder per VPN (Tailscale, WireGuard)
- **PWA / Handy-App** – als App auf dem Homescreen installierbar (Android + iOS), Vollbild, offline-fähig
- **Tailscale VPN** – Ein-Klick-Einrichtung im Dashboard für sicheren Fernzugriff über das Internet
- **Lüfterregelung** – proportionale PWM-Regelung via MOSFET, Abluft-Prinzip
- **SwitchBot Integration** – direkte Bluetooth-Verbindung, kein Cloud-API nötig (Meter, Meter Plus, Outdoor Meter / WoIOSensor)
- **Zwei Sensoren** – frei konfigurierbar welcher innen/außen ist
- **Regelungsarten** – Temperatur, Feuchtigkeit oder kombiniert
- **Trend-Indikatoren** – zeigen ob Werte steigen, fallen oder stabil sind
- **Verlaufsdiagramme** – Temperatur, Feuchtigkeit, Lüfterdrehzahl mit unabhängiger Zeitbereichswahl
- **Timelapse** – USB-Kamera mit automatischer Erkennung, konfigurierbares Intervall (Stunden), ffmpeg-Kompilierung und Download
- **Software-Update** – Update-Button im Dashboard prüft auf neue Versionen und installiert nach Bestätigung

---

## Hardware

| Komponente | Beschreibung |
|---|---|
| Raspberry Pi | 3B+ / 4 / 5 (Raspberry Pi OS Bookworm/Bullseye) |
| SwitchBot IP65 | Hygro-Thermometer (2×) – Bluetooth LE |
| MOSFET | z.B. IRLZ44N – PWM-Ansteuerung des Lüfters |
| Lüfter | 1× (12 V oder 5 V je nach Schaltung) |
| USB-Kamera | beliebige UVC-kompatible Webcam |

### Schaltung Lüfter (MOSFET)

```
GPIO18 (BCM) ──[1 kΩ]── Gate (MOSFET)
GND          ─────────── Source
                          Drain ── Lüfter (–)
                          Lüfter (+) ── 12 V
                          12 V GND ── Pi GND
```

GPIO-Pin ist im Dashboard konfigurierbar (Standard: GPIO18).

---

## Manuelle Installation / Entwicklung

```bash
git clone https://github.com/daTobi1/greenhouse-control.git
cd greenhouse-control

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

uvicorn main:app --host 0.0.0.0 --port 80 --reload
```

Auf Windows/Mac läuft die App im **Mock-Mode** – GPIO und Kamera werden simuliert, alle API-Endpunkte sind voll funktionsfähig.

---

## Projektstruktur

```
greenhouse-control/
├── main.py                  # FastAPI App + Lifespan
├── state.py                 # Globale Service-Instanzen
├── requirements.txt
├── install.sh               # Installations-Script (curl-kompatibel)
├── uninstall.sh             # Deinstallations-Script
├── greenhouse.service       # systemd Unit-File (Referenz)
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
│   ├── settings.py          # GET/PUT alle Einstellungen
│   ├── update.py            # GET /check, POST /apply, GET /status
│   └── tailscale.py         # GET /status, POST /up, /down (VPN-Steuerung)
│
└── static/
    ├── index.html           # Single-Page Dashboard (PWA)
    ├── manifest.json        # PWA-Manifest (App-Name, Icons, Theme)
    ├── sw.js                # Service Worker (Offline-Cache)
    ├── icon-*.svg           # App-Icons (192, 512, maskable)
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
| POST | `/api/fans/manual` | Manuelle Drehzahl `{"speed": 0.75}` |
| POST | `/api/fans/auto` | Zurück in Automatik |
| GET | `/api/settings` | Alle Einstellungen lesen |
| PUT | `/api/settings` | Einstellungen aktualisieren |
| GET | `/api/timelapse/cameras` | Verfügbare Kameras erkennen |
| POST | `/api/timelapse/start` | Timelapse-Session starten |
| POST | `/api/timelapse/stop` | Session stoppen |
| POST | `/api/timelapse/compile/{session}` | Video kompilieren |
| GET | `/api/timelapse/preview` | Live-Vorschau (JPEG) |
| GET | `/api/update/check` | Auf neue Version prüfen |
| POST | `/api/update/apply` | Update installieren (Hintergrund) |
| GET | `/api/update/status` | Status eines laufenden Updates |
| GET | `/api/tailscale/status` | Tailscale VPN-Status (IP, Hostname, Tailnet) |
| POST | `/api/tailscale/up` | Tailscale einschalten |
| POST | `/api/tailscale/down` | Tailscale ausschalten |

Interaktive Dokumentation: `http://<Pi-IP>:8080/docs`

---

## Einstellungen

Alle Einstellungen über das Zahnrad-Symbol im Dashboard.

| Einstellung | Standard | Beschreibung |
|---|---|---|
| `inside_sensor_mac` | – | MAC-Adresse Innen-Sensor |
| `outside_sensor_mac` | – | MAC-Adresse Außen-Sensor |
| `target_temperature` | 25.0 °C | Ziel-Temperatur |
| `target_humidity` | 65 % | Ziel-Feuchtigkeit |
| `control_mode` | combined_or | `temperature` / `humidity` / `combined_or` / `combined_and` |
| `fan_gpio_pin` | 18 | GPIO-Pin (BCM) |
| `fan_min_speed` | 0.2 | Mindest-Drehzahl (0–1) |
| `fan_max_speed` | 1.0 | Maximale Drehzahl (0–1) |
| `temp_control_range` | 5.0 °C | Temperaturdifferenz für volle Drehzahl |
| `humidity_control_range` | 20 % | Feuchtigkeitsdifferenz für volle Drehzahl |
| `fan_deadband` | 0.1 | Totzone / Hysterese (0–1) |
| `fan_min_temperature` | 5.0 °C | Frostschutz: Lüfter aus unter diesem Wert |
| `ble_scan_interval` | 30 s | Pause zwischen BLE-Scans |
| `timelapse_interval` | 3600 s | Abstand zwischen Timelapse-Frames |
| `timelapse_fps` | 25 | Bildrate des kompilierten Videos |

---

## Sensor-Einrichtung

1. Dashboard öffnen → Zahnrad-Symbol
2. **"Sensoren suchen"** klicken (10-Sekunden BLE-Scan)
3. Gefundene Geräte mit MAC-Adresse und Signalstärke werden angezeigt
4. Per **"Innen"** / **"Außen"** die Sensoren zuweisen
5. Speichern – Daten erscheinen nach dem nächsten BLE-Scan (max. 30 s)

---

## Regelungslogik

Der Lüfter arbeitet im **Abluft-Prinzip** (schiebt Luft aus dem Gewächshaus heraus):

- Lüfter läuft **nur**, wenn die Außenluft die Innenluft tatsächlich verbessern würde
  - Temperatur: Außen kühler als innen **und** innen über Zieltemperatur
  - Feuchtigkeit: Außen trockener als innen **und** innen über Ziel-Feuchtigkeit
- Drehzahl skaliert proportional zwischen `fan_min` und `fan_max`
- Unter Mindest-Drehzahl wird der Lüfter komplett ausgeschaltet
- **Frostschutz**: Lüfter wird blockiert wenn die Innentemperatur unter `fan_min_temperature` fällt (Standard: 5 °C)

---

## Software-Update

Im Dashboard erscheint oben rechts ein **Update-Button** (↑) sobald eine neue Version verfügbar ist. Ein Klick zeigt die aktuell installierte und verfügbare Version. Nach Bestätigung wird automatisch:

1. `git pull` ausgeführt
2. Python-Abhängigkeiten aktualisiert
3. Der systemd-Service neu gestartet
4. Das Dashboard neu geladen

---

## Service-Befehle

```bash
sudo systemctl start   greenhouse   # Starten
sudo systemctl stop    greenhouse   # Stoppen
sudo systemctl restart greenhouse   # Neu starten
sudo systemctl status  greenhouse   # Status
sudo journalctl -u greenhouse -f    # Logs verfolgen
```

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
