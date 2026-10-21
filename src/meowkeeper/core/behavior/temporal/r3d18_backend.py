"""R3D18 scaffold；沒有核准 checkpoint 時保持停用。"""

from __future__ import annotations

from pathlib import Path

from .base import BehaviorClip, TemporalPrediction


class TemporalModelUnavailable(RuntimeError):
    """自訂時序 checkpoint 或 PyTorch/torchvision 尚未配置。"""


class R3D18Backend:
    """僅載入使用者明確提供的本地 checkpoint，不會自動下載。"""

    def __init__(self, checkpoint: str | Path | None = None, *, device: str = "cpu") -> None:
        if checkpoint is None:
            raise TemporalModelUnavailable("V0.2 預設使用規則行為分類器；尚未提供 R3D18 checkpoint。")
        path = Path(checkpoint)
        if not path.is_file():
            raise TemporalModelUnavailable(f"找不到 R3D18 checkpoint：{path}")
        try:
            import torch  # type: ignore[import-not-found]
            from torchvision.models.video import r3d_18  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TemporalModelUnavailable("R3D18 需要 PyTorch 與 torchvision。") from exc
        self._torch = torch
        self.device = device if device.startswith("cuda") and torch.cuda.is_available() else "cpu"
        self.model = r3d_18(weights=None)
        state = torch.load(path, map_location="cpu")
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        self.model.load_state_dict(state)
        self.model.to(self.device).eval()
        self.model_version = f"r3d18@{path.name}"
        self.backend_name = "PyTorch CUDA" if self.device.startswith("cuda") else "CPU"

    def predict(self, clip: BehaviorClip) -> TemporalPrediction:
        raise TemporalModelUnavailable(
            "R3D18 已載入但尚未配置行為 label mapping；目前請使用規則分類器。"
        )
