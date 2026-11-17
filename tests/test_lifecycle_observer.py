import sys
import subprocess
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lifecycle_observer import Observer, ProcessAPI, windows_reports


class FakeAPI:
    def __init__(self):
        self.current = {100: 50, 101: 100}
        self.codes = {100: None, 101: None}
        self.closed = []

    def processes(self):
        return self.current

    def open(self, pid):
        return pid

    def exit_code(self, handle):
        return self.codes[handle]

    def close(self, handle):
        self.closed.append(handle)


class ObserverTests(unittest.TestCase):
    def test_native_process_handle_preserves_real_exit_code(self):
        api = ProcessAPI()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2); raise SystemExit(7)"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        handle = api.open(child.pid)
        try:
            self.assertIsNone(api.exit_code(handle))
            child.wait(timeout=5)
            self.assertEqual(api.exit_code(handle), 7)
        finally:
            api.close(handle)
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_records_both_supervisor_and_app_exiting_together(self):
        api = FakeAPI()
        records = []
        observer = Observer(api, lambda kind, **fields: records.append((kind, fields)))
        observer.poll()
        api.current = {}
        api.codes = {100: 1, 101: 0xFFFFFFFF}
        observer.poll()
        exits = [fields for kind, fields in records if kind == "process_exit"]
        self.assertEqual([item["pid"] for item in exits], [100, 101])
        self.assertEqual(exits[1]["exit_hex"], "0xFFFFFFFF")
        self.assertEqual(api.closed, [100, 101])

    def test_clean_exit_zero_is_recorded(self):
        api = FakeAPI()
        records = []
        observer = Observer(api, lambda kind, **fields: records.append((kind, fields)))
        observer.poll()
        api.current = {}
        api.codes = {100: 0, 101: 0}
        observer.poll()
        self.assertEqual(len([record for record in records if record[0] == "process_exit"]), 2)
        observer.poll()
        self.assertEqual(len([record for record in records if record[0] == "process_exit"]), 2)

    def test_access_denied_is_logged_once_not_each_poll(self):
        api = FakeAPI()
        def denied(pid):
            raise OSError("PRIVATE ERROR")
        api.open = denied
        records = []
        observer = Observer(api, lambda kind, **fields: records.append((kind, fields)))
        observer.poll()
        observer.poll()
        self.assertEqual(len(records), 2)
        self.assertNotIn("PRIVATE", str(records))

    def test_windows_report_filters_other_apps_and_private_fields(self):
        xml = """<Events><Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
        <System><EventID>1002</EventID><EventRecordID>55119</EventRecordID><TimeCreated SystemTime="2026-11-12T14:45:35Z"/></System>
        <EventData><Data Name="AppName">ImagePastor.exe</Data><Data Name="ProcessId">0x13390</Data>
        <Data Name="HangType">Cross-process;Quiesce</Data><Data Name="Private">PRIVATE CONTENT</Data></EventData>
        </Event></Events>"""
        reports = windows_reports(xml)
        self.assertEqual(reports[0]["record_id"], "55119")
        self.assertNotIn("PRIVATE", str(reports))
        self.assertEqual(windows_reports(xml.replace("ImagePastor.exe", "other.exe")), [])
