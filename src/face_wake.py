"""Local presence detection only. No identification, recording, or network access."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

# Windowed executables have no reliable stdout; use a reserved helper exit code.
FACE_DETECTED_EXIT = 42

class FaceGate:
    """Require departure before return, with sustained fresh observations."""

    def __init__(self, absent_seconds=2.0, present_seconds=1.0):
        self.absent_seconds = absent_seconds
        self.present_seconds = present_seconds
        self.armed = False
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


def camera_worker(camera_index: int) -> int:
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
        gate = FaceGate()
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
            if gate.observe(len(faces) > 0, time.monotonic()):
                return FACE_DETECTED_EXIT
            time.sleep(0.2)
    finally:
        camera.release()


class FaceWatcher:
    def __init__(self):
        self.camera_lock = threading.Lock()

    def run(self, cancel: threading.Event, config: dict, wake, log):
        if cancel.wait(3.0):
            return
        while not self.camera_lock.acquire(timeout=0.2):
            if cancel.is_set():
                return
        process = None
        try:
            if cancel.is_set():
                return
            index = config.get("face_wake_camera_index", 0)
            if type(index) is not int or not 0 <= index <= 16:
                raise ValueError("Camera index must be an integer from 0 to 16")
            command = ([sys.executable] if getattr(sys, "frozen", False) else
                       [sys.executable, str(Path(__file__).resolve().with_name("imagepastor.py"))])
            process = subprocess.Popen(
                [*command, "--face-wake-worker", str(index)], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log("Face wake camera requested; waiting for departure, then a sustained face.")
            while not cancel.is_set():
                try:
                    process.communicate(timeout=0.2)
                except subprocess.TimeoutExpired:
                    continue
                if not cancel.is_set() and process.returncode == FACE_DETECTED_EXIT:
                    wake()
                elif not cancel.is_set():
                    log("Face wake camera unavailable or stopped; monitor hotkey remains available.")
                return
        except Exception as exc:
            log(f"Face wake stopped ({type(exc).__name__}); monitor hotkey remains available.")
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
                self.camera_lock.release()


def worker_main() -> int:
    try:
        index = int(sys.argv[2])
        if not 0 <= index <= 16:
            return 2
        return camera_worker(index)
    except Exception:
        return 5
