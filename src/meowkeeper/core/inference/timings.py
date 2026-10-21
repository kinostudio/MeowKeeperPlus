"""Small bounded duration distributions; stages are not added into a fake total."""
from __future__ import annotations

import math
import threading
from collections import deque


class StageTimings:
    def __init__(self, capacity=120):
        self.values = {}
        self.capacity = capacity
        self._lock = threading.Lock()

    def observe(self, samples):
        with self._lock:
            self._observe(samples)

    def _observe(self, samples):
        for name, value in samples.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                continue
            if name not in self.values:
                if len(self.values) >= 20:
                    continue
                self.values[name] = deque(maxlen=self.capacity)
            self.values[name].append(value)

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def _snapshot(self):
        result = {}
        for name, values in self.values.items():
            ordered = sorted(values)
            result[name] = {"samples": len(ordered), "average_ms": round(sum(ordered) / len(ordered), 3),
                            "p50_ms": round(ordered[max(0, math.ceil(len(ordered) * .5) - 1)], 3),
                            "p95_ms": round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 3)}
        return result
