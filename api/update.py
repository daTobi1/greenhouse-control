"""
Update API – prüft auf neue Versionen im GitHub-Repo und installiert sie.
"""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIP = PROJECT_ROOT / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "pip"

_update_state: dict = {"status": "idle", "log": ""}


def _run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=kwargs.pop("timeout", 60),
        **kwargs,
    )


@router.get("/check")
async def check_update():
    """Vergleicht den lokalen Git-Commit mit dem aktuellen Stand auf GitHub."""
    try:
        local = await asyncio.to_thread(
            _run, ["git", "rev-parse", "HEAD"], timeout=5
        )
        current = local.stdout.strip()

        remote = await asyncio.to_thread(
            _run, ["git", "ls-remote", "origin", "HEAD"], timeout=10
        )
        if remote.returncode != 0 or not remote.stdout:
            return {"error": "Keine Verbindung zum Repository", "up_to_date": None}

        latest = remote.stdout.split()[0]

        return {
            "current":          current[:7],
            "current_full":     current,
            "latest":           latest[:7],
            "latest_full":      latest,
            "up_to_date":       current == latest,
            "update_available": current != latest,
        }
    except Exception as exc:
        logger.error(f"Update check failed: {exc}")
        return {"error": str(exc), "up_to_date": None}


@router.post("/apply")
async def apply_update(background_tasks: BackgroundTasks):
    """
    Führt git pull + pip install durch und startet den systemd-Service neu.
    Antwortet sofort; das Update läuft im Hintergrund.
    """
    global _update_state
    if _update_state["status"] == "running":
        return JSONResponse({"error": "Update läuft bereits"}, status_code=409)

    _update_state = {"status": "running", "log": ""}
    background_tasks.add_task(_do_update)
    return {"status": "started"}


@router.get("/status")
async def update_status():
    """Aktueller Stand eines laufenden oder abgeschlossenen Updates."""
    return _update_state


def _do_update():
    global _update_state
    log_lines = []

    def log(msg: str):
        logger.info(msg)
        log_lines.append(msg)
        _update_state["log"] = "\n".join(log_lines)

    try:
        log("git fetch origin...")
        fetch = _run(["git", "fetch", "origin"], timeout=30)
        if fetch.returncode != 0:
            raise RuntimeError(fetch.stderr)

        log("git reset --hard origin/master...")
        pull = _run(["git", "reset", "--hard", "origin/master"], timeout=30)
        if pull.returncode != 0:
            raise RuntimeError(pull.stderr)
        log(pull.stdout.strip())

        if PIP.exists():
            log("pip install -r requirements.txt...")
            pip = _run([str(PIP), "install", "-q", "-r", "requirements.txt"], timeout=120)
            if pip.returncode != 0:
                log(f"pip Warnung: {pip.stderr[:200]}")

        log("Starte Service neu...")
        restart = subprocess.run(
            ["sudo", "systemctl", "restart", "greenhouse"],
            capture_output=True, text=True, timeout=15
        )
        if restart.returncode == 0:
            log("Service neu gestartet.")
        else:
            log(f"systemctl: {restart.stderr.strip()} (Mock-Mode oder kein systemd?)")

        _update_state["status"] = "done"
        log("Update abgeschlossen.")

    except Exception as exc:
        logger.error(f"Update failed: {exc}")
        log(f"FEHLER: {exc}")
        _update_state["status"] = "error"
