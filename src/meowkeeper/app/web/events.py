"""低頻、可界限的 SSE 狀態事件匯流排。"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any


class EventHub:
    """每個訂閱者有獨立有界佇列；慢客戶端不會阻塞核心。"""

    def __init__(self, *, max_pending_per_client: int = 32) -> None:
        if max_pending_per_client < 1:
            raise ValueError("SSE 每客戶端佇列至少需要一格。")
        self.max_pending_per_client = max_pending_per_client
        self._clients: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.RLock()
        self.dropped = 0

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, event: str, data: Mapping[str, Any]) -> None:
        item = {"event": str(event), "data": dict(data), "published_at": time.time()}
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.put_nowait(item)
            except queue.Full:
                self.dropped += 1
                try:
                    client.get_nowait()
                    client.put_nowait(item)
                except (queue.Empty, queue.Full):
                    pass

    def stream(self, *, heartbeat_seconds: float = 15.0) -> Iterator[str]:
        client: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_pending_per_client)
        with self._lock:
            self._clients.add(client)
        try:
            yield ": meowkeeper sse connected\n\n"
            while True:
                try:
                    item = client.get(timeout=max(0.5, heartbeat_seconds))
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield (
                    f"event: {item['event']}\n"
                    f"data: {json.dumps(item['data'], ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
        finally:
            with self._lock:
                self._clients.discard(client)
