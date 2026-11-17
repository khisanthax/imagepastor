"""Local presence detection only. No identification, recording, or network access."""

from __future__ import annotations

import subprocess
import json
import sys
import threading
import time
import tempfile
from pathlib import Path

# Windowed executables have no reliable stdout; use a reserved helper exit code.
FACE_DETECTED_EXIT = 42

class FaceGate:
    """Require departure before return, with sustained fresh observations."""

    def __init__(self, absent_seconds=2.0, present_seconds=1.0, armed=False):
        self.absent_seconds = absent_seconds
        self.present_seconds = present_seconds
        self.armed = armed
        self.started = None
        self.last = None
        self.samples = 0

    def observe(self, face_present: bool, now: float) -> bool:
        if self.last is not None and now - self.last > 1.0:
            self.started = None
            self.samples = 0
        self.last = now
        wanted = face_present if self.armed else not face_present
        if not wanted:
            self.started = None
            self.samples = 0
            return False
        if self.started is None:
            self.started = now
        self.samples += 1
        threshold = self.present_seconds if self.armed else self.absent_seconds
        if self.samples < 3 or now - self.started < threshold:
            return False
        if self.armed:
            return True
        self.armed = True
        self.started = None
        self.samples = 0
        return False


def camera_worker(camera_index: int, status_path: Path | None = None, already_away=False) -> int:
    """The helper owns the camera; terminating it also releases a hung driver handle."""
    import cv2

    cv2.setNumThreads(1)
    detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    if detector.empty():
        return 2
    camera = cv2.VideoCapture()
    try:
        if not camera.open(camera_index, cv2.CAP_DSHOW):
            return 3
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        gate = FaceGate(armed=already_away)
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                return 4  # Missing frames are never evidence of absence or presence.
            height, width = frame.shape[:2]
            if not height or not width:
                return 4
            small = cv2.resize(frame, (480, max(1, round(height * 480 / width))))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(60, 60))
            now = time.monotonic()
            if status_path is not None:
                temporary = status_path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"face": len(faces) > 0, "time": now}), encoding="utf-8")
                try:
                    temporary.replace(status_path)
                except PermissionError:
                    pass  # A simultaneous Windows reader can briefly block replacement.
            elif gate.observe(len(faces) > 0, now):
                return FACE_DETECTED_EXIT
            time.sleep(0.2)
    finally:
        camera.release()


class FaceWatcher:
    def __init__(self):
        self.camera_lock = threading.Lock()

    def run(self, cancel: threading.Event, config: dict, wake, log, observe=None):
        if cancel.wait(3.0):
            return
        while not self.camera_lock.acquire(timeout=0.2):
            if cancel.is_set():
                return
        process = None
        status_directory = None
        try:
            if cancel.is_set():
                return
            index = config.get("face_wake_camera_index", 0)
            if type(index) is not int or not 0 <= index <= 16:
                raise ValueError("Camera index must be an integer from 0 to 16")
            command = ([sys.executable] if getattr(sys, "frozen", False) else
                       [sys.executable, str(Path(__file__).resolve().with_name("imagepastor.py"))])
            extra = []
            if observe is None and config.get("_face_wake_armed", False):
                extra = ["--armed"]
            if observe is not None:
                status_directory = tempfile.TemporaryDirectory(prefix="imagepastor-presence-")
                status_path = Path(status_directory.name) / "status.json"
                extra = [str(status_path)]
            process = subprocess.Popen(
                [*command, "--face-wake-worker", str(index), *extra], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log("Auto-sleep presence camera requested; waiting for 15 minutes of absence and input idle."
                if observe is not None else
                "Face wake camera requested; departure already verified, waiting for a sustained face."
                if config.get("_face_wake_armed", False) else
                "Face wake camera requested; waiting for departure, then a sustained face.")
            while not cancel.is_set():
                try:
                    process.communicate(timeout=0.2)
                except subprocess.TimeoutExpired:
                    if observe is not None and not cancel.is_set():
                        try:
                            status = json.loads(status_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            status = None
                        observe(status)
                    continue
                if observe is not None:
                    if not cancel.is_set():
                        observe(None)
                        log("Auto-sleep camera unavailable or stopped; automatic sleep suspended.")
                elif not cancel.is_set() and process.returncode == FACE_DETECTED_EXIT:
                    wake()
                elif not cancel.is_set():
                    log("Face wake camera unavailable or stopped; monitor hotkey remains available.")
                return
        except Exception as exc:
            if observe is not None:
                observe(None)
            log(f"Camera watcher stopped ({type(exc).__name__}); monitor hotkey remains available.")
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        # Frozen one-file builds have a bootloader parent and a camera child.
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=3.0, check=False)
                    try:
                        process.communicate(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=2.0)
            finally:
                try:
                    if status_directory is not None:
                        status_directory.cleanup()
                finally:
                    self.camera_lock.release()


def worker_main() -> int:
    try:
        index = int(sys.argv[2])
        if not 0 <= index <= 16:
            return 2
        argument = sys.argv[3] if len(sys.argv) > 3 else None
        return camera_worker(index, Path(argument) if argument and argument != "--armed" else None,
                             already_away=argument == "--armed")
    except Exception:
        return 5
