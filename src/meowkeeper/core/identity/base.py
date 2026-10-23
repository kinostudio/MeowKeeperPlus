"""多貓身分辨識的可替換資料契約。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class IdentityThresholds:
    """身分門檻；初始值是可校正起點，不代表真實準確率。"""

    accepted_threshold: float = 0.62
    unknown_threshold: float = 0.45
    # Lower only the candidate separation gate. Similarity, unknown and
    # temporal-confirmation thresholds remain unchanged; identity is still
    # experimental until household validation.
    minimum_margin: float = 0.05
    temporal_vote_window: int = 5
    minimum_valid_samples: int = 2
    top_k_embeddings: int = 3
    embedding_weight: float = 0.85
    color_weight: float = 0.15
    minimum_unique_frames: int = 2
    minimum_time_bins: int = 1
    minimum_vote_fraction: float = 0.70

    def validate(self) -> None:
        if not 0.0 <= self.unknown_threshold <= self.accepted_threshold <= 1.0:
            raise ValueError("身分門檻必須滿足 0 <= unknown_threshold <= accepted_threshold <= 1。")
        if self.minimum_margin < 0.0 or self.minimum_margin > 1.0:
            raise ValueError("身分最低差距必須介於 0 到 1。")
        if self.temporal_vote_window < 1:
            raise ValueError("身分投票視窗必須至少為 1。")
        if self.minimum_valid_samples < 1 or self.minimum_valid_samples > self.temporal_vote_window:
            raise ValueError("身分有效樣本數不可超過投票視窗。")
        if self.top_k_embeddings < 1:
            raise ValueError("身分比對的 top-k 必須至少為 1。")
        if self.embedding_weight < 0 or self.color_weight < 0:
            raise ValueError("身分特徵權重不可小於零。")
        if self.embedding_weight + self.color_weight <= 0:
            raise ValueError("身分特徵至少需要一個非零權重。")
        if self.minimum_unique_frames < 1:
            raise ValueError("身分最少獨立影格數必須至少為 1。")
        if self.minimum_time_bins < 1:
            raise ValueError("身分最少時間區間數必須至少為 1。")
        if not 0.0 < self.minimum_vote_fraction <= 1.0:
            raise ValueError("身分最低得票率必須介於 0 到 1。")


@dataclass(frozen=True)
class IdentityPrediction:
    """單次或時間平滑後的身分結果。"""

    predicted_pet_id: str | None
    predicted_name: str | None
    similarity: float
    second_best_similarity: float
    confidence_margin: float
    unknown_probability: float
    model_version: str
    track_id: str | int | None = None
    stable: bool = False
    reason: str = ""
    candidates: tuple[dict[str, Any], ...] = ()
    unknown_reason: str | None = None
    valid_frames: int = 0
    vote_fraction: float = 0.0
    evidence_seconds: float = 0.0
    posture: Any | None = None

    @property
    def is_unknown(self) -> bool:
        return self.predicted_pet_id is None


class EmbeddingBackend(Protocol):
    """將貓咪 crop 轉為 L2-normalized 向量。"""

    @property
    def model_version(self) -> str:
        ...

    @property
    def backend_name(self) -> str:
        ...

    def embed(self, crop: bytes | Any) -> Any:
        ...
