"""疑似乾嘔／嘔吐候選事件；不宣稱醫療診斷。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .audiovisual_fusion import AudioVisualFusion, FusionEvidence

ALLOWED_REVIEW_LABELS = frozenset({"retching", "vomiting", "false_positive", "other"})


@dataclass(frozen=True)
class RetchingCandidate:
    is_candidate: bool
    label: str
    confidence: float
    observed_at: datetime
    evidence: FusionEvidence
    message_zh_tw: str


class RetchingCandidateClassifier:
    def __init__(self, fusion: AudioVisualFusion | None = None) -> None:
        self.fusion = fusion or AudioVisualFusion()

    def classify(
        self,
        *,
        observed_at: datetime,
        audio_score: float,
        visual_score: float,
        temporal_consistency: float = 0.0,
        identity_confidence: float = 0.0,
        motion_score: float = 0.0,
    ) -> RetchingCandidate:
        evidence = FusionEvidence(
            audio_score=audio_score,
            visual_score=visual_score,
            temporal_consistency=temporal_consistency,
            identity_confidence=identity_confidence,
            motion_score=motion_score,
        )
        candidate = self.fusion.evaluate(evidence)
        return RetchingCandidate(
            is_candidate=candidate,
            label="possible_vomit" if candidate else "no_candidate",
            confidence=evidence.fused_score,
            observed_at=observed_at,
            evidence=evidence,
            message_zh_tw=(
                "疑似乾嘔／嘔吐事件，請人工確認。"
                if candidate
                else "目前證據不足，未建立疑似乾嘔候選。"
            ),
        )

    @staticmethod
    def validate_review_label(label: str) -> str:
        """限制人工回饋字典；`vomiting` 仍只是使用者標註，不是模型診斷。"""

        normalized = label.strip().lower()
        if normalized not in ALLOWED_REVIEW_LABELS:
            raise ValueError("音視事件標註必須是 retching、vomiting、false_positive 或 other。")
        return normalized
