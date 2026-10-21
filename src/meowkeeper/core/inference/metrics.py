"""推論排程的低成本統計；不逐影格寫入資料庫。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .priority import InferencePriority


@dataclass
class InferenceMetrics:
    submitted: int = 0
    completed: int = 0
    dropped: int = 0
    timed_out: int = 0
    cancelled: int = 0
    failed: int = 0
    queue_peak: int = 0
    latency_ms_total: float = 0.0
    priority_submitted: Counter[str] = field(default_factory=Counter)
    priority_completed: Counter[str] = field(default_factory=Counter)
    priority_dropped: Counter[str] = field(default_factory=Counter)
    last_error: str | None = None

    def record_submitted(self, priority: InferencePriority, queue_size: int) -> None:
        self.submitted += 1
        self.priority_submitted[priority.name] += 1
        self.queue_peak = max(self.queue_peak, queue_size)

    def record_completed(self, priority: InferencePriority, latency_ms: float) -> None:
        self.completed += 1
        self.priority_completed[priority.name] += 1
        self.latency_ms_total += max(0.0, float(latency_ms))

    def record_dropped(self, priority: InferencePriority) -> None:
        self.dropped += 1
        self.priority_dropped[priority.name] += 1

    @property
    def average_latency_ms(self) -> float | None:
        return self.latency_ms_total / self.completed if self.completed else None

    def as_dict(self) -> dict[str, object]:
        return {
            "submitted": self.submitted,
            "completed": self.completed,
            "dropped": self.dropped,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "failed": self.failed,
            "queue_peak": self.queue_peak,
            "average_latency_ms": (
                round(self.average_latency_ms, 3) if self.average_latency_ms is not None else None
            ),
            "priority_submitted": dict(self.priority_submitted),
            "priority_completed": dict(self.priority_completed),
            "priority_dropped": dict(self.priority_dropped),
            "last_error": self.last_error,
        }
