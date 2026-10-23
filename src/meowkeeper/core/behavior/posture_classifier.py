"""輕量靜態貓咪姿態分類器 (Lightweight Static Cat Posture Classifier)

主要架構：DINOv2 ViT-S/14 特徵 (384 維) + Linear／MLP 分類頭
目標類別：坐著 (sitting)／站立 (standing)／趴躺 (lying)／未知 (unknown)
運算方式：低頻、條件觸發，支援 PyTorch 與純 NumPy 零延遲推論 (< 0.1ms)。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np


POSTURE_CLASSES = ("sitting", "standing", "lying", "unknown")

POSTURE_LABELS_ZH: dict[str, str] = {
    "sitting": "坐著",
    "standing": "站立",
    "lying": "趴躺",
    "unknown": "未知",
}


@dataclass(frozen=True)
class PosturePrediction:
    """姿態預測結果。"""

    posture: str
    confidence: float
    probabilities: dict[str, float]
    label_zh: str
    model_version: str
    latency_ms: float = 0.0

    @property
    def is_known(self) -> bool:
        return self.posture in {"sitting", "standing", "lying"} and self.confidence > 0.0


def _softmax(x: np.ndarray) -> np.ndarray:
    """數值穩定的 Softmax 運算。"""
    shifted = x - np.max(x)
    exp = np.exp(shifted)
    sum_exp = np.sum(exp)
    if sum_exp <= 0:
        return np.ones_like(x) / len(x)
    return exp / sum_exp


class PostureMLP:
    """PyTorch MLP 模型定義（若有 PyTorch 則使用，若無亦可透過 NumPy 矩陣推論）。"""

    def __init__(self, in_features: int = 384, hidden_dim: int = 128, num_classes: int = 4):
        self.in_features = in_features
        self.hidden_dim = hidden_dim
        self.num_classes = num_classes

    @staticmethod
    def build_torch_module(in_features: int = 384, hidden_dim: int = 128, num_classes: int = 4) -> Any:
        import torch
        import torch.nn as nn

        class _Net(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.fc1 = nn.Linear(in_features, hidden_dim)
                self.norm1 = nn.LayerNorm(hidden_dim)
                self.relu = nn.ReLU()
                self.dropout = nn.Dropout(0.1)
                self.fc2 = nn.Linear(hidden_dim, num_classes)

            def forward(self, x: torch.Tensor) -> torch.Tensor:
                x = self.fc1(x)
                x = self.norm1(x)
                x = self.relu(x)
                x = self.dropout(x)
                return self.fc2(x)

        return _Net()


class PostureClassifier:
    """姿態分類器推論引擎。

    支援加載 .pth 權重或直接以 NumPy 矩陣推論，確保即便在無 GPU 或 CPU 忙碌時也能以 < 0.1ms 完成。
    """

    DEFAULT_VERSION = "cat_posture_mlp@v1.0.0"

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        confidence_threshold: float = 0.45,
        version: str | None = None,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.model_version = version or self.DEFAULT_VERSION
        self.model_path = Path(model_path) if model_path else None

        # 權重矩陣（NumPy 格式，推論零開銷）
        self.w1: np.ndarray | None = None  # (384, 128)
        self.b1: np.ndarray | None = None  # (128,)
        self.ln_gamma: np.ndarray | None = None  # (128,)
        self.ln_beta: np.ndarray | None = None  # (128,)
        self.w2: np.ndarray | None = None  # (128, 4)
        self.b2: np.ndarray | None = None  # (4,)

        self.last_latency_ms: float = 0.0

        if self.model_path and self.model_path.is_file():
            self.load_weights(self.model_path)

    def load_weights(self, path: str | Path) -> bool:
        """載入權重檔（支援 PyTorch state_dict 或 npz 檔案）。"""
        target = Path(path)
        if not target.is_file():
            return False

        try:
            if target.suffix == ".npz":
                data = np.load(target)
                self.w1 = data["w1"]
                self.b1 = data["b1"]
                self.ln_gamma = data.get("ln_gamma", np.ones(self.w1.shape[1], dtype=np.float32))
                self.ln_beta = data.get("ln_beta", np.zeros(self.w1.shape[1], dtype=np.float32))
                self.w2 = data["w2"]
                self.b2 = data["b2"]
                return True

            import torch

            state = torch.load(target, map_location="cpu")
            if isinstance(state, dict):
                # 提取 state_dict
                if "state_dict" in state:
                    state = state["state_dict"]

                def get_np(key: str) -> np.ndarray:
                    t = state[key]
                    if hasattr(t, "detach"):
                        return t.detach().cpu().numpy()
                    return np.asarray(t)

                self.w1 = get_np("fc1.weight").T  # (128, 384) -> (384, 128)
                self.b1 = get_np("fc1.bias")
                self.ln_gamma = get_np("norm1.weight") if "norm1.weight" in state else np.ones(self.w1.shape[1], dtype=np.float32)
                self.ln_beta = get_np("norm1.bias") if "norm1.bias" in state else np.zeros(self.w1.shape[1], dtype=np.float32)
                self.w2 = get_np("fc2.weight").T  # (4, 128) -> (128, 4)
                self.b2 = get_np("fc2.bias")
                return True
        except Exception:
            return False
        return False

    def save_weights(self, path: str | Path) -> None:
        """保存當前權重至 PyTorch 相容的 state_dict 格式。"""
        import torch

        if self.w1 is None or self.b1 is None or self.w2 is None or self.b2 is None:
            raise ValueError("無權重可保存。")

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "fc1.weight": torch.from_numpy(self.w1.T).float(),
            "fc1.bias": torch.from_numpy(self.b1).float(),
            "norm1.weight": torch.from_numpy(self.ln_gamma if self.ln_gamma is not None else np.ones(self.w1.shape[1], dtype=np.float32)).float(),
            "norm1.bias": torch.from_numpy(self.ln_beta if self.ln_beta is not None else np.zeros(self.w1.shape[1], dtype=np.float32)).float(),
            "fc2.weight": torch.from_numpy(self.w2.T).float(),
            "fc2.bias": torch.from_numpy(self.b2).float(),
            "model_version": self.model_version,
            "classes": list(POSTURE_CLASSES),
        }
        torch.save(state, target)

    def set_weights_numpy(
        self,
        w1: np.ndarray,
        b1: np.ndarray,
        w2: np.ndarray,
        b2: np.ndarray,
        ln_gamma: np.ndarray | None = None,
        ln_beta: np.ndarray | None = None,
    ) -> None:
        """直接設置 NumPy 權重。"""
        self.w1 = w1.astype(np.float32)
        self.b1 = b1.astype(np.float32)
        self.w2 = w2.astype(np.float32)
        self.b2 = b2.astype(np.float32)
        self.ln_gamma = ln_gamma.astype(np.float32) if ln_gamma is not None else np.ones(w1.shape[1], dtype=np.float32)
        self.ln_beta = ln_beta.astype(np.float32) if ln_beta is not None else np.zeros(w1.shape[1], dtype=np.float32)

    def is_ready(self) -> bool:
        """檢查分類器權重是否已載入就緒。"""
        return self.w1 is not None and self.w2 is not None

    def predict_vector(self, vector: np.ndarray | Any) -> PosturePrediction:
        """從 384 維 DINOv2 向量進行極速姿態推論 (< 0.1ms)。"""
        t0 = time.perf_counter()

        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        if vec.size != 384 or not np.isfinite(vec).all():
            return PosturePrediction(
                posture="unknown",
                confidence=0.0,
                probabilities={c: 0.25 for c in POSTURE_CLASSES},
                label_zh=POSTURE_LABELS_ZH["unknown"],
                model_version=self.model_version,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # 確保 L2 正規化
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm

        if not self.is_ready():
            return PosturePrediction(
                posture="unknown",
                confidence=0.0,
                probabilities={c: 0.25 for c in POSTURE_CLASSES},
                label_zh=POSTURE_LABELS_ZH["unknown"],
                model_version=self.model_version,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        # MLP 前向推論:
        # h = LayerNorm(x @ W1 + b1)
        h = np.dot(vec, self.w1) + self.b1
        # LayerNorm
        mean = np.mean(h)
        var = np.var(h)
        h_norm = (h - mean) / np.sqrt(var + 1e-5)
        if self.ln_gamma is not None and self.ln_beta is not None:
            h_norm = h_norm * self.ln_gamma + self.ln_beta
        # ReLU
        h_act = np.maximum(0.0, h_norm)
        # Logits
        logits = np.dot(h_act, self.w2) + self.b2

        probs = _softmax(logits)
        pred_idx = int(np.argmax(probs))
        top_prob = float(probs[pred_idx])
        pred_class = POSTURE_CLASSES[pred_idx]

        prob_dict = {cls_name: float(probs[i]) for i, cls_name in enumerate(POSTURE_CLASSES)}

        # 門檻檢查：若信心度不足或預測為 unknown 類別，輸出 unknown
        if top_prob < self.confidence_threshold or pred_class == "unknown":
            decision = "unknown"
            conf = top_prob if pred_class == "unknown" else 0.0
        else:
            decision = pred_class
            conf = top_prob

        latency = (time.perf_counter() - t0) * 1000.0
        self.last_latency_ms = latency

        return PosturePrediction(
            posture=decision,
            confidence=conf,
            probabilities=prob_dict,
            label_zh=POSTURE_LABELS_ZH.get(decision, "未知"),
            model_version=self.model_version,
            latency_ms=latency,
        )

    def predict_crop(self, crop: bytes | Any, embedder: Any) -> PosturePrediction:
        """從影像 crop 透過 embedder 提取特徵後推論。"""
        if embedder is None:
            return PosturePrediction(
                posture="unknown",
                confidence=0.0,
                probabilities={c: 0.25 for c in POSTURE_CLASSES},
                label_zh=POSTURE_LABELS_ZH["unknown"],
                model_version=self.model_version,
            )

        try:
            vec = embedder.embed(crop)
            return self.predict_vector(vec)
        except Exception:
            return PosturePrediction(
                posture="unknown",
                confidence=0.0,
                probabilities={c: 0.25 for c in POSTURE_CLASSES},
                label_zh=POSTURE_LABELS_ZH["unknown"],
                model_version=self.model_version,
            )
