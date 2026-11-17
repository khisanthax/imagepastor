import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import face_wake
import imagepastor


class FaceGateTests(unittest.TestCase):
    def test_auto_sleep_can_wake_on_return_without_a_second_departure(self):
        gate = face_wake.FaceGate(armed=True)
        for tick in range(4):
            self.assertFalse(gate.observe(True, tick * 0.25))
        self.assertTrue(gate.observe(True, 1.0))

    def arm(self, gate):
        for tick in range(9):
            self.assertFalse(gate.observe(False, tick * 0.25))
        self.assertTrue(gate.armed)

    def test_face_still_present_after_hotkey_never_wakes(self):
        gate = face_wake.FaceGate()
        self.assertFalse(any(gate.observe(True, tick * 0.25) for tick in range(100)))
        self.assertFalse(gate.armed)

    def test_departure_then_sustained_return(self):
        gate = face_wake.FaceGate()
        self.arm(gate)
        for tick in range(4):
            self.assertFalse(gate.observe(True, 2.25 + tick * 0.25))
        self.assertTrue(gate.observe(True, 3.25))

    def test_brief_absence_does_not_arm(self):
        gate = face_wake.FaceGate()
        for tick in range(8):
            gate.observe(False, tick * 0.25)
        gate.observe(True, 2.0)
        self.assertFalse(gate.armed)

    def test_brief_face_does_not_wake_and_absence_resets_streak(self):
        gate = face_wake.FaceGate()
        self.arm(gate)
        gate.observe(True, 2.25)
        gate.observe(False, 2.5)
        self.assertFalse(gate.observe(True, 3.0))
        self.assertFalse(gate.observe(True, 3.25))

    def test_frame_gaps_do_not_count_as_sustained_presence(self):
        gate = face_wake.FaceGate()
        self.arm(gate)
        self.assertFalse(gate.observe(True, 2.25))
        self.assertFalse(gate.observe(True, 20.0))
        self.assertFalse(gate.observe(True, 20.25))


class FastCancel(threading.Event):
    def wait(self, timeout=None):
        return self.is_set()


class FaceWatcherTests(unittest.TestCase):
    def test_cancelled_worker_never_opens_camera(self):
        cancel = FastCancel()
        cancel.set()
        with patch.object(face_wake.subprocess, "Popen") as create:
            face_wake.FaceWatcher().run(cancel, {}, Mock(), Mock())
            create.assert_not_called()

    def test_success_wakes_once_after_child_has_exited(self):
        process = Mock(returncode=face_wake.FACE_DETECTED_EXIT)
        process.communicate.return_value = (b"FACE\n", None)
        process.poll.return_value = face_wake.FACE_DETECTED_EXIT
        wake = Mock()
        with patch.object(face_wake.subprocess, "Popen", return_value=process) as create:
            face_wake.FaceWatcher().run(FastCancel(), {}, wake, Mock())
        wake.assert_called_once()
        self.assertEqual(create.call_args.kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(create.call_args.args[0][-2:], ["--face-wake-worker", "0"])

    def test_missing_camera_never_wakes_or_retries(self):
        process = Mock(returncode=3)
        process.communicate.return_value = (b"", None)
        process.poll.return_value = 3
        wake = Mock()
        with patch.object(face_wake.subprocess, "Popen", return_value=process) as create:
            face_wake.FaceWatcher().run(FastCancel(), {}, wake, Mock())
        wake.assert_not_called()
        create.assert_called_once()

    def test_hung_camera_cancel_kills_entire_helper_tree_without_wake(self):
        cancel = FastCancel()
        process = Mock(returncode=None, pid=123)
        process.poll.return_value = None
        def communicate(timeout):
            if not cancel.is_set():
                cancel.set()
                raise subprocess.TimeoutExpired("helper", timeout)
            return b"FACE\n", None
        process.communicate.side_effect = communicate
        wake = Mock()
        with patch.object(face_wake.subprocess, "Popen", return_value=process), \
                patch.object(face_wake.subprocess, "run") as kill_tree:
            face_wake.FaceWatcher().run(cancel, {}, wake, Mock())
        wake.assert_not_called()
        self.assertEqual(kill_tree.call_args.args[0], ["taskkill", "/PID", "123", "/T", "/F"])

    def test_only_local_integer_camera_indices_are_accepted(self):
        for index in (-1, 17, "http://camera", True):
            with self.subTest(index=index), patch.object(face_wake.subprocess, "Popen") as create:
                face_wake.FaceWatcher().run(FastCancel(), {"face_wake_camera_index": index}, Mock(), Mock())
                create.assert_not_called()

    def test_failed_frame_releases_camera_and_is_not_absence(self):
        camera = Mock()
        camera.open.return_value = True
        camera.read.return_value = (False, None)
        detector = Mock()
        detector.empty.return_value = False
        cv = SimpleNamespace(setNumThreads=Mock(), data=SimpleNamespace(haarcascades="models"),
                             CascadeClassifier=Mock(return_value=detector), VideoCapture=Mock(return_value=camera),
                             CAP_DSHOW=1, CAP_PROP_FRAME_WIDTH=2, CAP_PROP_FRAME_HEIGHT=3, CAP_PROP_BUFFERSIZE=4)
        with patch.dict(sys.modules, {"cv2": cv}), patch.object(face_wake, "FaceGate") as gate:
            self.assertEqual(face_wake.camera_worker(0), 4)
            gate.return_value.observe.assert_not_called()
        camera.release.assert_called_once()

    def test_detection_uses_reserved_exit_code_and_releases_camera(self):
        camera = Mock()
        camera.open.return_value = True
        camera.read.return_value = (True, SimpleNamespace(shape=(480, 640, 3)))
        detector = Mock()
        detector.empty.return_value = False
        detector.detectMultiScale.return_value = [(0, 0, 100, 100)]
        cv = SimpleNamespace(setNumThreads=Mock(), data=SimpleNamespace(haarcascades="models"),
                             CascadeClassifier=Mock(return_value=detector), VideoCapture=Mock(return_value=camera),
                             resize=Mock(), cvtColor=Mock(), equalizeHist=Mock(), COLOR_BGR2GRAY=5,
                             CAP_DSHOW=1, CAP_PROP_FRAME_WIDTH=2, CAP_PROP_FRAME_HEIGHT=3, CAP_PROP_BUFFERSIZE=4)
        with patch.dict(sys.modules, {"cv2": cv}), patch.object(face_wake, "FaceGate") as gate:
            gate.return_value.observe.return_value = True
            self.assertEqual(face_wake.camera_worker(6), face_wake.FACE_DETECTED_EXIT)
        camera.release.assert_called_once()


class FaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.old = (imagepastor.active_config, imagepastor.monitor_sleep_guard_enabled,
                    imagepastor.monitor_sleep_guard_cancel_event, imagepastor.face_wake_session_locked)
        imagepastor.monitor_sleep_guard_enabled = True
        imagepastor.monitor_sleep_guard_cancel_event = threading.Event()
        imagepastor.face_wake_session_locked = False

    def tearDown(self):
        imagepastor.stop_face_wake()
        (imagepastor.active_config, imagepastor.monitor_sleep_guard_enabled,
         imagepastor.monitor_sleep_guard_cancel_event, imagepastor.face_wake_session_locked) = self.old

    def test_disabled_or_locked_session_does_not_start_camera(self):
        with patch.object(imagepastor.threading, "Thread") as create:
            imagepastor.start_face_wake({"face_wake_enabled": False})
            imagepastor.face_wake_session_locked = True
            imagepastor.start_face_wake({"face_wake_enabled": True})
            create.assert_not_called()

    def test_face_uses_existing_wake_only_for_its_current_guard(self):
        with patch.object(imagepastor.threading, "Thread") as create, \
                patch.object(imagepastor, "set_monitor_sleep_guard") as wake:
            imagepastor.start_face_wake({"face_wake_enabled": True})
            callback = create.call_args.kwargs["args"][2]
            callback()
            wake.assert_called_once_with(False, {"face_wake_enabled": True})
            wake.reset_mock()
            imagepastor.monitor_sleep_guard_cancel_event = threading.Event()
            callback()
            wake.assert_not_called()
            imagepastor.stop_face_wake()
            callback()
            wake.assert_not_called()

    def test_lock_cancels_camera_without_unlocking_or_waking(self):
        cancel = threading.Event()
        imagepastor.face_wake_cancel = cancel
        with patch.object(imagepastor, "set_monitor_sleep_guard") as wake:
            imagepastor.monitor_control_window_proc(0, imagepastor.WM_WTSSESSION_CHANGE,
                                                   imagepastor.WTS_SESSION_LOCK, 0)
            wake.assert_not_called()
        self.assertTrue(cancel.is_set())
        self.assertTrue(imagepastor.face_wake_session_locked)

    def test_saved_toggle_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps({"paste_delay_ms": 321}))
            imagepastor.active_config = {"face_wake_enabled": False}
            with patch.object(imagepastor, "app_dir", return_value=root), \
                    patch.object(imagepastor, "start_face_wake"), patch.object(imagepastor, "update_tray_icon"):
                imagepastor.toggle_face_wake()
            self.assertEqual(json.loads((root / "config.json").read_text()),
                             {"paste_delay_ms": 321, "face_wake_enabled": True})


if __name__ == "__main__":
    unittest.main()
