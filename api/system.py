"""
System API – Neustart und Herunterfahren des Raspberry Pi.
"""

import subprocess
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


def _delayed_run(cmd: list, delay: float = 2.0):
    """Führt einen Befehl nach einer kurzen Verzögerung aus (damit die HTTP-Antwort zuerst gesendet wird)."""
    time.sleep(delay)
    subprocess.run(cmd, check=False)


@router.post("/reboot")
async def reboot():
    """Startet den Raspberry Pi neu."""
    import asyncio
    asyncio.get_event_loop().run_in_executor(None, _delayed_run, ["sudo", "reboot"])
    return {"status": "rebooting"}


@router.post("/shutdown")
async def shutdown():
    """Fährt den Raspberry Pi herunter."""
    import asyncio
    asyncio.get_event_loop().run_in_executor(None, _delayed_run, ["sudo", "shutdown", "-h", "now"])
    return {"status": "shutting_down"}
