"""Observe the app from outside its process; respect intentional clean exits."""

import subprocess
import time
from datetime import datetime


def supervise(command, directory, *, launch=subprocess.Popen, clock=time.monotonic, sleep=time.sleep):
    def record(message):
        with (directory / "imagepastor-supervisor.log").open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")

    failures = []
    while True:
        try:
            child = launch(command, cwd=str(directory))
            record(f"App launched; launcher_pid={child.pid}.")
            code = child.wait()
            record(f"App exited; launcher_pid={child.pid}; exit_code={code}; hex=0x{code & 0xFFFFFFFF:08X}.")
        except OSError as exc:
            record(f"Supervisor stopped: {type(exc).__name__}; winerror={getattr(exc, 'winerror', None)}.")
            return 1
        if code == 0:
            record("Clean exit; not restarting.")
            return 0
        now = clock()
        failures = [when for when in failures if now - when < 600]
        failures.append(now)
        if len(failures) >= 3:
            record("Three abnormal exits within ten minutes; restart limit reached.")
            return 1
        record("Abnormal exit; restarting in two seconds. Monitor guard starts disabled for safe recovery.")
        sleep(2)
