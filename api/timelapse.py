import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

import state

router = APIRouter()

# Track ongoing compilation jobs
_compile_jobs: dict[str, str] = {}  # session -> "running" | "done" | "error"


class StartSessionRequest(BaseModel):
    name: str | None = None


@router.get("/status")
async def get_status():
    settings = await state.db.get_all_settings()
    return {
        "active":          settings.get("timelapse_active", False),
        "session":         state.camera_service.current_session,
        "frame_count":     state.camera_service.frame_count,
        "interval":        settings.get("timelapse_interval", 300),
        "fps":             settings.get("timelapse_fps", 25),
        "camera_index":    settings.get("camera_index", 0),
        "capture_mode":    settings.get("capture_mode", "still"),
        "clip_duration":   settings.get("clip_duration", 5),
        "clip_fps":        settings.get("clip_fps", 10),
        "camera_available": state.camera_service._frames_dir is not None,
    }


@router.post("/start")
async def start_timelapse(req: StartSessionRequest):
    if state.camera_service.is_capturing:
        raise HTTPException(400, "A timelapse session is already running")
    session = state.camera_service.start_session(req.name)
    await state.db.update_settings({"timelapse_active": True})
    return {"session": session}


@router.post("/stop")
async def stop_timelapse():
    session = state.camera_service.stop_session()
    await state.db.update_settings({"timelapse_active": False})
    return {"stopped_session": session}


@router.get("/sessions")
async def list_sessions():
    return {"sessions": state.camera_service.get_sessions()}


@router.post("/compile/{session}")
async def compile_session(session: str, background_tasks: BackgroundTasks):
    sessions = {s["name"]: s for s in state.camera_service.get_sessions()}
    if session not in sessions:
        raise HTTPException(404, "Session not found")
    if _compile_jobs.get(session) == "running":
        raise HTTPException(409, "Compilation already in progress")

    settings = await state.db.get_all_settings()
    fps = int(settings.get("timelapse_fps", 25))

    _compile_jobs[session] = "running"

    def _compile():
        result = state.camera_service.compile_timelapse(session, fps=fps)
        _compile_jobs[session] = "done" if result else "error"

    background_tasks.add_task(asyncio.to_thread, _compile)
    return {"session": session, "status": "compiling"}


@router.get("/compile/{session}/status")
async def compile_status(session: str):
    status = _compile_jobs.get(session, "not_started")
    sessions = {s["name"]: s for s in state.camera_service.get_sessions()}
    has_video = sessions.get(session, {}).get("has_video", False)
    return {"session": session, "status": status, "has_video": has_video}


@router.delete("/sessions/{session}")
async def delete_session(session: str):
    if state.camera_service.current_session == session:
        raise HTTPException(400, "Cannot delete an active session")
    deleted = state.camera_service.delete_session(session)
    if not deleted:
        raise HTTPException(404, "Session not found")
    return {"deleted": session}


@router.get("/cameras")
async def detect_cameras():
    """Scan for available camera devices (non-blocking thread)."""
    cameras = await asyncio.to_thread(state.camera_service.detect_cameras)
    return {"cameras": cameras}


@router.get("/resolutions")
async def detect_resolutions(camera: int = Query(0, ge=0)):
    """Return resolutions supported by the given camera index."""
    resolutions = await asyncio.to_thread(state.camera_service.detect_resolutions, camera)
    return {"resolutions": resolutions}


@router.get("/fps")
async def detect_fps(camera: int = Query(0, ge=0), width: int = Query(0), height: int = Query(0)):
    """Return FPS values the camera supports at the given resolution."""
    fps_list = await asyncio.to_thread(state.camera_service.detect_fps, camera, width, height)
    return {"fps": fps_list}


@router.get("/video/{session}")
async def get_video(session: str):
    """Serve a compiled timelapse video for download."""
    settings = await state.db.get_all_settings()
    path = Path(settings.get("timelapse_path", "timelapse")) / "output" / f"{session}.mp4"
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{session}.mp4")


@router.get("/browse", response_class=HTMLResponse)
async def browse_timelapse():
    """Simple HTTP file browser for the timelapse output folder (network share)."""
    settings = await state.db.get_all_settings()
    if not settings.get("timelapse_share_enabled", False):
        raise HTTPException(403, "Network share is disabled")
    output = Path(settings.get("timelapse_path", "timelapse")) / "output"
    files = sorted(output.glob("*.mp4"), reverse=True) if output.exists() else []
    items = "".join(
        f'<li><a href="/api/timelapse/video/{f.stem}">{f.name}</a>'
        f' &nbsp;<span style="color:#8b949e">({f.stat().st_size // 1024:,} KB)</span></li>'
        for f in files
    )
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<title>Timelapse – Netzwerkfreigabe</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;
background:#0d1117;color:#e6edf3;padding:1rem}}
h1{{color:#79c0ff;margin-bottom:1rem}}a{{color:#58a6ff}}
li{{margin:.5rem 0;font-size:.9rem}}</style></head>
<body><h1>&#127909; Timelapse-Videos</h1>
<ul>{items or "<li>Keine Videos vorhanden</li>"}</ul></body></html>"""


@router.get("/preview")
async def camera_preview():
    """Return a live JPEG preview from the camera."""
    data = state.camera_service.capture_preview()
    if data is None:
        raise HTTPException(503, "Camera not available")
    return Response(content=data, media_type="image/jpeg")
