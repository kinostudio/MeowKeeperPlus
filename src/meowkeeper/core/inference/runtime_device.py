"""CUDA 推論故障時只降級一次；不掩蓋 CPU／資料錯誤。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def run_with_cpu_fallback(
    owner: Any, operation: Callable[..., Any], kwargs: dict[str, Any]
) -> Any:
    """同一影格在 GPU 故障後重試 CPU，後續保持 CPU 避免重啟迴圈。

    owner 提供 device、inference_backend 與 _model。重建 predictor 會
    失去短期 track ID；永久身分不得依賴這些 ID。
    """
    owner.last_cpu_fallback_ms = None
    if isinstance(getattr(owner._model, "backend", None), str):
        try:
            return operation(**kwargs)
        finally:
            owner.device = owner._model.device
            owner.inference_backend = owner._model.backend
            owner.backend_warning = owner._model.warning
    try:
        return operation(**kwargs)
    except RuntimeError as exc:
        message = str(exc).casefold()
        if not str(owner.device).casefold().startswith("cuda") or not any(
            marker in message
            for marker in ("cuda", "cudnn", "cublas", "out of memory", "device-side assert")
        ):
            raise
        fallback_started = time.perf_counter()
        owner.device = "cpu"
        owner.inference_backend = "CPU"
        owner.input_size = 640
        owner.backend_warning = "GPU 推論失敗，已切換 CPU；短期追蹤可能重新編號。"
        # Ultralytics 的 predictor 會快取原裝置，僅改 device 參數不足。
        owner._model.predictor = None
        owner._model.to("cpu")
        history = getattr(owner, "track_history", None)
        if history is not None:
            history.reset()
            owner._reset_reason = "gpu_fallback"
        try:
            return operation(**{**kwargs, "device": "cpu", "imgsz": 640})
        finally:
            owner.last_cpu_fallback_ms = max(0.0, time.perf_counter() - fallback_started) * 1000
