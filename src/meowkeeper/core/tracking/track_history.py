"""短期 track bbox 歷史與動作特徵。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import pairwise

from .base import TrackObservation


@dataclass(frozen=True)
class MotionFeatures:
    speed_px_per_sec: float
    acceleration_px_per_sec2: float
    vertical_speed_px_per_sec: float
    direction_changes: int
    motion_state: str
    dwell_seconds: float


class TrackHistory:
    """保留有限 bbox 歷史；不逐影格寫 SQLite。"""

    def __init__(self, *, maxlen: int = 30, stationary_speed: float = 8.0, running_speed: float = 120.0) -> None:
        if maxlen < 3:
            raise ValueError("track history 長度至少為 3。")
        self.maxlen = maxlen
        self.stationary_speed = stationary_speed
        self.running_speed = running_speed
        self._history: dict[str | int, deque[TrackObservation]] = {}
        self._last_features: dict[str | int, MotionFeatures] = {}

    def update(self, observation: TrackObservation) -> MotionFeatures:
        history = self._history.setdefault(observation.track_id, deque(maxlen=self.maxlen))
        history.append(observation)
        if len(history) < 2:
            result = MotionFeatures(0.0, 0.0, 0.0, 0, "stationary", 0.0)
            self._last_features[observation.track_id] = result
            return result
        previous = history[-2]
        current = history[-1]
        elapsed = max((current.captured_at - previous.captured_at).total_seconds(), 1e-3)
        px, py = previous.bbox.center
        cx, cy = current.bbox.center
        dx, dy = cx - px, cy - py
        speed = (dx * dx + dy * dy) ** 0.5 / elapsed
        previous_features = self._last_features.get(observation.track_id)
        previous_speed = previous_features.speed_px_per_sec if previous_features else 0.0
        acceleration = (speed - previous_speed) / elapsed
        changes = self._direction_changes(history)
        if speed <= self.stationary_speed:
            state = "stationary"
        elif speed >= self.running_speed:
            state = "running"
        else:
            state = "walking"
        dwell = max((current.captured_at - history[0].captured_at).total_seconds(), 0.0)
        result = MotionFeatures(speed, acceleration, dy / elapsed, changes, state, dwell)
        self._last_features[observation.track_id] = result
        return result

    def get(self, track_id: str | int) -> tuple[TrackObservation, ...]:
        return tuple(self._history.get(track_id, ()))

    def features(self, track_id: str | int) -> MotionFeatures | None:
        return self._last_features.get(track_id)

    def reset(self, track_id: str | int | None = None) -> None:
        if track_id is None:
            self._history.clear()
            self._last_features.clear()
        else:
            self._history.pop(track_id, None)
            self._last_features.pop(track_id, None)

    @staticmethod
    def _direction_changes(history: deque[TrackObservation]) -> int:
        directions: list[int] = []
        items = list(history)
        for previous, current in pairwise(items):
            dx = current.bbox.center[0] - previous.bbox.center[0]
            if abs(dx) > 1.0:
                directions.append(1 if dx > 0 else -1)
        return sum(1 for left, right in pairwise(directions) if left != right)
