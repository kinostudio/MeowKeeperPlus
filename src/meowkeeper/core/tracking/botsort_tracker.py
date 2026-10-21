"""Ultralytics BoT-SORT adapter；只負責短期 track，不指派永久身分。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from meowkeeper.core.inference.runtime_device import run_with_cpu_fallback
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.runtime import default_model_file, validate_model_file

from .base import BoundingBox, TrackingBackend, TrackObservation
from .track_history import TrackHistory


class TrackingConfigurationError(RuntimeError):
    """追蹤器設定或選用 runtime 不足。"""


def _accelerator_warning(diagnostics: dict[str, Any]) -> str:
    stage = diagnostics.get("stage")
    error_type = diagnostics.get("error_type")
    labels = {
        "export_verification": "加速模型完整性檢查",
        "openvino_import": "OpenVINO 載入",
        "intel_gpu_discovery": "Intel GPU 探索",
        "openvino_model_compile": "OpenVINO 模型編譯",
        "openvino_device_verification": "Intel GPU 執行裝置確認",
        "gpu_warmup_640": "GPU 首次推論暖機",
        "cuda_kernel_verification": "CUDA kernel 驗證",
        "onnxruntime_session": "ONNX Runtime 初始化",
        "backend_initialization": "加速後端初始化",
    }
    if stage and error_type:
        return f"指定的加速後端於「{labels.get(stage, '啟動')}」未就緒（{error_type}），已安全降級 CPU。"
    return "指定的加速後端不可用，已安全降級 CPU。"


class BoTSORTTracker(TrackingBackend):
    """以 Ultralytics BoT-SORT `persist=True` 追蹤 COCO cat。"""

    def __init__(
        self,
        model_path: str | None = None,
        *,
        model: Any | None = None,
        confidence_threshold: float = 0.45,
        device: str | None = None,
        tracker_config: str | None = None,
        track_buffer: int = 30,
        input_size: int = 640,
    ) -> None:
        if input_size not in {640, 960, 1280, 1920}:
            raise TrackingConfigurationError("YOLO 推論長邊只支援 640／960／1280／1920。")
        self.input_size = input_size
        self.model_path = Path(model_path) if model_path else default_model_file()
        if model is None:
            if not self.model_path.is_file():
                raise TrackingConfigurationError(
                    "找不到 YOLO 模型，無法啟動 BoT-SORT；請由開發／建置階段準備預設模型，"
                    "終端使用者不需要手動下載。"
                )
            if self.model_path.resolve() == default_model_file().resolve():
                try:
                    validate_model_file(self.model_path)
                except RuntimeError as exc:
                    raise TrackingConfigurationError(str(exc)) from exc
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TrackingConfigurationError("BoT-SORT 需要 opencv-python 與 numpy。") from exc
        self._cv2 = cv2
        self._np = np
        try:
            import torch  # type: ignore[import-not-found]

            cuda_available = bool(torch.cuda.is_available())
        except (ImportError, OSError, RuntimeError):
            cuda_available = False
        if model is None:
            try:
                data_root = Path.cwd() / "data" / "ultralytics"
                data_root.mkdir(parents=True, exist_ok=True)
                os.environ.setdefault("YOLO_CONFIG_DIR", str(data_root))
                matplotlib_root = data_root.parent / "matplotlib"
                matplotlib_root.mkdir(parents=True, exist_ok=True)
                os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_root))
                from ultralytics import YOLO  # type: ignore[import-not-found]
            except ImportError as exc:
                raise TrackingConfigurationError("BoT-SORT 需要 ultralytics。") from exc
            from meowkeeper.core.inference.accelerated_yolo import select_accelerated
            self.backend_diagnostics: dict[str, Any] = {}
            self._model = select_accelerated(
                self.model_path, device, diagnostics=self.backend_diagnostics
            ) or YOLO(str(self.model_path))
        else:
            self._model = model
            self.backend_diagnostics = {}
        self.backend_warning: str | None = None
        self.confidence_threshold = confidence_threshold
        requested_device = device.strip() if device and device.strip() else None
        if requested_device and requested_device.casefold().startswith("cuda") and not cuda_available:
            requested_device = "cpu"
        self.device = requested_device or ("cuda:0" if cuda_available else "cpu")
        self.inference_backend = (
            "PyTorch CUDA" if self.device.casefold().startswith("cuda") and cuda_available else "CPU"
        )
        if isinstance(getattr(self._model, "backend", None), str):
            self.inference_backend = self._model.backend
            self.device = self._model.device
        elif self.device in {"intel:gpu", "onnx:cuda", "onnx:cpu", "auto"}:
            self.device = "cpu"
            self.inference_backend = "CPU"
            self.backend_warning = _accelerator_warning(self.backend_diagnostics)
        self.track_history = TrackHistory(maxlen=track_buffer)
        self._reset_reason: str | None = None
        self.tracker_config = tracker_config or str(
            Path(__file__).resolve().parents[4] / "configs" / "botsort_reid.yaml"
        )

    def update(self, frame: FramePacket) -> list[TrackObservation]:
        from time import perf_counter
        decode_at = perf_counter()
        image_array = self._np.frombuffer(frame.jpeg_bytes, dtype=self._np.uint8)
        image = self._cv2.imdecode(image_array, self._cv2.IMREAD_COLOR)
        decoded_at = perf_counter()
        if image is None:
            return []
        height, width = image.shape[:2]
        effective_size = min(self.input_size, max(width, height))
        kwargs: dict[str, Any] = {
            "source": image,
            "persist": True,
            "tracker": self.tracker_config,
            "with_reid": True,
            "classes": [15],
            "conf": self.confidence_threshold,
            "verbose": False,
            "imgsz": effective_size,
        }
        if self.device:
            kwargs["device"] = self.device
        try:
            results = run_with_cpu_fallback(self, self._model.track, kwargs)
        except (SyntaxError, TypeError):
            # Some Ultralytics releases read with_reid from the YAML only and
            # reject it as a generic predict argument. The config remains the
            # source of truth for BoT-SORT ReID in that case.
            kwargs.pop("with_reid", None)
            # A preceding CUDA fallback can change the owner before the
            # compatibility retry. Never reuse the original CUDA device.
            kwargs["device"] = self.device
            results = run_with_cpu_fallback(self, self._model.track, kwargs)
        if isinstance(getattr(self._model, "backend", None), str):
            self.inference_backend = self._model.backend
            self.device = self._model.device
            self.backend_warning = self._model.warning
            self.backend_diagnostics = dict(getattr(self._model, "backend_diagnostics", self.backend_diagnostics))
        observations: list[TrackObservation] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or getattr(boxes, "id", None) is None:
                continue
            ids = boxes.id
            classes = getattr(boxes, "cls", [])
            confidences = getattr(boxes, "conf", [])
            coordinates = boxes.xyxy
            for index in range(len(ids)):
                if int(classes[index]) != 15:
                    continue
                x1, y1, x2, y2 = [float(value) for value in coordinates[index].tolist()]
                bbox = BoundingBox(x1, y1, x2, y2)
                left, top = max(0, int(x1)), max(0, int(y1))
                right, bottom = min(width, int(x2)), min(height, int(y2))
                crop_jpeg: bytes | None = None
                if right > left and bottom > top:
                    ok, encoded = self._cv2.imencode(".jpg", image[top:bottom, left:right])
                    if ok:
                        crop_jpeg = encoded.tobytes()
                observations.append(
                    TrackObservation(
                        track_id=int(ids[index]),
                        bbox=bbox,
                        captured_at=frame.captured_at,
                        confidence=float(confidences[index]),
                        source_id=frame.source_url,
                        frame_width=width,
                        frame_height=height,
                        crop_jpeg=crop_jpeg,
                    )
                )
        self.last_timings = {**getattr(self._model, "last_timings", {}),
                             "jpeg_decode_ms": (decoded_at - decode_at) * 1000,
                             "worker_tracking_and_crops_ms": (perf_counter() - decoded_at) * 1000}
        return observations

    def reset(self, reason: str = "manual") -> None:
        self.track_history.reset()
        self._reset_reason = reason
        predictor = getattr(self._model, "predictor", None)
        if predictor is not None:
            predictor.trackers = None

    @property
    def reset_reason(self) -> str | None:
        return self._reset_reason
