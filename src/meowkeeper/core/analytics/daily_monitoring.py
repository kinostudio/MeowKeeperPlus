"""以有界背景批次保存真實影格取樣區間，不逐影格寫入 SQLite。"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from queue import Empty, Full, Queue
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class DailyMonitoringConfig:
    flush_seconds: float = 30.0
    maximum_gap_seconds: float = 5.0
    maximum_pending_batches: int = 8

    def __post_init__(self) -> None:
        if (not math.isfinite(self.flush_seconds) or not math.isfinite(self.maximum_gap_seconds)
                or self.flush_seconds <= 0 or self.maximum_gap_seconds <= 0 or self.maximum_pending_batches < 1):
            raise ValueError("每日取樣統計的批次時間、最大間隔及佇列大小必須大於零。")


class DailyMonitoringRecorder:
    """只累計相鄰實測影格，丟棄長空白，正負過渡不推估貓咪時間。

    flush/observe 不呼叫資料庫；唯一背景 worker 做有限重試的 SQLite 寫入。
    程序崩潰可能遺失尚未提交的批次，不能用 Host uptime 補回這些時間。
    """

    def __init__(
        self, store: Any, *, config: DailyMonitoringConfig | None = None,
        local_timezone: tzinfo | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.config = config or DailyMonitoringConfig()
        self.local_timezone = local_timezone
        self.logger = logger or (lambda _message: None)
        self._lock = threading.Lock()
        self._last: tuple[datetime, bool | None] | None = None
        self._buckets: dict[str, list[float]] = {}
        self._last_flush = time.monotonic()
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=self.config.maximum_pending_batches)
        self._stopping = threading.Event()
        self.dropped_batches = 0
        self.failed_batches = 0
        self._worker = threading.Thread(target=self._run, name="meowkeeper-daily-summary", daemon=True)
        self._worker.start()

    def observe(self, captured_at: datetime, *, cat_present: bool | None) -> None:
        if captured_at.tzinfo is None or (cat_present is not None and type(cat_present) is not bool):
            raise ValueError("取樣時間必須帶時區；貓咪結果只能是 true、false 或未知。")
        current = captured_at.astimezone(timezone.utc)
        with self._lock:
            if self._stopping.is_set():
                return
            previous = self._last
            if previous and current <= previous[0]:
                return  # A clock regression must not count an interval twice.
            self._last = (current, cat_present)
            if previous:
                start, previous_cat = previous
                delta = (current - start).total_seconds()
                if 0 < delta <= self.config.maximum_gap_seconds:
                    cursor = start
                    while cursor < current:
                        local = cursor.astimezone(self.local_timezone)
                        midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0)
                                    + timedelta(days=1)).astimezone(timezone.utc)
                        end = min(current, midnight)
                        seconds = (end - cursor).total_seconds()
                        bucket = self._buckets.setdefault(local.date().isoformat(), [0.0, 0.0, 0.0])
                        bucket[0] += seconds
                        if previous_cat is not None and cat_present is not None:
                            bucket[1] += seconds
                            if previous_cat and cat_present:
                                bucket[2] += seconds
                        cursor = end
            if time.monotonic() - self._last_flush >= self.config.flush_seconds:
                self._flush_locked()

    def end_session(self) -> None:
        with self._lock:
            self._last = None  # Source changes and restart gaps are not observed time.
            self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        for day, values in self._buckets.items():
            batch = {
                "batch_id": str(uuid4()), "day": day,
                "monitoring_seconds": values[0], "analyzed_seconds": values[1],
                "cat_visible_seconds": values[2],
            }
            try:
                self._queue.put_nowait(batch)
            except Full:
                self.dropped_batches += 1
                self._log("⚠️ 每日統計佇列已滿，略過未保存批次；不影響影音或通知。")
        self._buckets.clear()
        self._last_flush = time.monotonic()

    def _run(self) -> None:
        while not self._stopping.is_set() or not self._queue.empty():
            try:
                batch = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self.store.record_monitoring_batch(**batch)
            except Exception as exc:  # noqa: BLE001 - isolated bounded SQLite failure
                self.failed_batches += 1
                self._log(f"⚠️ 每日統計批次未保存：{type(exc).__name__}；沒有補算缺失時間。")
            finally:
                self._queue.task_done()

    def close(self, *, wait_seconds: float = 5.0) -> bool:
        with self._lock:
            self._last = None
            self._flush_locked()
            self._stopping.set()
        self._worker.join(timeout=max(0.0, wait_seconds))
        complete = not self._worker.is_alive()
        if not complete:
            self._log("⚠️ 每日統計背景寫入尚未結束，未保存部分不列為已完成統計。")
        return complete

    def _log(self, message: str) -> None:
        try:
            self.logger(message)
        except Exception:  # noqa: BLE001 - logger cannot disrupt the media pipeline
            self.logger = lambda _message: None
