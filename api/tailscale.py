"""
Tailscale API – VPN-Status abfragen, ein-/ausschalten.
Nutzt die Tailscale CLI auf dem Raspberry Pi.
"""

import asyncio
import json
import logging
import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
logger = logging.getLogger(__name__)


async def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """Run a shell command asynchronously."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        # Read any buffered output before killing the process
        stdout = b""
        stderr = b""
        try:
            stdout = await asyncio.wait_for(proc.stdout.read(), timeout=1) if proc.stdout else b""
            stderr = await asyncio.wait_for(proc.stderr.read(), timeout=1) if proc.stderr else b""
        except (asyncio.TimeoutError, Exception):
            pass
        proc.kill()
        await proc.wait()
        return -1, stdout.decode(errors="replace"), stderr.decode(errors="replace") or "Timeout"
    except FileNotFoundError:
        return -1, "", f"Befehl nicht gefunden: {cmd[0]}"


@router.get("/status")
async def tailscale_status():
    """Current Tailscale VPN status."""
    rc, out, err = await _run(["tailscale", "status", "--json"])

    if rc != 0:
        # tailscaled not running or tailscale not installed
        if "not found" in err.lower() or rc == -1:
            return {"installed": False, "state": "NotInstalled"}
        return {"installed": True, "state": "Stopped", "ip": None,
                "hostname": None, "tailnet": None, "auth_url": None}

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {"installed": True, "state": "Unknown"}

    state = data.get("BackendState", "Unknown")
    self_node = data.get("Self", {})
    ips = self_node.get("TailscaleIPs", [])
    hostname = self_node.get("HostName", "")
    dns_suffix = data.get("MagicDNSSuffix", "")
    auth_url = data.get("AuthURL", "")

    return {
        "installed": True,
        "state": state,
        "ip": ips[0] if ips else None,
        "hostname": hostname,
        "tailnet": dns_suffix or None,
        "auth_url": auth_url or None,
    }


@router.post("/up")
async def tailscale_up():
    """Start Tailscale VPN."""
    # Run with short timeout – if login is needed, tailscale up blocks
    rc, out, err = await _run(
        ["sudo", "tailscale", "up", "--accept-routes"], timeout=15
    )

    # Check for auth URL in output (shown when login is needed)
    combined = out + err
    auth_url = None
    url_match = re.search(r'(https://login\.tailscale\.com/\S+)', combined)
    if url_match:
        auth_url = url_match.group(1)

    if auth_url:
        return {"ok": True, "auth_url": auth_url, "message": "Anmeldung erforderlich"}

    if rc == 0:
        return {"ok": True, "auth_url": None, "message": "Tailscale gestartet"}

    # Timeout is expected when waiting for auth
    if "Timeout" in err or rc == -1:
        # Check if there's an auth URL we missed
        status_rc, status_out, _ = await _run(["tailscale", "status", "--json"])
        if status_rc == 0:
            try:
                data = json.loads(status_out)
                auth_url = data.get("AuthURL", "")
                if auth_url:
                    return {"ok": True, "auth_url": auth_url,
                            "message": "Anmeldung erforderlich"}
            except json.JSONDecodeError:
                pass
        return {"ok": True, "auth_url": None,
                "message": "Tailscale wird gestartet..."}

    return JSONResponse(status_code=500, content={
        "ok": False, "error": (err or out).strip()
    })


@router.post("/down")
async def tailscale_down():
    """Stop Tailscale VPN."""
    rc, out, err = await _run(["sudo", "tailscale", "down"])
    if rc != 0:
        return JSONResponse(status_code=500, content={
            "ok": False, "error": (err or out).strip()
        })
    return {"ok": True, "message": "Tailscale gestoppt"}


@router.post("/reauth")
async def tailscale_reauth():
    """Force re-authentication (logout + up --force-reauth)."""
    # Logout first to clear old credentials
    await _run(["sudo", "tailscale", "logout"], timeout=10)

    # Start tailscale up in background – it blocks until auth completes,
    # but writes the auth URL to stderr quickly
    rc, out, err = await _run(
        ["sudo", "tailscale", "up", "--accept-routes", "--force-reauth"],
        timeout=10,
    )

    # Check command output for auth URL
    combined = out + err
    auth_url = None
    url_match = re.search(r'(https://login\.tailscale\.com/\S+)', combined)
    if url_match:
        auth_url = url_match.group(1)

    if auth_url:
        return {"ok": True, "auth_url": auth_url, "message": "Anmeldung erforderlich"}

    # Auth URL not in command output – poll status a few times
    # (tailscaled needs a moment to generate the URL after logout)
    for _ in range(4):
        await asyncio.sleep(2)
        status_rc, status_out, _ = await _run(["tailscale", "status", "--json"])
        if status_rc == 0:
            try:
                data = json.loads(status_out)
                auth_url = data.get("AuthURL", "")
                if auth_url:
                    return {"ok": True, "auth_url": auth_url,
                            "message": "Anmeldung erforderlich"}
            except json.JSONDecodeError:
                pass

    return {"ok": True, "auth_url": None,
            "message": "Kein Anmelde-Link erhalten. Versuche den Pi neu zu starten."}
