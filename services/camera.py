"""
Camera service: captures timelapse frames via USB camera (OpenCV)
and compiles them into a video using ffmpeg.
"""

import logging
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from services import device_lock, v4l2

logger = logging.getLogger(__name__)

try:
    import cv2
    from services import exposure
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV not available – camera features disabled")

DEFAULT_FOURCC = "MJPG"

# Zeitfenster, das Erkennung und Vorschau auf eine belegte Kamera warten.
# Die Aufnahme wartet unbegrenzt, sie hat Vorrang.
LOCK_TIMEOUT = 2.0

# Reiner Marker: dokumentiert, dass FOURCC vor Breite/Höhe gesetzt werden muss.
# Wird von tests/test_camera_open.py importiert.
_PROP_FOURCC_ORDER_MARKER = "fourcc-before-size"


class CameraBusy(RuntimeError):
    """Die Kamera ist gerade von einem anderen Zugriff belegt."""


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

# OpenCV property IDs (numeric for compatibility across builds)
_PROP_BRIGHTNESS     = 10
_PROP_CONTRAST       = 11
_PROP_GAIN           = 14
_PROP_EXPOSURE       = 15
_PROP_AUTO_EXPOSURE  = 21
_PROP_GAMMA          = 22
_PROP_ZOOM           = 27
_PROP_FOCUS          = 28
_PROP_AUTOFOCUS      = 39
_PROP_AUTO_WB        = 44
_PROP_WB_TEMPERATURE = 45

# V4L2: 1 = manuell, 3 = Blendenpriorität (automatisch).
_V4L2_EXPOSURE_MANUAL = 1.0

# Mehr als drei Korrekturschritte lohnen nicht: die Regelung ist gedämpft und
# konvergiert in zwei Schritten, jeder weitere kostet nur Kamerazeit.
_EXPOSURE_ITERATIONS = 3

# Nach einer Belichtungsänderung braucht der Sensor zwei Frames, bis der neue
# Wert wirklich im Bild ankommt.
_EXPOSURE_SETTLE_FRAMES = 2

CAMERA_PROPERTIES = [
    {"key": "auto_exposure", "prop": _PROP_AUTO_EXPOSURE,  "label": "Auto-Belichtung",    "type": "bool"},
    {"key": "exposure",      "prop": _PROP_EXPOSURE,       "label": "Belichtung",         "type": "range", "min": 1,    "max": 5000,  "step": 1,   "auto_key": "auto_exposure"},
    {"key": "gain",          "prop": _PROP_GAIN,           "label": "Verstärkung",        "type": "range", "min": 0,    "max": 255,   "step": 1},
    {"key": "brightness",    "prop": _PROP_BRIGHTNESS,     "label": "Helligkeit",         "type": "range", "min": 0,    "max": 255,   "step": 1},
    {"key": "gamma",         "prop": _PROP_GAMMA,          "label": "Gamma",              "type": "range", "min": 1,    "max": 500,   "step": 1},
    {"key": "auto_wb",       "prop": _PROP_AUTO_WB,        "label": "Auto-Weissabgleich", "type": "bool"},
    {"key": "white_balance", "prop": _PROP_WB_TEMPERATURE, "label": "Weissabgleich",      "type": "range", "min": 2000, "max": 10000, "step": 100, "unit": "K", "auto_key": "auto_wb"},
    {"key": "contrast",      "prop": _PROP_CONTRAST,       "label": "Kontrast",           "type": "range", "min": 0,    "max": 255,   "step": 1},
    {"key": "autofocus",     "prop": _PROP_AUTOFOCUS,      "label": "Auto-Fokus",         "type": "bool"},
    {"key": "focus",         "prop": _PROP_FOCUS,          "label": "Fokus",              "type": "range", "min": 0,    "max": 255,   "step": 1,   "auto_key": "autofocus"},
    {"key": "zoom",          "prop": _PROP_ZOOM,           "label": "Zoom",               "type": "range", "min": 100,  "max": 800,   "step": 10},
]

_RESOLUTION_LABELS = {(w, h): label for w, h, label in COMMON_RESOLUTIONS if label}

_detect_cache: dict[tuple, object] = {}


def clear_detect_cache() -> None:
    """Erkennungs-Cache leeren – nach Kamerawechsel im laufenden Betrieb."""
    _detect_cache.clear()


def _cached(key: tuple, producer, refresh: bool = False):
    if refresh:
        _detect_cache.pop(key, None)
    if key not in _detect_cache:
        _detect_cache[key] = producer()
    return _detect_cache[key]


def _resolution_label(width: int, height: int) -> str:
    known = _RESOLUTION_LABELS.get((width, height))
    return f"{width}×{height}" + (f" ({known})" if known else "")


def _formats_for(camera_index: int, refresh: bool = False) -> list[dict]:
    return _cached(
        ("formats", camera_index),
        lambda: v4l2.list_formats(f"/dev/video{camera_index}"),
        refresh,
    )


class CameraService:
    def __init__(self, camera_id: int = 0):
        self._camera_id: int = camera_id
        self._frames_dir = Path(f"timelapse/cam{camera_id}/frames")
        self._output_dir = Path(f"timelapse/cam{camera_id}/output")
        self._camera_index: int = 0
        self._capture_width: int = 0
        self._capture_height: int = 0
        self._session: str | None = None
        self._frame_count: int = 0
        self._cam_props: dict[int, float] = {}
        self._fourcc: str = DEFAULT_FOURCC
        self._warmup_seconds: float = 1.5
        self._target_brightness: float = 120.0
        self._brightness_tol: float = 12.0

    @property
    def camera_id(self) -> int:
        return self._camera_id

    def setup(
        self,
        frames_dir: str = "timelapse/frames",
        output_dir: str = "timelapse/output",
        camera_index: int = 0,
        capture_width: int = 0,
        capture_height: int = 0,
        fourcc: str = DEFAULT_FOURCC,
    ):
        self._frames_dir = Path(frames_dir)
        self._output_dir = Path(output_dir)
        self._camera_index = camera_index
        self._capture_width = capture_width
        self._capture_height = capture_height
        self._fourcc = fourcc or DEFAULT_FOURCC
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _open_capture(self, timeout: float | None = None):
        """Kamera unter Gerätelock öffnen und konfiguriert bereitstellen.

        timeout=None wartet unbegrenzt (Aufnahme hat Vorrang). Mit einem
        Timeout wird CameraBusy geworfen, wenn die Kamera belegt ist – der
        Aufrufer kann daraus eine verständliche Meldung machen, statt eine
        leere Liste zu liefern.

        Reihenfolge beim Setzen ist zwingend FOURCC, Breite, Höhe: setzt man
        zuerst die Breite, steht die Kamera kurzzeitig auf einem ungültigen
        Format und der Treiber snapped auf etwas anderes.
        """
        lock = device_lock.get(self._camera_index)
        acquired = lock.acquire() if timeout is None else lock.acquire(timeout=timeout)
        if not acquired:
            raise CameraBusy(f"Kamera {self._camera_index} ist belegt")

        cap = None
        try:
            cap = cv2.VideoCapture(self._camera_index)
            if not cap.isOpened():
                raise CameraBusy(f"Kamera {self._camera_index} lässt sich nicht öffnen")

            if self._fourcc:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self._fourcc))
            if self._capture_width > 0 and self._capture_height > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._capture_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._capture_height)

            yield cap
        finally:
            if cap is not None:
                cap.release()
            lock.release()

    # ------------------------------------------------------------------
    # Camera properties (white balance, contrast, focus, zoom)
    # ------------------------------------------------------------------

    def set_properties(self, props: dict[str, float]):
        """Update camera properties from a {key: value} dict."""
        prop_map = {p["key"]: p["prop"] for p in CAMERA_PROPERTIES}
        self._cam_props = {}
        for key, value in props.items():
            if key in prop_map:
                self._cam_props[prop_map[key]] = float(value)

    def _apply_props(self, cap, warmup_seconds: float | None = None):
        """Gespeicherte Properties setzen und die Kamera einregeln lassen.

        Das Warm-up läuft immer, auch ohne konfigurierte Properties: das erste
        Frame nach dem Öffnen einer USB-Kamera ist praktisch nie korrekt
        belichtet.
        """
        if self._cam_props:
            # Auto toggles first
            for pid in (_PROP_AUTO_WB, _PROP_AUTOFOCUS, _PROP_AUTO_EXPOSURE):
                if pid in self._cam_props:
                    cap.set(pid, self._cam_props[pid])
            # Manual values (skip if corresponding auto is on)
            for pid, val in self._cam_props.items():
                if pid in (_PROP_AUTO_WB, _PROP_AUTOFOCUS, _PROP_AUTO_EXPOSURE):
                    continue
                if pid == _PROP_WB_TEMPERATURE and self._cam_props.get(_PROP_AUTO_WB, 0) > 0.5:
                    continue
                if pid == _PROP_FOCUS and self._cam_props.get(_PROP_AUTOFOCUS, 0) > 0.5:
                    continue
                if pid == _PROP_EXPOSURE and self._cam_props.get(_PROP_AUTO_EXPOSURE, 0) > 1.5:
                    continue
                cap.set(pid, val)

        seconds = self._warmup_seconds if warmup_seconds is None else warmup_seconds
        self._warmup(cap, seconds)

    def _warmup(self, cap, seconds: float, max_frames: int = 60) -> int:
        """Frames verwerfen, bis die Kamera eingeregelt ist.

        Zeitbasiert statt frame-basiert, weil die Bildrate je nach Auflösung
        und Pixelformat stark schwankt. max_frames begrenzt den Aufwand bei
        sehr hohen Bildraten und bricht bei hängender Kamera ab.
        """
        if seconds <= 0:
            return 0
        deadline = time.monotonic() + seconds
        discarded = 0
        while time.monotonic() < deadline and discarded < max_frames:
            ok, _ = cap.read()
            if not ok:
                break
            discarded += 1
        return discarded

    def _exposure_manual(self, cap) -> bool:
        """Auto-Belichtung abschalten. True, wenn der Treiber es übernimmt."""
        cap.set(_PROP_AUTO_EXPOSURE, _V4L2_EXPOSURE_MANUAL)
        return abs(cap.get(_PROP_AUTO_EXPOSURE) - _V4L2_EXPOSURE_MANUAL) < 0.5

    def _capture_balanced(self, cap):
        """Frame lesen und auf die Ziel-Helligkeit regeln.

        Rückgabe ist das Frame mit der geringsten Abweichung zum Ziel, oder
        None wenn kein Frame gelesen werden konnte. Bei _target_brightness <= 0
        wird das erste Frame unverändert zurückgegeben.
        """
        ok, frame = cap.read()
        if not ok:
            return None

        target = self._target_brightness
        if target <= 0:
            return frame

        measured = exposure.measure_brightness(frame)
        best, best_err = frame, abs(measured - target)

        if not self._exposure_manual(cap):
            # Kamera lässt sich nicht manuell belichten – begrenzte
            # Software-Korrektur als Notbehelf.
            factor = exposure.software_gain(measured, target)
            if factor is None:
                return best
            logger.debug(f"cam{self._camera_id}: software gain {factor:.2f}")
            return cv2.convertScaleAbs(best, alpha=factor, beta=0)

        for _ in range(_EXPOSURE_ITERATIONS):
            if exposure.within_tolerance(measured, target, self._brightness_tol):
                return best

            current = cap.get(_PROP_EXPOSURE)
            if current <= 0:
                break

            cap.set(_PROP_EXPOSURE, current * exposure.correction_factor(measured, target))
            if abs(cap.get(_PROP_EXPOSURE) - current) < 1e-6:
                # Treiber hat den Wert nicht übernommen – weitere Versuche
                # würden nur Zeit kosten.
                break

            for _ in range(_EXPOSURE_SETTLE_FRAMES):
                cap.read()
            ok, frame = cap.read()
            if not ok:
                break

            measured = exposure.measure_brightness(frame)
            err = abs(measured - target)
            if err < best_err:
                best, best_err = frame, err

        if best_err > self._brightness_tol:
            logger.info(
                f"cam{self._camera_id}: Ziel-Helligkeit {target:.0f} nicht erreicht "
                f"(Abweichung {best_err:.0f})"
            )
        return best

    def detect_properties(self, camera_index: int) -> list[dict]:
        """Probe camera for supported properties and their value ranges."""
        if not CV2_AVAILABLE:
            return []
        lock = device_lock.get(camera_index)
        if not lock.acquire(timeout=LOCK_TIMEOUT):
            raise CameraBusy(f"Kamera {camera_index} ist belegt")
        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return []

            result = []
            for pdef in CAMERA_PROPERTIES:
                pid = pdef["prop"]
                current = cap.get(pid)

                entry = {
                    "key": pdef["key"],
                    "label": pdef["label"],
                    "type": pdef["type"],
                    "value": current,
                }
                if "unit" in pdef:
                    entry["unit"] = pdef["unit"]
                if "auto_key" in pdef:
                    entry["auto_key"] = pdef["auto_key"]

                if pdef["type"] == "range":
                    cap.set(pid, pdef["min"])
                    actual_min = cap.get(pid)
                    cap.set(pid, pdef["max"])
                    actual_max = cap.get(pid)
                    cap.set(pid, current)
                    supported = abs(actual_max - actual_min) > 0.001
                    entry["min"] = actual_min if supported else pdef["min"]
                    entry["max"] = actual_max if supported else pdef["max"]
                    entry["step"] = pdef["step"]
                    entry["supported"] = supported
                else:
                    test = 0.0 if current > 0.5 else 1.0
                    cap.set(pid, test)
                    readback = cap.get(pid)
                    supported = abs(readback - test) < 0.5
                    cap.set(pid, current)
                    entry["supported"] = supported

                result.append(entry)

            cap.release()
            return result
        finally:
            lock.release()

    # ------------------------------------------------------------------
    # Session control
    # ------------------------------------------------------------------

    def start_session(self, name: str | None = None) -> str:
        self._session = name or f"cam{self._camera_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
        try:
            with self._open_capture() as cap:
                self._apply_props(cap)
                frame = self._capture_balanced(cap)
        except CameraBusy as exc:
            logger.error(f"capture_frame: {exc}")
            return None

        if frame is None:
            logger.error("Failed to read frame from camera")
            return None

        filename = session_dir / f"cam{self._camera_id}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
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
        clip_path = session_dir / f"cam{self._camera_id}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mp4"

        try:
            with self._open_capture() as cap:
                self._apply_props(cap)

                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                out = cv2.VideoWriter(str(clip_path), fourcc, float(clip_fps), (w, h))

                try:
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
                finally:
                    out.release()
        except CameraBusy as exc:
            logger.error(f"capture_clip: {exc}")
            clip_path.unlink(missing_ok=True)
            return None

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
        try:
            with self._open_capture(timeout=LOCK_TIMEOUT) as cap:
                self._apply_props(cap)
                ret, frame = cap.read()
        except CameraBusy:
            return None
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

    def detect_cameras(self, refresh: bool = False) -> list[dict]:
        """Verfügbare Kameras. Primär aus sysfs, sonst OpenCV-Probing.

        sysfs ist nicht nur schneller, sondern liefert auch echte Gerätenamen
        und blendet Metadata-Nodes aus, die beim Öffnen scheitern würden.
        """
        def _produce():
            if v4l2.available():
                devices = v4l2.list_devices()
                if devices:
                    return [{"index": d["index"], "name": d["name"]} for d in devices]
            return self._detect_cameras_opencv()

        return _cached(("cameras",), _produce, refresh)

    def _detect_cameras_opencv(self) -> list[dict]:
        if not CV2_AVAILABLE:
            return []
        cameras = []
        for i in range(10):
            lock = device_lock.get(i)
            if not lock.acquire(timeout=0.2):
                continue  # belegt – vermutlich läuft dort eine Aufnahme
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    ret, _ = cap.read()
                    cap.release()
                    if ret:
                        cameras.append({"index": i, "name": f"Kamera {i}"})
            finally:
                lock.release()
        return cameras

    def detect_resolutions(self, camera_index: int, refresh: bool = False) -> list[dict]:
        """Vom Treiber gemeldete Auflösungen, absteigend nach Pixelzahl."""
        formats = _formats_for(camera_index, refresh)
        if formats:
            seen: set[tuple] = set()
            result = []
            for f in formats:
                key = (f["width"], f["height"])
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    {
                        "width": f["width"],
                        "height": f["height"],
                        "label": _resolution_label(f["width"], f["height"]),
                    }
                )
            result.sort(key=lambda r: r["width"] * r["height"], reverse=True)
            return result

        return _cached(
            ("res_cv", camera_index),
            lambda: self._detect_resolutions_opencv(camera_index),
            refresh,
        )

    def detect_formats(self, camera_index: int, refresh: bool = False) -> list[str]:
        """Pixelformate des Treibers, z. B. ["MJPG", "YUYV"]."""
        formats = _formats_for(camera_index, refresh)
        seen: list[str] = []
        for f in formats:
            if f["fourcc"] not in seen:
                seen.append(f["fourcc"])
        return seen

    def detect_fps(
        self, camera_index: int, width: int = 0, height: int = 0, refresh: bool = False
    ) -> list[int]:
        """Bildraten, die der Treiber für diese Auflösung meldet.

        Ohne Auflösung wird die Vereinigung über alle Formate gebildet.
        """
        formats = _formats_for(camera_index, refresh)
        if formats:
            values: set[int] = set()
            for f in formats:
                if width > 0 and height > 0 and (f["width"], f["height"]) != (width, height):
                    continue
                values.update(int(round(v)) for v in f["fps"])
            if values:
                return sorted(values)

        return _cached(
            ("fps_cv", camera_index, width, height),
            lambda: self._detect_fps_opencv(camera_index, width, height),
            refresh,
        )

    def _detect_resolutions_opencv(self, camera_index: int) -> list[dict]:
        """Fallback für Systeme ohne v4l2-ctl (Windows-Entwicklungsmaschine).

        Zwingend: FOURCC vor Breite vor Höhe, und Prüfung gegen ein wirklich
        gelesenes Frame. cap.get() gibt bei vielen Treibern nur den gesetzten
        Wunschwert zurück und erzeugt so falsch positive Treffer.
        """
        if not CV2_AVAILABLE:
            return []
        lock = device_lock.get(camera_index)
        if not lock.acquire(timeout=LOCK_TIMEOUT):
            return []
        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return []
            supported = []
            seen: set[tuple] = set()
            for w, h, label in COMMON_RESOLUTIONS:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*DEFAULT_FOURCC))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                ah, aw = frame.shape[:2]
                if (aw, ah) == (w, h) and (aw, ah) not in seen:
                    seen.add((aw, ah))
                    supported.append(
                        {"width": w, "height": h, "label": _resolution_label(w, h)}
                    )
            cap.release()
            supported.sort(key=lambda r: r["width"] * r["height"], reverse=True)
            return supported
        finally:
            lock.release()

    def _detect_fps_opencv(self, camera_index: int, width: int, height: int) -> list[int]:
        if not CV2_AVAILABLE:
            return []
        lock = device_lock.get(camera_index)
        if not lock.acquire(timeout=LOCK_TIMEOUT):
            return []
        try:
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return []
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*DEFAULT_FOURCC))
            if width > 0 and height > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            supported: list[int] = []
            for fps in COMMON_FPS:
                cap.set(cv2.CAP_PROP_FPS, fps)
                if round(cap.get(cv2.CAP_PROP_FPS)) == fps and fps not in supported:
                    supported.append(fps)
            cap.release()
            return sorted(supported)
        finally:
            lock.release()

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
