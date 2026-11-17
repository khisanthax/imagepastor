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
from away_sleep import AwayGate
from face_wake import FaceWatcher
import imagepastor as app


class AwayGateTests(unittest.TestCase):
    def observe(self, gate, stamp, idle=None, face=False):
        return gate.observe({"face": face, "time": stamp}, stamp, stamp if idle is None else idle)

    def test_exact_15_minutes_not_earlier_and_no_extra_15_minutes(self):
        gate = AwayGate()
        for tick in range(1800):
            self.assertFalse(self.observe(gate, tick / 2))
        self.assertTrue(self.observe(gate, 900))

    def test_visible_face_prevents_sleep_despite_input_idle(self):
        gate = AwayGate(5)
        for tick in range(20):
            self.assertFalse(self.observe(gate, tick, idle=1000, face=True))

    def test_keyboard_or_mouse_activity_restarts_full_countdown(self):
        gate = AwayGate(5)
        for tick in range(5):
            self.observe(gate, tick)
        for tick in range(5, 10):
            self.assertFalse(self.observe(gate, tick, idle=tick - 5))
        self.assertTrue(self.observe(gate, 10, idle=5))

    def test_face_restarts_absence_countdown(self):
        gate = AwayGate(5)
        for tick in range(5):
            self.observe(gate, tick)
        self.assertFalse(self.observe(gate, 5, face=True))
        for tick in range(6, 11):
            self.assertFalse(self.observe(gate, tick))
        self.assertTrue(self.observe(gate, 11))

    def test_camera_failure_never_counts_as_absence(self):
        gate = AwayGate(5)
        for tick in range(5):
            self.observe(gate, tick)
        self.assertFalse(gate.observe(None, 5, 1000))
        for tick in range(6, 11):
            self.assertFalse(self.observe(gate, tick))
        self.assertTrue(self.observe(gate, 11))

    def test_stale_future_or_malformed_samples_fail_closed(self):
        gate = AwayGate(5)
        for value in (None, {}, {"face": 0, "time": 10}, {"face": False, "time": float("nan")},
                      {"face": False, "time": 20}, {"face": False, "time": 1}):
            self.assertFalse(gate.observe(value, 10, 1000))
            self.assertIsNone(gate.since)

    def test_frame_gap_resets_time(self):
        gate = AwayGate(5)
        for tick in range(5):
            self.observe(gate, tick)
        self.assertFalse(self.observe(gate, 100))
        for tick in range(101, 105):
            self.assertFalse(self.observe(gate, tick))
        self.assertTrue(self.observe(gate, 105))

    def test_reused_frame_cannot_trigger_sleep(self):
        gate = AwayGate(1)
        gate.observe({"face": False, "time": 0}, 0, 1000)
        self.assertFalse(gate.observe({"face": False, "time": 0}, 1, 1000))

    def test_unknown_input_state_prevents_sleep(self):
        gate = AwayGate(1)
        for idle in (None, float("nan"), -1):
            self.assertFalse(gate.observe({"face": False, "time": 10}, 10, idle))
            self.assertIsNone(gate.since)


class AwayIntegrationTests(unittest.TestCase):
    def test_last_input_tick_wrap_is_handled_without_logging_input(self):
        def last_input(pointer):
            pointer._obj.tick = 0xFFFFFFF0
            return True
        with patch.object(app.user32, "GetLastInputInfo", side_effect=last_input), \
                patch.object(app.kernel32, "GetTickCount", return_value=100):
            self.assertAlmostEqual(app.last_input_idle_seconds(), 0.116)

    def test_last_input_api_failure_is_unknown_not_idle(self):
        with patch.object(app.user32, "GetLastInputInfo", return_value=False):
            self.assertIsNone(app.last_input_idle_seconds())

    def test_disabled_locked_or_guarded_does_not_start_camera(self):
        for enabled, locked, guarded in ((False, False, False), (True, True, False), (True, False, True)):
            with patch.object(app, "face_wake_session_locked", locked), \
                    patch.object(app, "monitor_sleep_guard_enabled", guarded), \
                    patch.object(app, "desktop_is_unlocked", return_value=True), \
                    patch.object(app.threading, "Thread") as thread:
                app.start_auto_sleep({"auto_sleep_enabled": enabled})
                thread.assert_not_called()

    def test_unknown_desktop_state_does_not_open_camera(self):
        with patch.object(app, "monitor_sleep_guard_enabled", False), \
                patch.object(app, "desktop_is_unlocked", return_value=False), \
                patch.object(app.threading, "Thread") as thread:
            app.start_auto_sleep({"auto_sleep_enabled": True})
            thread.assert_not_called()

    def test_threshold_uses_existing_guard_and_stale_callback_cannot_repeat(self):
        with patch.object(app, "face_wake_session_locked", False), \
                patch.object(app, "monitor_sleep_guard_enabled", False), \
                patch.object(app, "desktop_is_unlocked", return_value=True), \
                patch.object(app, "last_input_idle_seconds", return_value=900), \
                patch.object(app, "AwayGate") as gate, patch.object(app, "set_monitor_sleep_guard") as guard, \
                patch.object(app, "log"), patch.object(app.threading, "Thread") as thread:
            gate.return_value.observe.return_value = True
            config = {"auto_sleep_enabled": True}
            app.start_auto_sleep(config)
            callback = thread.call_args.kwargs["kwargs"]["observe"]
            callback({"face": False, "time": 0})
            guard.assert_called_once_with(True, config, already_away=True)
            app.stop_auto_sleep()
            callback({"face": False, "time": 0})
            guard.assert_called_once()

    def test_setting_persists_without_changing_face_wake_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"face_wake_enabled": False, "face_wake_camera_index": 6}))
            config = {"face_wake_enabled": False}
            with patch.object(app, "active_config", config), patch.object(app, "app_dir", return_value=Path(directory)), \
                    patch.object(app, "start_auto_sleep") as start, patch.object(app, "update_tray_icon"), patch.object(app, "log"):
                app.toggle_auto_sleep()
                start.assert_called_once_with(config)
            saved = json.loads(path.read_text())
            self.assertTrue(saved["auto_sleep_enabled"])
            self.assertFalse(saved["face_wake_enabled"])
            self.assertEqual(saved["face_wake_camera_index"], 6)

    def test_presence_helper_reads_only_boolean_status_and_cancellation_releases_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "status.json").write_text('{"face":false,"time":10}')
            temporary = SimpleNamespace(name=directory, cleanup=Mock())
            cancel = threading.Event()
            cancel.wait = lambda timeout=None: cancel.is_set()
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.side_effect = lambda timeout: (_ for _ in ()).throw(subprocess.TimeoutExpired("camera", timeout)) if timeout == 0.2 else (b"", None)
            observations = []
            def observe(status):
                observations.append(status)
                cancel.set()
            watcher = FaceWatcher()
            with patch("face_wake.tempfile.TemporaryDirectory", return_value=temporary), \
                    patch("face_wake.subprocess.Popen", return_value=process):
                watcher.run(cancel, {}, None, Mock(), observe=observe)
            self.assertEqual(observations, [{"face": False, "time": 10}])
            self.assertFalse(watcher.camera_lock.locked())
            temporary.cleanup.assert_called_once()
