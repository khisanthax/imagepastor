"""Independent, observation-only process lifecycle evidence for ImagePastor."""

import ctypes
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from ctypes import wintypes
from datetime import datetime
from pathlib import Path


class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]


class ProcessAPI:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Process32FirstW": ([wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
            "Process32NextW": ([wintypes.HANDLE, ctypes.POINTER(ProcessEntry)], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateMutexW": ([ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def processes(self):
        snapshot = self.api.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ProcessEntry()
            entry.size = ctypes.sizeof(entry)
            found = bool(self.api.Process32FirstW(snapshot, ctypes.byref(entry)))
            result = {}
            while found:
                if entry.name.lower() == "imagepastor.exe":
                    result[int(entry.pid)] = int(entry.parent)
                found = bool(self.api.Process32NextW(snapshot, ctypes.byref(entry)))
            return result
        finally:
            self.api.CloseHandle(snapshot)

    def open(self, pid):
        handle = self.api.OpenProcess(0x101000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def exit_code(self, handle):
        status = self.api.WaitForSingleObject(handle, 0)
        if status == 258:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.api.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def close(self, handle):
        self.api.CloseHandle(handle)


class Observer:
    def __init__(self, api, record):
        self.api, self.record = api, record
        self.handles = {}
        self.denied = set()

    def poll(self):
        for pid, handle in list(self.handles.items()):
            code = self.api.exit_code(handle)
            if code is not None:
                self.record("process_exit", pid=pid, exit_code=code, exit_hex=f"0x{code:08X}")
                self.api.close(handle)
                del self.handles[pid]
        current = self.api.processes()
        self.denied.intersection_update(current)
        for pid, parent in current.items():
            if pid in self.handles or pid in self.denied:
                continue
            try:
                self.handles[pid] = self.api.open(pid)
                self.record("process_observed", pid=pid, parent_pid=parent)
            except OSError as exc:
                self.denied.add(pid)
                self.record("process_access_unavailable", pid=pid, winerror=getattr(exc, "winerror", None))
        return sorted(current)


def windows_reports(xml):
    root = ET.fromstring(xml)
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
    result = []
    for event in root.findall("e:Event", ns):
        data = {item.get("Name"): item.text for item in event.findall("e:EventData/e:Data", ns)}
        if (data.get("AppName") or "").lower() != "imagepastor.exe":
            continue
        result.append({
            "record_id": event.findtext("e:System/e:EventRecordID", namespaces=ns),
            "event_id": event.findtext("e:System/e:EventID", namespaces=ns),
            "event_time_utc": event.find("e:System/e:TimeCreated", ns).get("SystemTime"),
            "process_id": data.get("ProcessId"), "hang_type": data.get("HangType"),
        })
    return result


def main():
    directory = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "dist"
    def record(kind, **fields):
        with (directory / "imagepastor-lifecycle.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": datetime.now().astimezone().isoformat(timespec="seconds"),
                                     "kind": kind, **fields}) + "\n")
    api = ProcessAPI()
    mutex = api.api.CreateMutexW(None, False, "ImagePastorIndependentObserver")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        return 0
    record("observer_started", observer_pid=os.getpid())
    observer = Observer(api, record)
    seen_reports = set()
    next_events = next_heartbeat = 0
    while True:
        try:
            pids = observer.poll()
            now = time.monotonic()
            if now >= next_heartbeat:
                record("observer_heartbeat", observer_pid=os.getpid(), app_pids=pids)
                next_heartbeat = now + 300
            if now >= next_events:
                next_events = now + 60
                response = subprocess.run([
                    "wevtutil.exe", "qe", "Application",
                    "/q:*[System[(EventID=1000 or EventID=1002) and (Provider[@Name='Application Hang'] or Provider[@Name='Application Error'])]]",
                    "/rd:true", "/c:50", "/f:xml", "/e:Events"],
                    capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                if response.returncode:
                    record("windows_events_unavailable", exit_code=response.returncode)
                else:
                    for report in windows_reports(response.stdout):
                        if report["record_id"] not in seen_reports:
                            record("windows_application_report", **report)
                            seen_reports.add(report["record_id"])
        except Exception as exc:
            record("observer_error", error_type=type(exc).__name__)
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
