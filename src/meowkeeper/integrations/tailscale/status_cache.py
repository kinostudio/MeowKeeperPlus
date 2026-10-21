"""Single-flight, short-lived cache for local Tailscale status reads."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any


class TailscaleStatusCache:
    """Coalesce concurrent Dashboard polls into one bounded read-only CLI call."""

    def __init__(
        self,
        loader: Callable[[], Mapping[str, Any]],
        *,
        ttl_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at = float("-inf")
        self._value: dict[str, Any] | None = None

    def get(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if self._value is not None and now - self._checked_at < self._ttl_seconds:
                return dict(self._value)
            value = dict(self._loader())
            self._checked_at = now
            self._value = value
            return dict(value)
