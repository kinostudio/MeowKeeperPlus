"""官方 DINOv2 ViT-S/14 embedding 後端；模型與套件採 lazy load。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from meowkeeper.runtime import (
    DEFAULT_IDENTITY_MODEL_RELEASE_TAG,
    identity_model_file,
    project_root,
    validate_identity_model_file,
)


def _prepare_dinov2_rgb(image: Any, cv2: Any, target_size: int = 518) -> Any:
    """保留原始長寬比縮放至 518×518，並以 ImageNet 均值中性色補邊，避免非正方形貓咪裁切拉伸變形。"""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("DINOv2 身分 crop 尺寸不合法。")
    if height == target_size and width == target_size:
        return rgb
    scale = min(target_size / float(width), target_size / float(height))
    new_w = max(1, min(target_size, round(width * scale)))
    new_h = max(1, min(target_size, round(height * scale)))
    interp = cv2.INTER_AREA if scale <= 1.0 else cv2.INTER_CUBIC
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=interp)
    if new_w == target_size and new_h == target_size:
        return resized
    pad_top = (target_size - new_h) // 2
    pad_bottom = target_size - new_h - pad_top
    pad_left = (target_size - new_w) // 2
    pad_right = target_size - new_w - pad_left
    return cv2.copyMakeBorder(
        resized,
        pad_top,
        pad_bottom,
        pad_left,
        pad_right,
        cv2.BORDER_CONSTANT,
        value=(124, 116, 104),
    )


class DINOv2Unavailable(RuntimeError):

    """DINOv2 權重、PyTorch 或官方 hub 程式目前不可用。"""


class DinoV2Embedder:
    """載入官方 DINOv2 ViT-S/14，輸出 L2-normalized CLS embedding。"""

    def __init__(
        self,
        model_path: str | Path | None = None,
        *,
        device: str | None = None,
        repo: str = "facebookresearch/dinov2",
    ) -> None:
        self.model_path = Path(model_path) if model_path else identity_model_file()
        try:
            validate_identity_model_file(self.model_path)
        except Exception as exc:
            raise DINOv2Unavailable(str(exc)) from exc
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DINOv2Unavailable("DINOv2 需要 PyTorch；目前沒有安裝。") from exc
        self._torch = torch
        cuda = bool(torch.cuda.is_available())
        self.device = device or ("cuda:0" if cuda else "cpu")
        if self.device.startswith("cuda") and not cuda:
            self.device = "cpu"
        try:
            # Keep the official hub source in the ignored project data directory;
            # some Windows profiles deny writes to the default roaming cache.
            cache_root = project_root() / "data" / "torch"
            cache_root.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("TORCH_HOME", str(cache_root))
            # The repository and checkpoint are both official DINOv2 sources. Load
            # the architecture without pretrained=True, then apply the verified
            # local checkpoint so torch hub cannot fetch an unknown weight file.
            pinned_repo = (
                repo
                if ":" in repo
                else f"{repo}:{DEFAULT_IDENTITY_MODEL_RELEASE_TAG.rsplit('@', 1)[-1]}"
            )
            model = torch.hub.load(
                pinned_repo,
                "dinov2_vits14",
                source="github",
                pretrained=False,
            )
            checkpoint = torch.load(self.model_path, map_location="cpu")
            if isinstance(checkpoint, dict):
                for key in ("teacher", "student", "state_dict"):
                    value = checkpoint.get(key)
                    if isinstance(value, dict):
                        checkpoint = value
                        break
            if isinstance(checkpoint, dict):
                checkpoint = {
                    str(key).removeprefix("module.").removeprefix("backbone."): value
                    for key, value in checkpoint.items()
                }
            missing, unexpected = model.load_state_dict(checkpoint, strict=False)
            if len(missing) > 20:
                raise RuntimeError(f"DINOv2 checkpoint 欄位不完整：missing={len(missing)}")
            if len(unexpected) > 20:
                raise RuntimeError(f"DINOv2 checkpoint 欄位不相容：unexpected={len(unexpected)}")
            self.model = model.to(self.device).eval()
        except Exception as exc:
            raise DINOv2Unavailable(
                "DINOv2 官方程式載入失敗；請確認官方 hub 程式可用且未被防火牆阻擋。"
            ) from exc
        self.model_version = f"dinov2_vits14@{DEFAULT_IDENTITY_MODEL_RELEASE_TAG}"
        self.backend_name = "PyTorch CUDA" if self.device.startswith("cuda") else "CPU"
        self.last_latency_ms: float | None = None

    def embed(self, crop: bytes | Any) -> Any:
        with _shared_lock:
            return self._embed_locked(crop)

    def _embed_locked(self, crop: bytes | Any) -> Any:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DINOv2Unavailable("DINOv2 影像前處理需要 opencv-python 與 numpy。") from exc
        if isinstance(crop, (bytes, bytearray, memoryview)):
            image = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
        else:
            image = crop
        if image is None:
            raise ValueError("無法解碼 DINOv2 身分 crop。")
        image = _prepare_dinov2_rgb(image, cv2, target_size=518)
        tensor = self._torch.from_numpy(image).permute(2, 0, 1).float() / 255.0
        mean = self._torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = self._torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        tensor = ((tensor - mean) / std).unsqueeze(0).to(self.device)
        started = time.perf_counter()
        with self._torch.inference_mode():
            features = self.model(tensor)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        if isinstance(features, dict):
            if "x_norm_clstoken" in features:
                features = features["x_norm_clstoken"]
            else:
                features = features.get("x_norm_patchtokens")
        if getattr(features, "ndim", 0) == 3:
            features = features.mean(dim=1)
        features = features.reshape(features.shape[0], -1)
        features = self._torch.nn.functional.normalize(features, dim=1)
        return features[0].detach().cpu().numpy().astype(np.float32)


import threading
import weakref

_shared_lock = threading.RLock()
_shared_backends = weakref.WeakValueDictionary()


def shared_cpu_embedder():
    """同一 Host 的建特徵、單張測試與背景身分共用一個 CPU 模型。"""
    with _shared_lock:
        backend = _shared_backends.get(DinoV2Embedder)
        if backend is None:
            backend = DinoV2Embedder(device="cpu")
            _shared_backends[DinoV2Embedder] = backend
        return backend
