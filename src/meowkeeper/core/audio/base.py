"""共用音訊觀測與可選模型契約；不建立第二條攝影機串流。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AudioObservation:
    captured_at: datetime
    source_id: str
    duration_seconds: float
    scores: dict[str, float] = field(default_factory=dict)
    sample_rate: int | None = None


class AudioEmbeddingBackend(Protocol):
    @property
    def model_version(self) -> str:
        ...

    @property
    def backend_name(self) -> str:
        ...

    def classify(self, observation: AudioObservation) -> dict[str, float]:
        ...
