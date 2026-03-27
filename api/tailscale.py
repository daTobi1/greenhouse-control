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

# Keep the reauth process alive globally so the auth URL stays valid
_reauth_proc: asyncio.subprocess.Process | None = None


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


@router.get("/debug")
async def tailscale_debug():
    """Debug info: find state files, check daemon, show status."""
    log = []

    # Find all tailscale state files
    rc, out, _ = await _run(["sudo", "find", "/var/lib", "/etc", "/root",
                              "-name", "*tailscale*", "-type", "f"], timeout=10)
    log.append(f"state files:\n{out.strip() or '(none)'}")

    # Check tailscaled service
    rc, out, _ = await _run(["systemctl", "is-active", "tailscaled"], timeout=5)
    log.append(f"tailscaled active: {out.strip()}")

    # Tailscale status
    rc, out, _ = await _run(["tailscale", "status", "--json"], timeout=10)
    if rc == 0:
        try:
            data = json.loads(out)
            log.append(f"BackendState: {data.get('BackendState')}")
            log.append(f"AuthURL: {data.get('AuthURL', '(none)')}")
            self_node = data.get("Self", {})
            log.append(f"NodeKey: {self_node.get('PublicKey', '(none)')}")
        except json.JSONDecodeError:
            log.append(f"status parse error: {out[:200]}")
    else:
        log.append(f"status failed (rc={rc})")

    return {"log": "\n".join(log)}


@router.post("/reauth")
async def tailscale_reauth():
    """Full Tailscale state reset + re-authentication."""
    global _reauth_proc
    log = []

    # Kill any previous reauth process
    if _reauth_proc and _reauth_proc.returncode is None:
        try:
            _reauth_proc.kill()
            await _reauth_proc.wait()
        except Exception:
            pass

    # Step 0: Kill ALL orphaned tailscale up processes from previous attempts
    rc, out, err = await _run(["sudo", "pkill", "-f", "tailscale up"], timeout=5)
    log.append(f"pkill tailscale up: rc={rc}")
    logger.info(f"pkill tailscale up: rc={rc}")

    # Step 1: Stop tailscaled daemon FIRST (so logout doesn't hang)
    rc, out, err = await _run(["sudo", "systemctl", "stop", "tailscaled"], timeout=10)
    log.append(f"stop daemon: rc={rc}")
    logger.info(f"stop tailscaled: rc={rc} err={err.strip()!r}")

    # Step 2: Wipe ALL tailscale state
    rc, out, err = await _run(
        ["sudo", "sh", "-c", "rm -rf /var/lib/tailscale && mkdir -p /var/lib/tailscale"],
        timeout=5,
    )
    log.append(f"wipe state dir: rc={rc}")
    logger.info(f"wipe /var/lib/tailscale: rc={rc} err={err.strip()!r}")

    # Step 3: Restart tailscaled daemon (completely fresh, no old keys in memory)
    rc, out, err = await _run(["sudo", "systemctl", "start", "tailscaled"], timeout=15)
    log.append(f"start daemon: rc={rc}")
    logger.info(f"start tailscaled: rc={rc} err={err.strip()!r}")

    # Step 4: Wait for daemon to be ready
    await asyncio.sleep(3)

    # Step 5: Verify clean state before starting auth
    status_rc, status_out, _ = await _run(["tailscale", "status", "--json"])
    if status_rc == 0:
        try:
            data = json.loads(status_out)
            node_key = data.get("Self", {}).get("PublicKey", "")
            log.append(f"fresh NodeKey: {node_key[:20]}...")
            logger.info(f"fresh state: BackendState={data.get('BackendState')} NodeKey={node_key}")
        except json.JSONDecodeError:
            pass

    # Step 6: Start tailscale up as persistent background process
    _reauth_proc = await asyncio.create_subprocess_exec(
        "sudo", "tailscale", "up", "--accept-routes",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    log.append(f"tailscale up pid={_reauth_proc.pid}")
    logger.info(f"tailscale up started (pid={_reauth_proc.pid})")

    # Step 7: Poll status for auth URL
    for attempt in range(8):
        await asyncio.sleep(2)
        status_rc, status_out, _ = await _run(["tailscale", "status", "--json"])
        if status_rc == 0:
            try:
                data = json.loads(status_out)
                state = data.get("BackendState", "")
                auth_url = data.get("AuthURL", "")
                log.append(f"poll {attempt+1}: state={state} url={'yes' if auth_url else 'no'}")
                logger.info(f"reauth poll {attempt+1}: state={state} auth_url={auth_url!r}")
                if auth_url:
                    return {"ok": True, "auth_url": auth_url,
                            "message": "Anmeldung erforderlich", "debug": "\n".join(log)}
            except json.JSONDecodeError:
                pass

    return {"ok": False, "auth_url": None,
            "message": "Kein Anmelde-Link erhalten.",
            "debug": "\n".join(log)}


@router.post("/authkey")
async def tailscale_authkey(body: dict):
    """Register with a pre-auth key (bypasses interactive login)."""
    key = body.get("key", "").strip()
    if not key:
        return JSONResponse(status_code=400, content={"ok": False, "error": "Kein Key angegeben"})

    log = []

    # Kill everything, wipe state, fresh start
    await _run(["sudo", "pkill", "-f", "tailscale up"], timeout=5)
    await _run(["sudo", "systemctl", "stop", "tailscaled"], timeout=10)
    await _run(["sudo", "sh", "-c", "rm -rf /var/lib/tailscale && mkdir -p /var/lib/tailscale"], timeout=5)

    rc, _, err = await _run(["sudo", "systemctl", "start", "tailscaled"], timeout=15)
    log.append(f"start daemon: rc={rc}")
    if rc != 0:
        return {"ok": False, "error": f"tailscaled start failed: {err.strip()}", "debug": "\n".join(log)}

    await asyncio.sleep(3)

    # Register with auth key – no interactive login needed
    rc, out, err = await _run(
        ["sudo", "tailscale", "up", "--accept-routes", "--authkey", key],
        timeout=30,
    )
    log.append(f"tailscale up --authkey: rc={rc} out={out.strip()} err={err.strip()}")
    logger.info(f"tailscale authkey: rc={rc} out={out.strip()!r} err={err.strip()!r}")

    if rc == 0:
        # Verify connection
        await asyncio.sleep(2)
        status = await tailscale_status()
        return {"ok": True, "message": "Tailscale verbunden!", "status": status, "debug": "\n".join(log)}

    return {"ok": False, "error": (err or out).strip(), "debug": "\n".join(log)}
