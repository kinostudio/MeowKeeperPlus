"""可選時序行為模型的介面；預設仍使用規則分類器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meowkeeper.domain.behavior import BehaviorState


@dataclass(frozen=True)
class BehaviorClip:
    frames: tuple[bytes, ...]
    started_at: datetime
    source_id: str
    pet_id: str | None = None


@dataclass(frozen=True)
class TemporalPrediction:
    behavior_state: BehaviorState
    confidence: float
    model_version: str
    backend_name: str


class TemporalBehaviorBackend(Protocol):
    @property
    def model_version(self) -> str:
        ...

    @property
    def backend_name(self) -> str:
        ...

    def predict(self, clip: BehaviorClip) -> TemporalPrediction:
        ...
