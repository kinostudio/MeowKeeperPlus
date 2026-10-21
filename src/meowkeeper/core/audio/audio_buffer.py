"""由同一個 media ingest 提供的短期音訊環形緩衝。"""

from __future__ import annotations

from collections import deque

from .base import AudioObservation


class AudioBuffer:
    def __init__(self, *, max_seconds: float = 30.0) -> None:
        if max_seconds <= 0:
            raise ValueError("音訊緩衝秒數必須大於零。")
        self.max_seconds = max_seconds
        self._items: deque[AudioObservation] = deque()

    def append(self, observation: AudioObservation) -> None:
        self._items.append(observation)
        newest = observation.captured_at
        while self._items and (newest - self._items[0].captured_at).total_seconds() > self.max_seconds:
            self._items.popleft()

    def recent(self) -> tuple[AudioObservation, ...]:
        return tuple(self._items)

    def clear(self) -> None:
        self._items.clear()
