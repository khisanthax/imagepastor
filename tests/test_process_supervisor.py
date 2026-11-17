import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from process_supervisor import supervise


class SupervisorTests(unittest.TestCase):
    def run_supervisor(self, codes, times=None):
        with tempfile.TemporaryDirectory() as directory:
            launch = Mock(side_effect=[SimpleNamespace(pid=100 + i, wait=lambda code=code: code) for i, code in enumerate(codes)])
            sleep = Mock()
            clock = Mock(side_effect=times) if times else lambda: 0
            result = supervise(["app.exe", "--app-worker"], Path(directory), launch=launch, sleep=sleep, clock=clock)
            log = (Path(directory) / "imagepastor-supervisor.log").read_text()
            return result, launch.call_count, sleep.call_count, log

    def test_clean_exit_never_restarts(self):
        result, launches, sleeps, log = self.run_supervisor([0])
        self.assertEqual((result, launches, sleeps), (0, 1, 0))
        self.assertIn("Clean exit; not restarting", log)

    def test_abnormal_exit_records_code_and_recovers(self):
        result, launches, sleeps, log = self.run_supervisor([0xC0000005, 0])
        self.assertEqual((result, launches, sleeps), (0, 2, 1))
        self.assertIn("hex=0xC0000005", log)

    def test_restart_storm_is_bounded(self):
        result, launches, sleeps, log = self.run_supervisor([1, 1, 1])
        self.assertEqual((result, launches, sleeps), (1, 3, 2))
        self.assertIn("restart limit reached", log)

    def test_failure_window_expires(self):
        result, launches, sleeps, log = self.run_supervisor([1, 1, 1, 0], [0, 601, 602])
        self.assertEqual((result, launches, sleeps), (0, 4, 3))

    def test_launch_failure_does_not_spin(self):
        with tempfile.TemporaryDirectory() as directory:
            launch = Mock(side_effect=OSError("not logged"))
            self.assertEqual(supervise(["app.exe"], Path(directory), launch=launch), 1)
            self.assertNotIn("not logged", (Path(directory) / "imagepastor-supervisor.log").read_text())
