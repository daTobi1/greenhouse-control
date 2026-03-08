"""
WiFi API – WLAN-Netzwerke scannen, verbinden und Status abfragen.
Nutzt nmcli (NetworkManager) auf dem Raspberry Pi.
"""

import asyncio
import logging
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectRequest(BaseModel):
    ssid: str
    password: str = ""


async def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """Führt einen Shell-Befehl asynchron aus."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "Timeout"
    except FileNotFoundError:
        return -1, "", f"Befehl nicht gefunden: {cmd[0]}"


def _parse_nmcli_line(line: str) -> list[str]:
    """Parse eine nmcli -t Zeile korrekt (escaped colons \\: beachten)."""
    parts = []
    current = []
    i = 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line) and line[i + 1] == ':':
            current.append(':')
            i += 2
        elif line[i] == ':':
            parts.append(''.join(current))
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1
    parts.append(''.join(current))
    return parts


@router.get("/status")
async def wifi_status():
    """Aktueller WLAN-Verbindungsstatus."""
    # Prüfe ob WLAN-Radio an/aus ist
    wifi_enabled = True
    rc_radio, out_radio, _ = await _run(["nmcli", "radio", "wifi"])
    if rc_radio == 0:
        wifi_enabled = out_radio.strip().lower() == "enabled"

    rc, out, err = await _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,FREQ,SECURITY", "dev", "wifi"])

    if rc != 0:
        # Fallback: versuche iwconfig
        rc2, out2, _ = await _run(["iwconfig", "wlan0"])
        if rc2 == 0:
            ssid_match = re.search(r'ESSID:"([^"]*)"', out2)
            return {
                "connected": bool(ssid_match and ssid_match.group(1)),
                "ssid": ssid_match.group(1) if ssid_match else None,
                "signal": None,
                "frequency": None,
                "security": None,
                "ip": await _get_ip(),
                "wifi_enabled": wifi_enabled,
                "mock_mode": False,
            }
        return {
            "connected": False,
            "ssid": None,
            "signal": None,
            "frequency": None,
            "security": None,
            "ip": None,
            "wifi_enabled": wifi_enabled,
            "mock_mode": True,
        }

    # Suche aktive Verbindung
    for line in out.strip().splitlines():
        parts = _parse_nmcli_line(line)
        if len(parts) >= 5 and parts[0].strip().lower() in ("ja", "yes"):
            return {
                "connected": True,
                "ssid": parts[1],
                "signal": int(parts[2]) if parts[2].isdigit() else None,
                "frequency": parts[3],
                "security": parts[4],
                "ip": await _get_ip(),
                "wifi_enabled": wifi_enabled,
                "mock_mode": False,
            }

    return {
        "connected": False,
        "ssid": None,
        "signal": None,
        "frequency": None,
        "security": None,
        "ip": await _get_ip(),
        "wifi_enabled": wifi_enabled,
        "mock_mode": False,
    }


async def _get_ip() -> str | None:
    """Holt die aktuelle IP-Adresse des WLAN-Interfaces."""
    rc, out, _ = await _run(["hostname", "-I"])
    if rc == 0 and out.strip():
        return out.strip().split()[0]
    return None


@router.get("/scan")
async def wifi_scan():
    """Scannt nach verfügbaren WLAN-Netzwerken (nutzt den letzten Scan-Cache)."""
    # Rescan nur auslösen wenn explizit gewünscht – kein rescan hier,
    # da das auf dem aktiven Interface die Verbindung stören kann.
    # nmcli listet den letzten bekannten Scan.
    rc, out, err = await _run([
        "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "dev", "wifi", "list",
    ])

    if rc != 0:
        return JSONResponse(
            status_code=503,
            content={"error": "WLAN-Scan fehlgeschlagen", "detail": err.strip()},
        )

    networks = []
    seen_ssids = set()
    for line in out.strip().splitlines():
        parts = _parse_nmcli_line(line)
        if len(parts) < 4:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid in seen_ssids:
            continue
        seen_ssids.add(ssid)
        signal = int(parts[1]) if parts[1].isdigit() else 0
        security = parts[2].strip()
        freq = parts[3].strip()
        networks.append({
            "ssid": ssid,
            "signal": signal,
            "security": security,
            "frequency": freq,
            "secured": security != "" and security != "--",
        })

    # Nach Signalstärke sortieren (stärkstes zuerst)
    networks.sort(key=lambda n: n["signal"], reverse=True)
    return {"networks": networks}


@router.post("/rescan")
async def wifi_rescan():
    """Löst einen aktiven WLAN-Rescan aus (kann kurz die Verbindung stören)."""
    rc, _, err = await _run(["nmcli", "dev", "wifi", "rescan"], timeout=10)
    if rc != 0:
        return JSONResponse(
            status_code=503,
            content={"error": "Rescan fehlgeschlagen", "detail": err.strip()},
        )
    await asyncio.sleep(3)
    return {"status": "ok"}


@router.post("/connect")
async def wifi_connect(req: ConnectRequest):
    """Verbindet mit einem WLAN-Netzwerk."""
    ssid = req.ssid.strip()
    password = req.password

    if not ssid:
        return JSONResponse(status_code=400, content={"error": "SSID darf nicht leer sein"})

    logger.info("WLAN: Verbinde mit '%s'", ssid)

    # Prüfe ob bereits ein Profil für diese SSID existiert
    rc_check, out_check, _ = await _run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    existing = ssid in out_check.splitlines() if rc_check == 0 else False

    if existing:
        # Bestehendes Profil löschen und neu anlegen (Passwort könnte sich geändert haben)
        await _run(["nmcli", "connection", "delete", ssid])

    # Verbindung herstellen
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]

    rc, out, err = await _run(cmd, timeout=30)

    if rc != 0:
        error_msg = err.strip() or out.strip()
        # Typische Fehlermeldungen übersetzen
        if "secrets were required" in error_msg.lower() or "no suitable" in error_msg.lower():
            error_msg = "Falsches Passwort oder Netzwerk nicht erreichbar"
        elif "no network" in error_msg.lower():
            error_msg = "Netzwerk nicht gefunden"
        logger.warning("WLAN-Verbindung fehlgeschlagen: %s", error_msg)
        return JSONResponse(
            status_code=400,
            content={"error": "Verbindung fehlgeschlagen", "detail": error_msg},
        )

    logger.info("WLAN: Erfolgreich verbunden mit '%s'", ssid)
    # Kurz warten bis IP zugewiesen
    await asyncio.sleep(2)
    ip = await _get_ip()
    return {"connected": True, "ssid": ssid, "ip": ip}


@router.post("/disconnect")
async def wifi_disconnect():
    """Trennt die aktuelle WLAN-Verbindung."""
    rc, out, err = await _run(["nmcli", "dev", "disconnect", "wlan0"])
    if rc != 0:
        return JSONResponse(
            status_code=400,
            content={"error": "Trennen fehlgeschlagen", "detail": err.strip()},
        )
    return {"connected": False}


@router.post("/radio")
async def wifi_radio(enabled: bool = True):
    """WLAN-Adapter ein- oder ausschalten."""
    state = "on" if enabled else "off"
    rc, _, err = await _run(["nmcli", "radio", "wifi", state])
    if rc != 0:
        return JSONResponse(
            status_code=400,
            content={"error": f"WLAN {state} fehlgeschlagen", "detail": err.strip()},
        )
    return {"wifi_enabled": enabled}
