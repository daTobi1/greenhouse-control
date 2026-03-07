import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import Response
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


@router.get("/preview")
async def camera_preview():
    """Return a live JPEG preview from the camera."""
    data = state.camera_service.capture_preview()
    if data is None:
        raise HTTPException(503, "Camera not available")
    return Response(content=data, media_type="image/jpeg")
