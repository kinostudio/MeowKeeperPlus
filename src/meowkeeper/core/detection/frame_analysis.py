"""可替換的影像分析介面與本地 YOLO 適配器。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from meowkeeper.core.inference.runtime_device import run_with_cpu_fallback
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.domain.settings import COCO_CAT_CLASS_ID, DEFAULT_VISION_ANALYSIS
from meowkeeper.runtime import (
    ModelValidationError,
    default_model_file,
    validate_model_file,
)


class AnalysisConfigurationError(RuntimeError):
    """分析器設定或可選相依套件不足。"""


@dataclass(frozen=True)
class FrameAnalysis:
    """一張影格的分析結果；triggered 才會進入通知策略。"""

    triggered: bool
    event_type: str | None
    confidence: float | None
    message_zh_tw: str
    details: dict[str, Any] = field(default_factory=dict)


class FrameAnalyzer(Protocol):
    """任何視覺分析後端都必須實作的最小介面。"""

    def analyze(self, frame: FramePacket) -> FrameAnalysis:
        ...


class NoopFrameAnalyzer:
    """乾跑與媒體管線測試用，不宣稱有 AI 分析結果。"""

    def analyze(self, frame: FramePacket) -> FrameAnalysis:
        return FrameAnalysis(
            triggered=False,
            event_type=None,
            confidence=None,
            message_zh_tw="目前未啟用影像分析模型。",
            details={"sequence": frame.sequence},
        )


class YoloFrameAnalyzer:
    """使用本機 Ultralytics YOLO 模型偵測貓咪。

    模型路徑必須指向已存在的本地檔案；為避免意外下載大型模型，本類別
    不接受模型名稱，也不會在缺檔時自動下載。``opencv-python``、``numpy``
    與 ``ultralytics`` 都是選用相依套件，缺少時會回報可理解的錯誤。
    """

    def __init__(
        self,
        model_path: str,
        *,
        confidence_threshold: float = DEFAULT_VISION_ANALYSIS.confidence_threshold,
        input_size: int = DEFAULT_VISION_ANALYSIS.input_size,
        device: str | None = None,
    ) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise AnalysisConfigurationError(
                "YOLO 模型檔不存在；請由開發／建置階段執行 tools\\fetch_model.py，"
                "終端使用者不需要手動下載或輸入模型路徑。"
            )
        if not 0.0 <= confidence_threshold <= 1.0:
            raise AnalysisConfigurationError("YOLO 信心門檻必須介於 0 到 1。")
        if input_size not in {640, 960, 1280, 1920}:
            raise AnalysisConfigurationError("YOLO 輸入長邊只支援 640／960／1280／1920。")
        try:
            if path.resolve() == default_model_file().resolve():
                validate_model_file(path)
        except ModelValidationError as exc:
            raise AnalysisConfigurationError(str(exc)) from exc
        try:
            # Ultralytics 會在 import 時建立設定目錄；固定到可寫的本機資料夾，
            # 不依賴唯讀的使用者漫遊設定，也不把模型或設定寫進 Git。
            data_root = Path(
                os.getenv("LOCALAPPDATA")
                or os.getenv("APPDATA")
                or (Path.home() / "AppData" / "Local")
            ) / "MeowKeeperPlus" / "ultralytics"
            try:
                data_root.mkdir(parents=True, exist_ok=True)
            except OSError:
                # 受限執行環境可能禁止寫入 AppData；使用專案的 ignored data
                # 目錄仍可讓測試與可攜式發布包正常運作。
                data_root = Path.cwd() / "data" / "ultralytics"
                try:
                    data_root.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
            os.environ["YOLO_CONFIG_DIR"] = str(data_root)
            os.environ["MPLCONFIGDIR"] = str(data_root.parent / "matplotlib")
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            import torch  # type: ignore[import-not-found]
            from ultralytics import YOLO  # type: ignore[import-not-found]
        except (ImportError, OSError) as exc:
            raise AnalysisConfigurationError(
                "YOLO 分析需要可寫的 runtime 設定目錄，以及 opencv-python、numpy 與 ultralytics。"
            ) from exc

        self._cv2 = cv2
        self._np = np
        try:
            self._cuda_available = bool(torch.cuda.is_available())
        except (OSError, RuntimeError):
            self._cuda_available = False
        requested_device = device.strip() if device and device.strip() else None
        self.backend_warning: str | None = None
        if requested_device and requested_device.lower().startswith("cuda") and not self._cuda_available:
            self.device = "cpu"
            self.backend_warning = "指定的 PyTorch CUDA 不可用，已安全降級為 CPU。"
        elif requested_device:
            self.device = requested_device
        else:
            self.device = "cuda:0" if self._cuda_available else "cpu"
        self.inference_backend = (
            "PyTorch CUDA"
            if self.device.lower().startswith("cuda") and self._cuda_available
            else "CPU"
        )
        try:
            from meowkeeper.core.inference.accelerated_yolo import select_accelerated
            self.backend_diagnostics: dict[str, Any] = {}
            self._model = select_accelerated(
                path, requested_device, diagnostics=self.backend_diagnostics
            ) or YOLO(str(path))
            if isinstance(getattr(self._model, "backend", None), str):
                self.inference_backend = self._model.backend
                self.device = self._model.device
            elif self.device in {"intel:gpu", "onnx:cuda", "onnx:cpu", "auto"}:
                self.device = "cpu"
                self.inference_backend = "CPU"
                stage = self.backend_diagnostics.get("stage")
                error_type = self.backend_diagnostics.get("error_type")
                if stage and error_type:
                    self.backend_warning = (
                        f"指定的加速後端於 {stage} 未就緒（{error_type}），已安全降級 CPU。"
                    )
                else:
                    self.backend_warning = "指定的加速後端不可用，已安全降級 CPU。"
        except Exception as exc:
            raise AnalysisConfigurationError(f"YOLO 模型載入失敗：{path.name}。") from exc
        self.confidence_threshold = confidence_threshold
        self.input_size = input_size
        self.cat_class_id = COCO_CAT_CLASS_ID
        self.last_timings: dict[str, float | None] = {}

    def analyze(self, frame: FramePacket) -> FrameAnalysis:
        analysis_started = time.perf_counter()
        decode_started = analysis_started
        image_array = self._np.frombuffer(frame.jpeg_bytes, dtype=self._np.uint8)
        image = self._cv2.imdecode(image_array, self._cv2.IMREAD_COLOR)
        decoded_at = time.perf_counter()
        if image is None:
            self.last_timings = {
                "jpeg_decode_ms": max(0.0, decoded_at - decode_started) * 1000,
                "analysis_total_ms": max(0.0, decoded_at - analysis_started) * 1000,
            }
            return FrameAnalysis(
                triggered=False,
                event_type=None,
                confidence=None,
                message_zh_tw="目前影格無法解碼，已略過本次分析。",
                details={"sequence": frame.sequence},
            )
        height, width = image.shape[:2]
        effective_size = min(self.input_size, max(width, height))

        kwargs: dict[str, Any] = {
            "source": image,
            "imgsz": effective_size,
            "conf": self.confidence_threshold,
            "verbose": False,
            "device": self.device,
            "classes": [self.cat_class_id],
        }
        model_started = time.perf_counter()
        self.last_cpu_fallback_ms = None
        results = run_with_cpu_fallback(self, self._model.predict, kwargs)
        model_finished = time.perf_counter()
        if isinstance(getattr(self._model, "backend", None), str):
            self.inference_backend = self._model.backend
            self.device = self._model.device
            self.backend_warning = self._model.warning
            self.backend_diagnostics = dict(getattr(self._model, "backend_diagnostics", self.backend_diagnostics))
        model_timings = dict(getattr(self._model, "last_timings", {}) or {})
        # Ultralytics Results.speed is per-image and excludes Host/IPC waits;
        # keep it separate from the model-call wall clock.
        reported_speed: dict[str, float] = {}
        for result in results:
            speed = getattr(result, "speed", None)
            if isinstance(speed, dict):
                for key in ("preprocess", "inference", "postprocess"):
                    value = speed.get(key)
                    if type(value) in (int, float) and value >= 0:
                        reported_speed[f"ultralytics_{key}_reported_ms"] = float(value)
                if reported_speed:
                    break
        result_started = time.perf_counter()
        cat_detections: list[dict[str, Any]] = []
        for result in results:
            names = getattr(result, "names", {}) or {}
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            classes = getattr(boxes, "cls", [])
            confidences = getattr(boxes, "conf", [])
            for index in range(len(classes)):
                class_id = int(classes[index])
                if class_id != self.cat_class_id:
                    continue
                if isinstance(names, dict):
                    label = str(names.get(class_id, "cat")).lower()
                else:
                    label = str(names[class_id] if class_id < len(names) else "cat").lower()
                confidence = float(confidences[index])
                if confidence >= self.confidence_threshold:
                    coordinates = getattr(boxes, "xyxy", None)
                    bbox = None
                    if coordinates is not None:
                        x1, y1, x2, y2 = (float(v) for v in coordinates[index])
                        bbox = [x1 / width, y1 / height, x2 / width, y2 / height]
                    cat_detections.append(
                        {
                            "class_id": self.cat_class_id,
                            "label": label,
                            "confidence": round(confidence, 4),
                            "bbox": bbox,
                        }
                    )

        best_confidence = max(
            (item["confidence"] for item in cat_detections), default=None
        )
        detected = bool(cat_detections)
        count = len(cat_detections)
        finished_at = time.perf_counter()
        self.last_timings = {
            **model_timings,
            **reported_speed,
            "jpeg_decode_ms": max(0.0, decoded_at - decode_started) * 1000,
            "model_call_wall_ms": max(0.0, model_finished - model_started) * 1000,
            "cpu_fallback_ms": self.last_cpu_fallback_ms,
            "detection_result_adaptation_ms": max(0.0, finished_at - result_started) * 1000,
            "analysis_total_ms": max(0.0, finished_at - analysis_started) * 1000,
        }
        return FrameAnalysis(
            triggered=detected,
            event_type="cat_detected" if detected else None,
            confidence=best_confidence,
            message_zh_tw=(
                f"偵測到 {count} 隻貓咪，信心值 {best_confidence:.2f}。"
                if detected and best_confidence is not None
                else "目前影格沒有偵測到貓咪。"
            ),
            details={"cat_count": count, "detections": cat_detections,
                     "inference_size": effective_size, "source_width": width, "source_height": height,
                     "tensor_shape": getattr(self._model, "last_input_shape", None),
                     "backend_warning": self.backend_warning},
        )
