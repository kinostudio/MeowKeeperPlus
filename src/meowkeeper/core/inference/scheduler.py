"""單一 GPU 推論工作者與有界優先佇列。

這個元件不保證特定 GPU 型號的效能；它只保證低優先工作不能無限堆積，且
同一程序同一時間只由一個工作者執行模型函式。影像接收與環形錄影仍在呼叫端
的媒體執行緒中，不會把原始影音塞進此佇列。
"""

from __future__ import annotations

import heapq
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .metrics import InferenceMetrics
from .priority import InferencePriority
from .timings import StageTimings


class InferenceQueueFull(RuntimeError):
    """佇列已滿且沒有可安全淘汰的低優先工作。"""


class InferenceDeferred(RuntimeError):
    """資源狀態要求延後低優先推論。"""


@dataclass
class InferenceTask:
    task_id: str
    priority: InferencePriority
    function: Callable[[], Any]
    track_id: str | int | None = None
    dedupe_key: str | None = None
    timeout_seconds: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = 0
    future: Future[Any] = field(default_factory=Future, repr=False)
    cancelled: bool = False
    started: bool = False
    created_monotonic: float = field(default_factory=time.perf_counter)


class GPUInferenceScheduler:
    """有界、單工作者、可去重的推論排程器。"""

    def __init__(
        self,
        *,
        max_pending_tasks: int = 8,
        worker_name: str = "meowkeeper-gpu-inference",
        resource_monitor: Any | None = None,
        logger: Callable[[str], None] | None = None,
        auto_start: bool = True,
    ) -> None:
        if max_pending_tasks < 1:
            raise ValueError("推論佇列長度必須至少為 1。")
        self.max_pending_tasks = max_pending_tasks
        self.resource_monitor = resource_monitor
        self.logger = logger
        self.metrics = InferenceMetrics()
        self.timings = StageTimings()
        self._condition = threading.Condition()
        self._queue: list[tuple[int, int, InferenceTask]] = []
        self._pending: dict[str, InferenceTask] = {}
        self._dedupe: dict[str, Future[Any]] = {}
        self._sequence = 0
        self._stopping = False
        self._worker: threading.Thread | None = None
        self._worker_ident: int | None = None
        if auto_start:
            self.start(worker_name)

    def start(self, worker_name: str = "meowkeeper-gpu-inference") -> None:
        with self._condition:
            self._start_locked(worker_name)

    def _start_locked(self, worker_name: str) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stopping = False
        self._worker = threading.Thread(target=self._worker_loop, name=worker_name, daemon=True)
        self._worker.start()

    @property
    def pending_count(self) -> int:
        with self._condition:
            return len(self._pending)

    @property
    def is_running(self) -> bool:
        return bool(self._worker and self._worker.is_alive())

    def submit(
        self,
        function: Callable[[], Any],
        *,
        priority: InferencePriority = InferencePriority.NORMAL,
        track_id: str | int | None = None,
        dedupe_key: str | None = None,
        timeout_seconds: float | None = None,
        task_id: str | None = None,
    ) -> Future[Any]:
        """排入工作；低優先工作在壅塞時可安全淘汰。"""

        if not callable(function):
            raise TypeError("推論工作必須是可呼叫函式。")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("推論逾時秒數必須大於零。")
        priority = InferencePriority(priority)
        with self._condition:
            if self._stopping:
                self._start_locked("meowkeeper-gpu-inference")
            if dedupe_key and dedupe_key in self._dedupe:
                existing = self._dedupe[dedupe_key]
                if not existing.done():
                    return existing
                self._dedupe.pop(dedupe_key, None)

            self._sequence += 1
            task = InferenceTask(
                task_id=task_id or str(uuid.uuid4()),
                priority=priority,
                function=function,
                track_id=track_id,
                dedupe_key=dedupe_key,
                timeout_seconds=timeout_seconds,
                sequence=self._sequence,
            )
            if len(self._pending) >= self.max_pending_tasks and not self._make_room_for(task):
                self.metrics.record_dropped(priority)
                task.future.set_exception(InferenceQueueFull("推論佇列繁忙，已略過低優先工作。"))
                self._log("⚠️ GPU 佇列繁忙，已略過一筆低優先推論工作。")
                return task.future
            self._pending[task.task_id] = task
            task.future.task_id = task.task_id  # type: ignore[attr-defined]
            if dedupe_key:
                self._dedupe[dedupe_key] = task.future
            heapq.heappush(self._queue, (int(priority), task.sequence, task))
            self.metrics.record_submitted(priority, len(self._pending))
            if self.resource_monitor is not None and hasattr(self.resource_monitor, "update_queue_length"):
                self.resource_monitor.update_queue_length(len(self._pending))
            self._condition.notify()
            return task.future

    def run_sync(
        self,
        function: Callable[[], Any],
        *,
        priority: InferencePriority = InferencePriority.HIGH,
        timeout_seconds: float | None = None,
    ) -> Any:
        """以統一排程器執行必須回傳結果的高優先工作。"""

        if threading.get_ident() == self._worker_ident:
            return function()
        future = self.submit(function, priority=priority, timeout_seconds=timeout_seconds)
        return future.result(timeout=timeout_seconds)

    def cancel(self, task_id: str) -> bool:
        with self._condition:
            task = self._pending.get(task_id)
            if task is None or task.started:
                return False
            task.cancelled = True
            self._pending.pop(task_id, None)
            if task.dedupe_key:
                self._dedupe.pop(task.dedupe_key, None)
            self.metrics.cancelled += 1
            if not task.future.done():
                task.future.cancel()
            self._condition.notify()
            return True

    def shutdown(self, *, wait: bool = False) -> None:
        with self._condition:
            self._stopping = True
            for task in list(self._pending.values()):
                task.cancelled = True
                if not task.future.done():
                    task.future.cancel()
            self._pending.clear()
            self._dedupe.clear()
            self._condition.notify_all()
            worker = self._worker
        if wait and worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5.0)

    close = shutdown

    def _make_room_for(self, incoming: InferenceTask) -> bool:
        candidates = [task for task in self._pending.values() if not task.started]
        if not candidates:
            return False
        victim = max(candidates, key=lambda item: (int(item.priority), -item.sequence))
        if int(victim.priority) <= int(incoming.priority):
            return False
        victim.cancelled = True
        self._pending.pop(victim.task_id, None)
        if victim.dedupe_key:
            self._dedupe.pop(victim.dedupe_key, None)
        self.metrics.record_dropped(victim.priority)
        if not victim.future.done():
            victim.future.set_exception(InferenceDeferred("低優先推論因高優先工作而延後。"))
        self._log("ℹ️ GPU 忙碌，已延後一筆低優先身分／背景工作。")
        return True

    def _worker_loop(self) -> None:
        self._worker_ident = threading.get_ident()
        try:
            while True:
                with self._condition:
                    while not self._queue and not self._stopping:
                        self._condition.wait(timeout=0.5)
                    if self._stopping and not self._queue:
                        return
                    _priority, _sequence, task = heapq.heappop(self._queue)
                    self._pending.pop(task.task_id, None)
                    if self.resource_monitor is not None and hasattr(self.resource_monitor, "update_queue_length"):
                        self.resource_monitor.update_queue_length(len(self._pending))
                    if task.cancelled or task.future.cancelled():
                        self._clear_dedupe(task)
                        continue
                    task.started = True

                monitor = self.resource_monitor
                if monitor is not None and hasattr(monitor, "allow"):
                    try:
                        allowed = bool(monitor.allow(task.priority))
                    except Exception:  # noqa: BLE001 - monitor is optional and diagnostic
                        allowed = True
                    if not allowed and task.priority >= InferencePriority.LOW:
                        self.metrics.record_dropped(task.priority)
                        if not task.future.done():
                            task.future.set_exception(
                                InferenceDeferred("GPU 負載過高，身分／背景工作暫時延後。")
                            )
                        self._log("⚠️ GPU 負載過高，暫停低優先身分工作。")
                        self._clear_dedupe(task)
                        continue

                started = time.perf_counter()
                stage = task.priority.name.lower()
                self.timings.observe({stage + "_queue_wait_ms": max(0, started - task.created_monotonic) * 1000})
                try:
                    result = task.function()
                    elapsed = time.perf_counter() - started
                    if task.timeout_seconds is not None and elapsed > task.timeout_seconds and result is None:
                        self.metrics.timed_out += 1
                        if not task.future.done():
                            task.future.set_exception(TimeoutError("推論工作超過允許時間。"))
                    elif not task.future.done():
                        task.future.set_result(result)
                        self.metrics.record_completed(task.priority, elapsed * 1000.0)
                        if monitor is not None and hasattr(monitor, "record_success"):
                            monitor.record_success()
                except Exception as exc:  # noqa: BLE001 - future carries the typed error
                    self.metrics.failed += 1
                    self.metrics.last_error = type(exc).__name__
                    if monitor is not None and hasattr(monitor, "record_error"):
                        monitor.record_error(exc)
                    if not task.future.done():
                        task.future.set_exception(exc)
                finally:
                    self.timings.observe({stage + "_task_ms": max(0, time.perf_counter() - started) * 1000})
                    self._clear_dedupe(task)
        finally:
            self._worker_ident = None

    def _clear_dedupe(self, task: InferenceTask) -> None:
        if task.dedupe_key:
            with self._condition:
                if self._dedupe.get(task.dedupe_key) is task.future:
                    self._dedupe.pop(task.dedupe_key, None)

    def _log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger(message)
            except Exception:  # noqa: BLE001 - diagnostics must not stop inference
                return
