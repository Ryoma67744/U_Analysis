"""Throttle progress I/O without delaying cancellation or terminal events."""
from __future__ import annotations

from copy import deepcopy
import math
import time


class ProgressReporter:
    """Coalesce numeric progress; callers must check cancellation independently."""

    def __init__(self, emit, *, interval=1.0, clock=None):
        interval = float(interval)
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("Progress interval must be finite and non-negative")
        self.emit = emit
        self.interval = interval
        self.clock = clock or time.monotonic
        self.latest = None
        self._last_time = None
        self._boundary = None

    def update(self, value, *, force=False):
        self.latest = deepcopy(value)
        boundary = tuple(value.get(key) for key in
                         ("request_id", "stage", "phase", "state", "sample", "sample_number", "file_id"))
        now = self.clock()
        numeric_progress = (isinstance(value.get("done"), (int, float))
                            and isinstance(value.get("total"), (int, float)))
        finished = numeric_progress and value["total"] > 0 and value["done"] >= value["total"]
        terminal = any(value.get(key) in {"complete", "completed", "done", "error", "failed",
                                         "stopped", "cancelled", "interrupted", "completed_with_errors"}
                       for key in ("stage", "phase", "state"))
        if (force or self._last_time is None or boundary != self._boundary
                or not numeric_progress or finished or terminal
                or now - self._last_time >= self.interval):
            self.emit(deepcopy(self.latest))
            self._last_time, self._boundary = now, boundary
            return True
        return False

    def flush(self):
        if self.latest is not None:
            return self.update(self.latest, force=True)
        return False
