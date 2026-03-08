"""
Update API – prüft auf neue Versionen im GitHub-Repo und installiert sie.
"""

import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIP = PROJECT_ROOT / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "pip"

_update_state: dict = {"status": "idle", "log": ""}


class RollbackRequest(BaseModel):
    commit: str


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
    """Führt git pull + pip install durch und startet den systemd-Service neu."""
    global _update_state
    if _update_state["status"] == "running":
        return JSONResponse({"error": "Update läuft bereits"}, status_code=409)
    _update_state = {"status": "running", "log": ""}
    background_tasks.add_task(_apply, "origin/master", fetch_first=True)
    return {"status": "started"}


@router.get("/status")
async def update_status():
    """Aktueller Stand eines laufenden oder abgeschlossenen Updates."""
    return _update_state


@router.get("/history")
async def get_history():
    """Gibt die letzten 20 Commits aus dem lokalen Git-Log zurück."""
    try:
        current = await asyncio.to_thread(
            _run, ["git", "rev-parse", "HEAD"], timeout=5
        )
        current_hash = current.stdout.strip()

        log_result = await asyncio.to_thread(
            _run,
            ["git", "log", "--pretty=format:%H|%h|%ci|%s", "-20"],
            timeout=10,
        )
        if log_result.returncode != 0:
            return {"error": log_result.stderr, "commits": []}

        commits = []
        for line in log_result.stdout.strip().splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                full_hash, short_hash, date_str, subject = parts
                commits.append({
                    "hash":    full_hash,
                    "short":   short_hash,
                    "date":    date_str[:10],
                    "subject": subject,
                    "current": full_hash == current_hash,
                })
        return {"commits": commits}
    except Exception as exc:
        logger.error(f"History failed: {exc}")
        return {"error": str(exc), "commits": []}


@router.post("/rollback")
async def rollback(req: RollbackRequest, background_tasks: BackgroundTasks):
    """Stellt eine frühere Version (Commit-Hash) wieder her."""
    global _update_state
    if not re.fullmatch(r"[0-9a-f]{7,40}", req.commit):
        return JSONResponse({"error": "Ungültiger Commit-Hash"}, status_code=400)
    if _update_state["status"] == "running":
        return JSONResponse({"error": "Update läuft bereits"}, status_code=409)
    _update_state = {"status": "running", "log": ""}
    background_tasks.add_task(_apply, req.commit, fetch_first=False)
    return {"status": "started"}


@router.post("/reboot")
async def reboot_system(background_tasks: BackgroundTasks):
    """Startet den Raspberry Pi neu."""
    background_tasks.add_task(_do_reboot)
    return {"status": "rebooting"}


def _do_reboot():
    time.sleep(2)
    subprocess.run(["sudo", "reboot"], check=False)


def _apply(target: str, fetch_first: bool = False):
    """Gemeinsame Logik für Update und Rollback."""
    global _update_state
    log_lines = []

    def log(msg: str):
        logger.info(msg)
        log_lines.append(msg)
        _update_state["log"] = "\n".join(log_lines)

    try:
        if fetch_first:
            log("git fetch origin...")
            fetch = _run(["git", "fetch", "origin"], timeout=30)
            if fetch.returncode != 0:
                raise RuntimeError(fetch.stderr)

        log(f"git reset --hard {target}...")
        reset = _run(["git", "reset", "--hard", target], timeout=30)
        if reset.returncode != 0:
            raise RuntimeError(reset.stderr)
        log(reset.stdout.strip())

        if PIP.exists():
            log("pip install -r requirements.txt...")
            pip = _run([str(PIP), "install", "-q", "-r", "requirements.txt"], timeout=120)
            if pip.returncode != 0:
                log(f"pip Warnung: {pip.stderr[:200]}")

        log("Starte Service neu...")
        restart = subprocess.run(
            ["sudo", "systemctl", "restart", "greenhouse"],
            capture_output=True, text=True, timeout=15,
        )
        if restart.returncode == 0:
            log("Service neu gestartet.")
        else:
            log(f"systemctl: {restart.stderr.strip()} (Mock-Mode oder kein systemd?)")

        _update_state["status"] = "done"
        log("Abgeschlossen.")

    except Exception as exc:
        logger.error(f"Apply failed: {exc}")
        log(f"FEHLER: {exc}")
        _update_state["status"] = "error"
