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


def _cam(cam: int):
    """Get camera service for slot, raise 404 if slot out of range."""
    return state.get_camera(cam)


@router.get("/status")
async def get_status(cam: int = Query(0, ge=0)):
    settings = await state.db.get_all_settings()
    cs = _cam(cam)
    camera_count = int(settings.get("camera_count", 1))

    # Per-camera settings with legacy fallback for cam 0
    if cam == 0:
        active       = settings.get("cam_0_timelapse_active", settings.get("timelapse_active", False))
        interval     = settings.get("cam_0_timelapse_interval", settings.get("timelapse_interval", 300))
        fps          = settings.get("cam_0_timelapse_fps", settings.get("timelapse_fps", 25))
        dev_idx      = settings.get("cam_0_device_index", settings.get("camera_index", 0))
        capture_mode = settings.get("cam_0_capture_mode", settings.get("capture_mode", "still"))
        clip_duration= settings.get("cam_0_clip_duration", settings.get("clip_duration", 5))
        clip_fps     = settings.get("cam_0_clip_fps", settings.get("clip_fps", 10))
    else:
        active       = settings.get(f"cam_{cam}_timelapse_active", False)
        interval     = settings.get(f"cam_{cam}_timelapse_interval", 300)
        fps          = settings.get(f"cam_{cam}_timelapse_fps", 25)
        dev_idx      = settings.get(f"cam_{cam}_device_index", cam)
        capture_mode = settings.get(f"cam_{cam}_capture_mode", "still")
        clip_duration= settings.get(f"cam_{cam}_clip_duration", 5)
        clip_fps     = settings.get(f"cam_{cam}_clip_fps", 10)

    return {
        "cam":             cam,
        "camera_count":    camera_count,
        "active":          active,
        "session":         cs.current_session,
        "frame_count":     cs.frame_count,
        "interval":        interval,
        "fps":             fps,
        "camera_index":    dev_idx,
        "capture_mode":    capture_mode,
        "clip_duration":   clip_duration,
        "clip_fps":        clip_fps,
        "camera_available": cs._frames_dir is not None,
    }


@router.post("/start")
async def start_timelapse(req: StartSessionRequest, cam: int = Query(0, ge=0)):
    cs = _cam(cam)
    if cs.is_capturing:
        raise HTTPException(400, "A timelapse session is already running")
    session = cs.start_session(req.name)
    await state.db.update_settings({f"cam_{cam}_timelapse_active": True})
    state.timelapse_wake.set()
    return {"session": session, "cam": cam}


@router.post("/stop")
async def stop_timelapse(cam: int = Query(0, ge=0)):
    cs = _cam(cam)
    session = cs.stop_session()
    await state.db.update_settings({f"cam_{cam}_timelapse_active": False})
    state.timelapse_wake.set()
    return {"stopped_session": session, "cam": cam}


@router.get("/sessions")
async def list_sessions(cam: int = Query(0, ge=0)):
    cs = _cam(cam)
    sessions = await asyncio.to_thread(cs.get_sessions)
    return {"sessions": sessions, "cam": cam}


@router.post("/compile/{session}")
async def compile_session(session: str, background_tasks: BackgroundTasks, cam: int = Query(0, ge=0)):
    cs = _cam(cam)
    all_sessions = await asyncio.to_thread(cs.get_sessions)
    sessions = {s["name"]: s for s in all_sessions}
    if session not in sessions:
        raise HTTPException(404, "Session not found")
    if _compile_jobs.get(session) == "running":
        raise HTTPException(409, "Compilation already in progress")

    settings = await state.db.get_all_settings()
    fps = int(settings.get(f"cam_{cam}_timelapse_fps", settings.get("timelapse_fps", 25)))

    _compile_jobs[session] = "running"

    def _compile():
        result = cs.compile_timelapse(session, fps=fps)
        _compile_jobs[session] = "done" if result else "error"

    background_tasks.add_task(asyncio.to_thread, _compile)
    return {"session": session, "status": "compiling"}


@router.get("/compile/{session}/status")
async def compile_status(session: str, cam: int = Query(0, ge=0)):
    cs = _cam(cam)
    status = _compile_jobs.get(session, "not_started")
    all_sessions = await asyncio.to_thread(cs.get_sessions)
    sessions = {s["name"]: s for s in all_sessions}
    has_video = sessions.get(session, {}).get("has_video", False)
    return {"session": session, "status": status, "has_video": has_video}


@router.get("/sessions/{session}/files")
async def list_session_files(session: str, cam: int = Query(0, ge=0)):
    """List all captured files in a session."""
    cs = _cam(cam)
    session_dir = cs.frames_dir / session
    if not session_dir.exists():
        raise HTTPException(404, "Session not found")

    def _list_files():
        files = []
        for f in sorted(session_dir.iterdir()):
            if f.suffix in (".jpg", ".mp4"):
                files.append({
                    "name":  f.name,
                    "url":   f"/api/timelapse/sessions/{session}/file/{f.name}?cam={cam}",
                    "type":  "video" if f.suffix == ".mp4" else "image",
                    "size":  f.stat().st_size,
                })
        return files

    files = await asyncio.to_thread(_list_files)
    return {"session": session, "files": files}


@router.get("/sessions/{session}/file/{filename}")
async def get_session_file(session: str, filename: str, cam: int = Query(0, ge=0)):
    """Serve an individual captured file (image or video)."""
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    cs = _cam(cam)
    path = (cs.frames_dir / session / filename).resolve()
    if not path.exists() or path.suffix not in (".jpg", ".mp4"):
        raise HTTPException(404, "File not found")
    media = "video/mp4" if path.suffix == ".mp4" else "image/jpeg"
    return FileResponse(path, media_type=media)


@router.delete("/sessions/{session}")
async def delete_session(session: str, cam: int = Query(0, ge=0)):
    cs = _cam(cam)
    if cs.current_session == session:
        raise HTTPException(400, "Cannot delete an active session")
    deleted = cs.delete_session(session)
    if not deleted:
        raise HTTPException(404, "Session not found")
    return {"deleted": session}


@router.get("/cameras")
async def detect_cameras():
    """Scan for available camera devices (non-blocking thread)."""
    cs = _cam(0)
    cameras = await asyncio.to_thread(cs.detect_cameras)
    return {"cameras": cameras}


@router.get("/resolutions")
async def detect_resolutions(camera: int = Query(0, ge=0)):
    """Return resolutions supported by the given camera index."""
    cs = _cam(0)
    resolutions = await asyncio.to_thread(cs.detect_resolutions, camera)
    return {"resolutions": resolutions}


@router.get("/fps")
async def detect_fps(camera: int = Query(0, ge=0), width: int = Query(0), height: int = Query(0)):
    """Return FPS values the camera supports at the given resolution."""
    cs = _cam(0)
    fps_list = await asyncio.to_thread(cs.detect_fps, camera, width, height)
    return {"fps": fps_list}


@router.get("/video/{session}")
async def get_video(session: str, cam: int = Query(0, ge=0)):
    """Serve a compiled timelapse video for download."""
    cs = _cam(cam)
    settings = await state.db.get_all_settings()
    tl_path = settings.get("timelapse_path", "timelapse")
    path = Path(tl_path) / f"cam{cam}" / "output" / f"{session}.mp4"
    if not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(path, media_type="video/mp4", filename=f"{session}.mp4")


@router.get("/browse", response_class=HTMLResponse)
async def browse_timelapse():
    """Simple HTTP file browser for the timelapse output folder (network share)."""
    settings = await state.db.get_all_settings()
    if not settings.get("timelapse_share_enabled", False):
        raise HTTPException(403, "Network share is disabled")
    tl_path = settings.get("timelapse_path", "timelapse")
    camera_count = int(settings.get("camera_count", 1))
    IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
    VIDEO_EXTS = {".mp4"}
    files = []
    for ci in range(camera_count):
        output = Path(tl_path) / f"cam{ci}" / "output"
        if output.exists():
            for f in sorted(output.iterdir(), reverse=True):
                if f.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
                    files.append((ci, f))
    def _item(ci: int, f: Path) -> str:
        is_video = f.suffix.lower() in VIDEO_EXTS
        url  = f"/api/timelapse/video/{f.stem}?cam={ci}" if is_video else f"/api/timelapse/output/{f.name}?cam={ci}"
        tag  = '<span style="color:#79c0ff;font-size:.75rem">Video</span>' if is_video \
               else '<span style="color:#56d364;font-size:.75rem">Bild</span>'
        size = f.stat().st_size // 1024
        cam_label = f'<span style="color:#e3b341;font-size:.7rem">Cam {ci}</span>'
        return (f'<li>{cam_label} {tag} &nbsp;<a href="{url}">{f.name}</a>'
                f' &nbsp;<span style="color:#8b949e">({size:,} KB)</span></li>')
    items = "".join(_item(ci, f) for ci, f in files)
    return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<title>Timelapse – Netzwerkfreigabe</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;
background:#0d1117;color:#e6edf3;padding:1rem}}
h1{{color:#79c0ff;margin-bottom:1rem}}a{{color:#58a6ff}}
li{{margin:.5rem 0;font-size:.9rem;list-style:none}}</style></head>
<body><h1>&#127909; Timelapse-Aufnahmen</h1>
<ul>{items or "<li>Keine Aufnahmen vorhanden</li>"}</ul></body></html>"""


@router.get("/output/{filename}")
async def get_output_file(filename: str, cam: int = Query(0, ge=0)):
    """Serve an image from the timelapse output folder."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    settings = await state.db.get_all_settings()
    if not settings.get("timelapse_share_enabled", False):
        raise HTTPException(403, "Network share is disabled")
    IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
    tl_path = settings.get("timelapse_path", "timelapse")
    path = (Path(tl_path) / f"cam{cam}" / "output" / filename).resolve()
    if not path.exists() or path.suffix.lower() not in IMAGE_EXTS:
        raise HTTPException(404, "File not found")
    media = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(path, media_type=media)


@router.get("/preview")
async def camera_preview(cam: int = Query(0, ge=0)):
    """Return a live JPEG preview from the camera."""
    cs = _cam(cam)
    data = await asyncio.to_thread(cs.capture_preview)
    if data is None:
        raise HTTPException(503, "Camera not available")
    return Response(content=data, media_type="image/jpeg")
