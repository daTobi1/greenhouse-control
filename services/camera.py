"""
Camera service: captures timelapse frames via USB camera (OpenCV)
and compiles them into a video using ffmpeg.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV not available – camera features disabled")


class CameraService:
    def __init__(self):
        self._frames_dir = Path("timelapse/frames")
        self._output_dir = Path("timelapse/output")
        self._camera_index: int = 0
        self._session: str | None = None
        self._frame_count: int = 0

    def setup(
        self,
        frames_dir: str = "timelapse/frames",
        output_dir: str = "timelapse/output",
        camera_index: int = 0,
    ):
        self._frames_dir = Path(frames_dir)
        self._output_dir = Path(output_dir)
        self._camera_index = camera_index
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
        if not cap.isOpened():
            logger.error("Cannot open camera")
            return None

        ret, frame = cap.read()
        cap.release()

        if not ret:
            logger.error("Failed to read frame from camera")
            return None

        filename = session_dir / f"frame_{self._frame_count:06d}.jpg"
        cv2.imwrite(str(filename), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        self._frame_count += 1
        logger.debug(f"Frame {self._frame_count} captured → {filename.name}")
        return str(filename)

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
        """Compile all frames of a session into an MP4 using ffmpeg."""
        session_dir = self._frames_dir / session
        if not session_dir.exists():
            logger.error(f"Session directory not found: {session_dir}")
            return None

        frames = sorted(session_dir.glob("frame_*.jpg"))
        if not frames:
            logger.error("No frames found in session")
            return None

        output_file = self._output_dir / f"{session}.mp4"
        list_file   = self._output_dir / f"{session}_list.txt"

        try:
            with open(list_file, "w") as f:
                for frame in frames:
                    f.write(f"file '{frame.absolute()}'\n")
                    f.write(f"duration {1 / fps:.6f}\n")

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-vf", "fps=25",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "23",
                str(output_file),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
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

    def get_sessions(self) -> list[dict]:
        sessions = []
        for d in self._frames_dir.iterdir():
            if not d.is_dir():
                continue
            frames = sorted(d.glob("frame_*.jpg"))
            output = self._output_dir / f"{d.name}.mp4"
            sessions.append(
                {
                    "name": d.name,
                    "frame_count": len(frames),
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
