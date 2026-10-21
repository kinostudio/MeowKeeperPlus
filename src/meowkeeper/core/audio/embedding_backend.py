"""音訊模型選用契約；V0.2 預設關閉，避免捏造模型能力。"""

from __future__ import annotations

from .base import AudioObservation


class NullAudioEmbeddingBackend:
    """沒有音訊模型時的安全後端。"""

    model_version = "audio-disabled"
    backend_name = "disabled"

    def classify(self, observation: AudioObservation) -> dict[str, float]:
        return dict(observation.scores)


class YAMNetBackendUnavailable(RuntimeError):
    """YAMNet 尚未配置或權重未經核准。"""
