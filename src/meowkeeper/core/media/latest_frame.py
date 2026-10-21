"""Drain the single ingest independently of slow AI; retain only the latest JPEG."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from meowkeeper.core.inference.timings import StageTimings


class LatestFrameReader:
    def __init__(self, frames: Iterable[Any], *, on_frame: Callable[[Any], None],
                 close_source: Callable[[], None]) -> None:
        self.frames = frames
        self.on_frame = on_frame
        self.close_source = close_source
        self.dropped = 0
        self._condition = threading.Condition()
        self._latest: Any = None
        self._latest_at = None
        self._wait_ms = None
        self._queue_ms = None
        self._last_arrival_at: float | None = None
        self._timings = StageTimings()
        self._done = False
        self._closed = False
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._read, name="meowkeeper-media-reader", daemon=True)

    def _read(self) -> None:
        try:
            for frame in self.frames:
                arrival_at = time.perf_counter()
                with self._condition:
                    if self._closed:
                        break
                    if self._last_arrival_at is not None:
                        self._timings.observe({
                            "media_frame_interarrival_ms": max(0.0, arrival_at - self._last_arrival_at) * 1000,
                        })
                    self._last_arrival_at = arrival_at
                callback_at = time.perf_counter()
                self.on_frame(frame)
                self._timings.observe({
                    "latest_frame_callback_ms": max(0.0, time.perf_counter() - callback_at) * 1000,
                })
                with self._condition:
                    if self._latest is not None:
                        self.dropped += 1
                    self._latest = frame
                    self._latest_at = time.monotonic()
                    self._condition.notify_all()
        except Exception as exc:  # noqa: BLE001 - rethrow in owner; never log raw secrets
            self._error = exc
        finally:
            with self._condition:
                self._done = True
                self._condition.notify_all()

    def __iter__(self) -> Iterator[Any]:
        self._thread.start()
        while True:
            with self._condition:
                waiting_at = time.monotonic()
                self._condition.wait_for(lambda: self._latest is not None or self._done or self._closed)
                now = time.monotonic()
                self._wait_ms = (now - waiting_at) * 1000
                if self._closed:
                    return
                frame, self._latest = self._latest, None
                self._queue_ms = (now - self._latest_at) * 1000 if frame is not None and self._latest_at is not None else None
                self._timings.observe({"frame_wait_ms": self._wait_ms, "latest_frame_queue_wait_ms": self._queue_ms})
                done = self._done
            if frame is not None:
                yield frame
            elif done:
                if self._error is not None:
                    raise self._error
                return

    def snapshot(self):
        with self._condition:
            return {"frame_wait_ms": self._wait_ms, "latest_frame_queue_wait_ms": self._queue_ms,
                    "dropped_frames": self.dropped, "queue_capacity": 1,
                    "timings": self._timings.snapshot(),
                    "source_decode_resize_ms": None,
                    "scope": "parent_monotonic_since_enqueue_not_camera_capture"}

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._latest = None
            self._condition.notify_all()
        self.close_source()
        if self._thread.ident is not None:
            self._thread.join(timeout=5)
