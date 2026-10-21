"""MX250 執行期間的低成本效能計數器。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    frames_seen: int = 0
    dropped_frames: int = 0
    ai_frames_dropped: int = 0
    yolo_inferences: int = 0
    yolo_latency_ms_total: float = 0.0
    last_yolo_backend: str | None = None
    identity_inferences: int = 0
    identity_requests: int = 0
    identity_latency_ms_total: float = 0.0
    last_identity_backend: str | None = None
    unknown_identity_tracks: int = 0
    behavior_observations: int = 0
    behavior_events_created: int = 0
    notifications_queued: int = 0
    notifications_dropped: int = 0
    peak_vram_mib: int | None = None

    def record_frame(self, *, dropped: bool = False) -> None:
        self.frames_seen += 1
        if dropped:
            self.dropped_frames += 1

    def record_identity(
        self,
        *,
        latency_ms: float | None,
        backend: str | None,
        count: bool = True,
    ) -> None:
        if count:
            self.identity_inferences += 1
            self.identity_requests += 1
        if latency_ms is not None:
            self.identity_latency_ms_total += max(0.0, latency_ms)
        self.last_identity_backend = backend

    def record_yolo(self, *, latency_ms: float | None = None, backend: str | None = None) -> None:
        self.yolo_inferences += 1
        self.last_yolo_backend = backend
        if latency_ms is not None:
            self.yolo_latency_ms_total += max(0.0, float(latency_ms))

    def record_behavior(self, *, event_created: bool = False) -> None:
        self.behavior_observations += 1
        if event_created:
            self.behavior_events_created += 1

    @property
    def average_identity_latency_ms(self) -> float | None:
        if self.identity_inferences <= 0:
            return None
        return self.identity_latency_ms_total / self.identity_inferences

    def as_dict(self) -> dict[str, int | float | str | None]:
        return {
            "frames_seen": self.frames_seen,
            "dropped_frames": self.dropped_frames,
            "ai_frames_dropped": self.ai_frames_dropped,
            "yolo_inferences": self.yolo_inferences,
            "yolo_latency_ms_total": round(self.yolo_latency_ms_total, 3),
            "average_yolo_latency_ms": (
                round(self.yolo_latency_ms_total / self.yolo_inferences, 3)
                if self.yolo_inferences
                else None
            ),
            "last_yolo_backend": self.last_yolo_backend,
            "identity_inferences": self.identity_inferences,
            "identity_requests": self.identity_requests,
            "identity_latency_ms_total": round(self.identity_latency_ms_total, 3),
            "average_identity_latency_ms": (
                round(self.average_identity_latency_ms, 3)
                if self.average_identity_latency_ms is not None
                else None
            ),
            "last_identity_backend": self.last_identity_backend,
            "unknown_identity_tracks": self.unknown_identity_tracks,
            "behavior_observations": self.behavior_observations,
            "behavior_events_created": self.behavior_events_created,
            "notifications_queued": self.notifications_queued,
            "notifications_dropped": self.notifications_dropped,
            "peak_vram_mib": self.peak_vram_mib,
        }
