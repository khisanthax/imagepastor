import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runtime_diagnostics import RuntimeDiagnostics
import imagepastor


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_start_and_clean_exit_receipts_and_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = []
            diagnostic = RuntimeDiagnostics(Path(directory), messages.append)
            old_hook = threading.excepthook
            with patch("runtime_diagnostics.faulthandler.enable"), patch("runtime_diagnostics.faulthandler.disable"):
                diagnostic.start()
                self.assertEqual(json.loads(diagnostic.state_path.read_text())["status"], "running")
                self.assertEqual(threading.excepthook, diagnostic.thread_error)
                diagnostic.finish("clean")
            self.assertEqual(json.loads(diagnostic.state_path.read_text())["status"], "clean")
            self.assertIs(threading.excepthook, old_hook)
            self.assertFalse(diagnostic.thread.is_alive())

    def test_abrupt_previous_run_is_reported_without_guessing_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = []
            diagnostic = RuntimeDiagnostics(Path(directory), messages.append)
            diagnostic.write_state("running")
            with patch("runtime_diagnostics.faulthandler.enable"), patch("runtime_diagnostics.faulthandler.disable"):
                diagnostic.start()
                diagnostic.finish("clean")
            self.assertTrue(any("cause unknown" in message for message in messages))

    def test_thread_error_records_frame_not_exception_content(self):
        messages = []
        diagnostic = RuntimeDiagnostics(Path("."), messages.append)
        try:
            raise ValueError("PRIVATE DRAFT CONTENT")
        except ValueError as exc:
            diagnostic.thread_error(SimpleNamespace(exc_type=type(exc), exc_traceback=exc.__traceback__))
        self.assertIn("ValueError", messages[0])
        self.assertIn("test_thread_error", messages[0])
        self.assertNotIn("PRIVATE DRAFT", messages[0])

    def test_heartbeat_is_local_and_stops(self):
        messages = []
        diagnostic = RuntimeDiagnostics(Path("."), messages.append)
        with patch.object(diagnostic.stop, "wait", side_effect=[False, True]) as wait:
            diagnostic.heartbeat()
        self.assertEqual(len(messages), 1)
        wait.assert_called_with(300)

    def test_closed_console_does_not_prevent_file_logging(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(imagepastor, "app_dir", return_value=Path(directory)), patch("builtins.print", side_effect=ValueError("closed")):
                imagepastor.log("test receipt")
            self.assertIn("test receipt", (Path(directory) / "imagepastor.log").read_text())


if __name__ == "__main__":
    unittest.main()
