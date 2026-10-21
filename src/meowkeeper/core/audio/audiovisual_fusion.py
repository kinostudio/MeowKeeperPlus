"""音訊、視覺與時間一致性的保守融合。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FusionEvidence:
    audio_score: float
    visual_score: float
    temporal_consistency: float
    identity_confidence: float
    motion_score: float

    @property
    def fused_score(self) -> float:
        return max(
            0.0,
            min(
                1.0,
                0.35 * self.audio_score
                + 0.35 * self.visual_score
                + 0.15 * self.temporal_consistency
                + 0.10 * self.identity_confidence
                + 0.05 * self.motion_score,
            ),
        )


class AudioVisualFusion:
    def __init__(self, *, minimum_audio: float = 0.55, minimum_visual: float = 0.45, minimum_fused: float = 0.55) -> None:
        self.minimum_audio = minimum_audio
        self.minimum_visual = minimum_visual
        self.minimum_fused = minimum_fused

    def evaluate(self, evidence: FusionEvidence) -> bool:
        """只有音訊與視覺都達到最低證據才產生疑似候選。"""

        return (
            evidence.audio_score >= self.minimum_audio
            and evidence.visual_score >= self.minimum_visual
            and evidence.fused_score >= self.minimum_fused
        )
