"""
Server-side filesystem browser: browse directories, create folders.
Used by the timelapse path picker in the dashboard.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _safe_path(path: str) -> Path:
    """Resolve path and ensure it's absolute."""
    try:
        return Path(path).resolve()
    except Exception:
        raise HTTPException(400, "Invalid path")


@router.get("/browse")
async def browse(path: str = "/"):
    """List subdirectories of the given path."""
    p = _safe_path(path)
    if not p.exists():
        raise HTTPException(404, "Path does not exist")
    if not p.is_dir():
        raise HTTPException(400, "Not a directory")
    try:
        dirs = []
        for entry in sorted(p.iterdir(), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                dirs.append({"name": entry.name, "path": str(entry)})
        return {
            "path":   str(p),
            "parent": str(p.parent) if str(p) != str(p.parent) else None,
            "dirs":   dirs,
        }
    except PermissionError:
        raise HTTPException(403, "Permission denied")


class MkdirRequest(BaseModel):
    path: str


@router.post("/mkdir")
async def mkdir(req: MkdirRequest):
    """Create a directory (including parents)."""
    p = _safe_path(req.path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p), "created": True}
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    except OSError as e:
        raise HTTPException(400, str(e))
