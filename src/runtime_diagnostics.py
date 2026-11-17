"""Local process diagnostics without UI content or memory dumps."""

import faulthandler
import json
import os
import sys
import threading
import traceback
from datetime import datetime


class RuntimeDiagnostics:
    def __init__(self, directory, log):
        self.directory = directory
        self.log = log
        self.state_path = directory / "imagepastor-runtime.json"
        self.fault_handle = None
        self.stop = threading.Event()
        self.old_thread_hook = None
        self.thread = None

    def write_state(self, status):
        state = {
            "pid": os.getpid(),
            "status": status,
            "time": datetime.now().isoformat(timespec="seconds"),
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self.state_path)

    def start(self):
        try:
            previous = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if previous and previous.get("status") == "running":
            self.log("Previous run has no clean-exit receipt; cause unknown. "
                     "Check imagepastor-fault.log and Windows events.")
        self.write_state("running")
        self.log(f"Process diagnostics started; pid={os.getpid()}.")
        self.fault_handle = (self.directory / "imagepastor-fault.log").open("a", encoding="utf-8")
        faulthandler.enable(file=self.fault_handle, all_threads=True)
        self.old_thread_hook = threading.excepthook
        threading.excepthook = self.thread_error
        self.thread = threading.Thread(target=self.heartbeat, name="diagnostics", daemon=True)
        self.thread.start()

    def heartbeat(self):
        while not self.stop.wait(300):
            self.log(f"Process heartbeat; pid={os.getpid()}.")

    def report_exception(self, kind, exception_type, tb):
        # Frame locations only: exception messages and source lines may contain UI data.
        frames = traceback.extract_tb(tb)
        locations = " -> ".join(f"{frame.filename}:{frame.lineno} ({frame.name})" for frame in frames)
        self.log(f"{kind}: {exception_type.__name__}; frames: {locations}")

    def thread_error(self, args):
        self.report_exception("Unhandled worker exception", args.exc_type, args.exc_traceback)

    def finish(self, status):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)
        if self.old_thread_hook is not None:
            threading.excepthook = self.old_thread_hook
        self.write_state(status)
        self.log(f"Process ending; pid={os.getpid()}; status={status}.")
        if self.fault_handle:
            faulthandler.disable()
            self.fault_handle.close()
