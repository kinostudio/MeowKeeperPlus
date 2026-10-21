"""非阻塞 Telegram 送出佇列；網路慢時不拖住 AI 主路徑。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future
from queue import Empty, Full, Queue
from typing import Any


class NotificationDispatcher:
    """單一背景工作者與有界通知佇列。"""

    def __init__(self, *, max_pending: int = 16, logger: Callable[[str], None] | None = None) -> None:
        if max_pending < 1:
            raise ValueError("通知佇列長度必須至少為 1。")
        self.max_pending = max_pending
        self.logger = logger
        self._queue: Queue[tuple[Callable[[], Any], Future[Any]]] = Queue(maxsize=max_pending)
        self._stopping = False
        self._worker = threading.Thread(
            target=self._run,
            name="meowkeeper-telegram",
            daemon=True,
        )
        self._worker.start()
        self.dropped = 0

    @property
    def pending_count(self) -> int:
        # Count queued AND executing work, including Future completion callbacks.
        # qsize() drops to zero as soon as the worker takes the last task.
        with self._queue.all_tasks_done:
            return self._queue.unfinished_tasks

    def submit(self, function: Callable[[], Any]) -> Future[Any]:
        if self._stopping:
            raise RuntimeError("通知背景工作者已停止。")
        future: Future[Any] = Future()
        try:
            self._queue.put_nowait((function, future))
        except Full:
            self.dropped += 1
            future.set_exception(RuntimeError("Telegram 通知佇列繁忙，已略過非必要通知。"))
        return future

    def shutdown(self, *, wait: bool = False) -> None:
        self._stopping = True
        if wait and self._worker is not threading.current_thread():
            self._worker.join(timeout=5.0)

    close = shutdown

    def _run(self) -> None:
        while not self._stopping or not self._queue.empty():
            try:
                function, future = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                result = function()
                if not future.done():
                    future.set_result(result)
            except Exception as exc:  # noqa: BLE001 - caller may inspect future
                if not future.done():
                    future.set_exception(exc)
                if self.logger is not None:
                    try:
                        self.logger(f"⚠️ Telegram 背景推播失敗：{type(exc).__name__}")
                    except Exception:  # noqa: BLE001 - diagnostics must not stop worker
                        self.logger = None
            finally:
                self._queue.task_done()
