"""
Camera service: captures timelapse frames via USB camera (OpenCV)
and compiles them into a video using ffmpeg.
"""

import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV not available – camera features disabled")


COMMON_FPS = [5, 10, 15, 20, 24, 25, 30, 60]

COMMON_RESOLUTIONS = [
    (320,  240,  "QVGA"),
    (640,  480,  "VGA"),
    (800,  600,  "SVGA"),
    (1024, 768,  "XGA"),
    (1280, 720,  "HD 720p"),
    (1280, 960,  ""),
    (1600, 900,  "HD+"),
    (1920, 1080, "Full HD"),
    (2560, 1440, "QHD"),
    (3840, 2160, "4K UHD"),
]


class CameraService:
    def __init__(self):
        self._frames_dir = Path("timelapse/frames")
        self._output_dir = Path("timelapse/output")
        self._camera_index: int = 0
        self._capture_width: int = 0
        self._capture_height: int = 0
        self._session: str | None = None
        self._frame_count: int = 0

    def setup(
        self,
        frames_dir: str = "timelapse/frames",
        output_dir: str = "timelapse/output",
        camera_index: int = 0,
        capture_width: int = 0,
        capture_height: int = 0,
    ):
        self._frames_dir = Path(frames_dir)
        self._output_dir = Path(output_dir)
        self._camera_index = camera_index
        self._capture_width = capture_width
        self._capture_height = capture_height
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Session control
    # ------------------------------------------------------------------

    def start_session(self, name: str | None = None) -> str:
        self._session = name or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._frame_count = 0
        session_dir = self._frames_dir / self._session
        session_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Timelapse session started: {self._session}")
        return self._session

    def stop_session(self) -> str | None:
        session = self._session
        self._session = None
        logger.info(f"Timelapse session stopped: {session}")
        return session

    @property
    def is_capturing(self) -> bool:
        return self._session is not None

    @property
    def current_session(self) -> str | None:
        return self._session

    @property
    def frame_count(self) -> int:
        return self._frame_count

    # ------------------------------------------------------------------
    # Frame capture
    # ------------------------------------------------------------------

    def capture_frame(self) -> str | None:
        """Capture one frame into the current session directory."""
        if not CV2_AVAILABLE:
            return None
        if not self._session:
            logger.warning("capture_frame called without an active session")
            return None

        session_dir = self._frames_dir / self._session
        cap = cv2.VideoCapture(self._camera_index)
        if self._capture_width > 0 and self._capture_height > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._capture_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._capture_height)
        if not cap.isOpened():
            logger.error("Cannot open camera")
            return None

        ret, frame = cap.read()
        cap.release()

        if not ret:
            logger.error("Failed to read frame from camera")
            return None

        filename = session_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        cv2.imwrite(str(filename), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        self._frame_count += 1
        logger.debug(f"Frame captured → {filename.name}")
        return str(filename)

    def capture_clip(self, duration: float = 5.0, clip_fps: int = 10) -> str | None:
        """Record a short video clip into the current session directory."""
        if not CV2_AVAILABLE:
            return None
        if not self._session:
            logger.warning("capture_clip called without an active session")
            return None

        session_dir = self._frames_dir / self._session
        cap = cv2.VideoCapture(self._camera_index)
        if self._capture_width > 0 and self._capture_height > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._capture_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._capture_height)
        if not cap.isOpened():
            logger.error("Cannot open camera for clip")
            return None

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        clip_path = session_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(str(clip_path), fourcc, float(clip_fps), (w, h))

        frame_interval = 1.0 / clip_fps
        end_time = time.monotonic() + duration
        next_frame = time.monotonic()

        while time.monotonic() < end_time:
            now = time.monotonic()
            if now >= next_frame:
                ret, frame = cap.read()
                if ret:
                    out.write(frame)
                next_frame += frame_interval
            else:
                time.sleep(min(0.005, next_frame - now))

        cap.release()
        out.release()

        if clip_path.exists() and clip_path.stat().st_size > 0:
            self._frame_count += 1
            logger.debug(f"Clip {self._frame_count} captured → {clip_path.name}")
            return str(clip_path)

        clip_path.unlink(missing_ok=True)
        return None

    def capture_preview(self) -> bytes | None:
        """Return a JPEG-encoded preview image for the dashboard."""
        if not CV2_AVAILABLE:
            return None
        cap = cv2.VideoCapture(self._camera_index)
        if not cap.isOpened():
            return None
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return None
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes()

    # ------------------------------------------------------------------
    # Compilation
    # ------------------------------------------------------------------

    def compile_timelapse(self, session: str, fps: int = 25) -> str | None:
        """Compile all frames or clips of a session into an MP4 using ffmpeg."""
        session_dir = self._frames_dir / session
        if not session_dir.exists():
            logger.error(f"Session directory not found: {session_dir}")
            return None

        clips  = sorted(session_dir.glob("*.mp4"))
        frames = sorted(session_dir.glob("*.jpg"))

        if not clips and not frames:
            logger.error("No frames or clips found in session")
            return None

        output_file = self._output_dir / f"{session}.mp4"
        list_file   = self._output_dir / f"{session}_list.txt"

        try:
            if clips:
                # Clip mode: concatenate mp4 segments (stream copy, fast)
                with open(list_file, "w") as f:
                    for clip in clips:
                        f.write(f"file '{clip.absolute()}'\n")
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(list_file),
                    "-c", "copy",
                    str(output_file),
                ]
            else:
                # Still mode: compile JPEGs into video
                with open(list_file, "w") as f:
                    for frame in frames:
                        f.write(f"file '{frame.absolute()}'\n")
                        f.write(f"duration {1 / fps:.6f}\n")
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(list_file),
                    "-vf", f"fps={fps}",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-crf", "23",
                    str(output_file),
                ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            list_file.unlink(missing_ok=True)

            if result.returncode == 0:
                logger.info(f"Timelapse compiled: {output_file}")
                return str(output_file)
            else:
                logger.error(f"ffmpeg error:\n{result.stderr[-1000:]}")
                return None

        except subprocess.TimeoutExpired:
            logger.error("ffmpeg compilation timed out")
            return None
        except FileNotFoundError:
            logger.error("ffmpeg not found – install with: sudo apt install ffmpeg")
            return None

    # ------------------------------------------------------------------
    # Session listing
    # ------------------------------------------------------------------

    @property
    def frames_dir(self) -> Path:
        return self._frames_dir

    def get_sessions(self) -> list[dict]:
        if not self._frames_dir.exists():
            return []
        sessions = []
        for d in self._frames_dir.iterdir():
            if not d.is_dir():
                continue
            frames = list(d.glob("*.jpg"))
            clips  = list(d.glob("*.mp4"))
            count  = len(clips) if clips else len(frames)
            mode   = "clip" if clips else "still"
            output = self._output_dir / f"{d.name}.mp4"
            sessions.append(
                {
                    "name": d.name,
                    "frame_count": count,
                    "capture_mode": mode,
                    "has_video": output.exists(),
                    "video_url": f"/api/timelapse/video/{d.name}" if output.exists() else None,
                    "active": d.name == self._session,
                }
            )
        return sorted(sessions, key=lambda x: x["name"], reverse=True)

    def detect_cameras(self) -> list[dict]:
        """Scan video device indices 0-9 and return those that can deliver a frame."""
        if not CV2_AVAILABLE:
            return []
        cameras = []
        for i in range(10):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    cameras.append({"index": i, "name": f"Kamera {i}"})
        return cameras

    def detect_resolutions(self, camera_index: int) -> list[dict]:
        """Return resolutions that the camera actually supports."""
        if not CV2_AVAILABLE:
            return []
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return []
        supported = []
        seen: set[tuple] = set()
        for w, h, label in COMMON_RESOLUTIONS:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            aw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            ah = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if (aw, ah) == (w, h) and (aw, ah) not in seen:
                seen.add((aw, ah))
                name = f"{w}×{h}" + (f" ({label})" if label else "")
                supported.append({"width": w, "height": h, "label": name})
        cap.release()
        return supported

    def detect_fps(self, camera_index: int, width: int = 0, height: int = 0) -> list[int]:
        """Return FPS values the camera supports at the given resolution."""
        if not CV2_AVAILABLE:
            return []
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return []
        if width > 0 and height > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        supported = []
        seen: set[int] = set()
        for fps in COMMON_FPS:
            cap.set(cv2.CAP_PROP_FPS, fps)
            actual = round(cap.get(cv2.CAP_PROP_FPS))
            if actual == fps and actual not in seen:
                seen.add(actual)
                supported.append(actual)
        cap.release()
        return sorted(supported)

    def delete_session(self, session: str) -> bool:
        session_dir = self._frames_dir / session
        output_file = self._output_dir / f"{session}.mp4"
        deleted = False
        if session_dir.exists():
            import shutil
            shutil.rmtree(session_dir)
            deleted = True
        if output_file.exists():
            output_file.unlink()
            deleted = True
        return deleted
