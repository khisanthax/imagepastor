"""Require fresh no-face evidence and keyboard/mouse inactivity together."""

import math

AWAY_SECONDS = 15 * 60


class AwayGate:
    def __init__(self, threshold=AWAY_SECONDS):
        self.threshold = threshold
        self.reset()

    def reset(self):
        self.since = None
        self.last = None

    def observe(self, status, now, idle_seconds):
        if (not isinstance(status, dict) or type(status.get("face")) is not bool
                or not isinstance(status.get("time"), (int, float))
                or not math.isfinite(status["time"])
                or idle_seconds is None or not math.isfinite(idle_seconds) or idle_seconds < 0
                or not 0 <= now - status["time"] <= 1.5):
            self.reset()
            return False
        stamp = status["time"]
        if self.last is not None and stamp <= self.last:
            return False
        if self.last is not None and stamp - self.last > 1.5:
            self.since = None
        self.last = stamp
        if status["face"]:
            self.since = None
            return False
        self.since = max(stamp if self.since is None else self.since, now - idle_seconds)
        return now - self.since >= self.threshold and idle_seconds >= self.threshold
