"""影音接收與 AI 採樣的有界緩衝契約。

錄影緩衝保存壓縮片段；AI 佇列採 latest-frame-wins。兩者刻意分離，AI 忙碌時可以
丟棄舊的分析影格，但不會丟棄已收到的壓縮錄影片段。
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from queue import Empty
from typing import Generic, TypeVar


@dataclass(frozen=True)
class CompressedSegment:
    captured_at: datetime
    data: bytes
    duration_seconds: float = 1.0
    keyframe: bool = False


class CompressedRingBuffer:
    """只保存壓縮影音片段，不接受解碼後的 numpy 原始影格。"""

    def __init__(self, max_seconds: float = 60.0) -> None:
        if max_seconds <= 0:
            raise ValueError("壓縮環形快取秒數必須大於零。")
        self.max_seconds = float(max_seconds)
        self._segments: deque[CompressedSegment] = deque()
        self._duration_seconds = 0.0

    @property
    def duration_seconds(self) -> float:
        return self._duration_seconds

    @property
    def bytes_used(self) -> int:
        return sum(len(segment.data) for segment in self._segments)

    def append(
        self,
        captured_at: datetime,
        data: bytes,
        *,
        duration_seconds: float = 1.0,
        keyframe: bool = False,
    ) -> None:
        if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
            raise ValueError("壓縮片段必須是非空 bytes。")
        if duration_seconds <= 0:
            raise ValueError("壓縮片段持續時間必須大於零。")
        segment = CompressedSegment(
            captured_at,
            bytes(data),
            float(duration_seconds),
            bool(keyframe),
        )
        self._segments.append(segment)
        self._duration_seconds += segment.duration_seconds
        while self._segments and self._duration_seconds > self.max_seconds:
            removed = self._segments.popleft()
            self._duration_seconds -= removed.duration_seconds

    def snapshot(self, seconds: float | None = None) -> tuple[CompressedSegment, ...]:
        requested = self.max_seconds if seconds is None else float(seconds)
        if requested <= 0:
            return ()
        if not self._segments:
            return ()
        newest = self._segments[-1].captured_at
        cutoff = newest - timedelta(seconds=requested)
        return tuple(segment for segment in self._segments if segment.captured_at >= cutoff)

    def clear(self) -> None:
        self._segments.clear()
        self._duration_seconds = 0.0


T = TypeVar("T")


class LatestFrameQueue(Generic[T]):
    """有界 AI 影格佇列；新影格到達時淘汰最舊待分析影格。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: T | None = None
        self._closed = False
        self.dropped_frames = 0

    def put(self, frame: T) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("AI 影格佇列已關閉。")
            if self._latest is not None:
                self.dropped_frames += 1
            self._latest = frame
            self._condition.notify()

    def get(self, timeout_seconds: float | None = None) -> T:
        with self._condition:
            if not self._condition.wait_for(
                lambda: self._latest is not None or self._closed,
                timeout=timeout_seconds,
            ):
                raise TimeoutError("等待 AI 影格逾時。")
            if self._latest is None:
                raise RuntimeError("AI 影格佇列已關閉。")
            frame = self._latest
            self._latest = None
            return frame

    def get_nowait(self) -> T:
        with self._condition:
            if self._latest is None:
                raise Empty
            frame = self._latest
            self._latest = None
            return frame

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
