"""同一 YOLO11n 的動態 ONNX 推論；不依賴 CUDA 版 PyTorch。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class AcceleratorInitializationError(RuntimeError):
    """Sanitized stage/type for an optional accelerator startup failure."""

    def __init__(self, stage: str, error_type: str) -> None:
        self.stage = stage
        self.error_type = error_type if error_type.isidentifier() else "BackendError"
        super().__init__(f"{stage}:{self.error_type}")


def verified_export(model_path: Path) -> Path | None:
    directory = model_path.parent / "accelerated"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        path = directory / "yolo11n.onnx"
        if (manifest["source_sha256"] == hashlib.sha256(model_path.read_bytes()).hexdigest()
                and manifest["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()):
            return path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


class AcceleratedYolo:
    """Batch 1、矩形 stride-32 輸入、cat-only NMS 與既有 BoT-SORT。"""

    def __init__(self, path: Path, backend: str) -> None:
        self.initialization_stage = "initialization"
        try:
            self._initialize(path, backend)
        except AcceleratorInitializationError:
            raise
        except Exception as exc:
            raise AcceleratorInitializationError(
                self.initialization_stage, type(exc).__name__
            ) from exc

    def _initialize(self, path: Path, backend: str) -> None:
        self.initialization_stage = "numpy_import"
        import numpy as np
        self.np = np
        self.path = path
        self.backend = backend
        self.warning = None
        self.backend_diagnostics: dict[str, Any] = {}
        self.predictor = SimpleNamespace(trackers=None)
        self.last_input_shape: tuple[int, ...] | None = None
        self.provider_node_counts: dict[str, int] = {}
        self.last_timings = {}
        self._timed_gmc_tracker = None
        self._tracker_gmc_timing_available = False
        self._last_tracker_gmc_apply_ms: float | None = None
        self._profile_directory = None
        if backend == "OpenVINO GPU":
            self.initialization_stage = "openvino_import"
            import openvino as ov
            self.initialization_stage = "intel_gpu_discovery"
            core = ov.Core()
            devices = [d for d in core.available_devices if d.startswith("GPU")
                       and "intel" in core.get_property(d, "FULL_DEVICE_NAME").lower()]
            if not devices:
                raise RuntimeError("Intel GPU 不可用。")
            self.initialization_stage = "openvino_model_compile"
            self.session = core.compile_model(core.read_model(path), devices[0], {
                "PERFORMANCE_HINT": "LATENCY",
            })
            self.initialization_stage = "openvino_device_verification"
            if not all(d.startswith("GPU") for d in self.session.get_property("EXECUTION_DEVICES")):
                raise RuntimeError("OpenVINO 未使用 GPU。")
            self.device = "intel:gpu"
            self.execution_devices = list(self.session.get_property("EXECUTION_DEVICES"))
        else:
            self.initialization_stage = "onnxruntime_session"
            self._ort_session(backend == "ONNX Runtime CUDA")
        # GPU 成功執行一張影格後才回報就緒；DLL/provider 清單不是推論證據。
        self.initialization_stage = "gpu_warmup_640"
        self._run(np.zeros((1, 3, 640, 640), dtype=np.float32))
        if backend == "ONNX Runtime CUDA":
            self.initialization_stage = "cuda_kernel_verification"
            try:
                profile = json.loads(Path(self.session.end_profiling()).read_text(encoding="utf-8"))
                for event in profile:
                    provider = event.get("args", {}).get("provider")
                    if provider:
                        self.provider_node_counts[provider] = self.provider_node_counts.get(provider, 0) + 1
                if not self.provider_node_counts.get("CUDAExecutionProvider"):
                    raise RuntimeError("未觀測到 CUDA kernel，不能宣稱 GPU 推論。")
            finally:
                self._profile_directory.cleanup()
        self.initialization_stage = "ready"

    def _ort_session(self, cuda: bool) -> None:
        import onnxruntime as ort
        if cuda:
            ort.preload_dlls(directory="")
            if "CUDAExecutionProvider" not in ort.get_available_providers():
                raise RuntimeError("ONNX CUDA provider 不可用。")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.log_severity_level = 4
        if cuda:
            self._profile_directory = tempfile.TemporaryDirectory(prefix="meowkeeper-ort-proof-")
            options.enable_profiling = True
            options.profile_file_prefix = str(Path(self._profile_directory.name) / "warmup")
        providers: list[Any] = [("CUDAExecutionProvider", {
            "device_id": 0, "gpu_mem_limit": 1536 * 1024**2,
            "cudnn_conv_algo_search": "HEURISTIC", "cudnn_conv_use_max_workspace": "0",
        })] if cuda else ["CPUExecutionProvider"]
        self.session = ort.InferenceSession(str(self.path), options, providers=providers)
        self.session.disable_fallback()
        if cuda and self.session.get_providers()[0] != "CUDAExecutionProvider":
            self.session.end_profiling()
            self.session = None
            self._profile_directory.cleanup()
            raise RuntimeError("ONNX CUDA 建立失敗，未實際使用 GPU。")
        self.device = "cuda:0" if cuda else "cpu"
        self.execution_devices = self.session.get_providers()
        self.input_name = self.session.get_inputs()[0].name

    def _run(self, tensor: Any) -> Any:
        if self.backend == "OpenVINO GPU":
            return self.session([tensor])[self.session.output(0)]
        return self.session.run(None, {self.input_name: tensor})[0]

    def predict(self, *, source: Any, imgsz: int, conf: float, **_kwargs: Any) -> list:
        import cv2
        from ultralytics.engine.results import Results
        np = self.np
        prepared_at = time.perf_counter()
        height, width = source.shape[:2]
        ratio = min(imgsz / max(width, height), 1.0)
        resized_w, resized_h = round(width * ratio), round(height * ratio)
        pad_w, pad_h = (-resized_w) % 32, (-resized_h) % 32
        left, top = pad_w // 2, pad_h // 2
        image = cv2.resize(source, (resized_w, resized_h))
        image = cv2.copyMakeBorder(image, top, pad_h - top, left, pad_w - left,
                                   cv2.BORDER_CONSTANT, value=(114, 114, 114))
        tensor = np.ascontiguousarray(image[..., ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
        self.last_input_shape = tuple(tensor.shape)
        run_at = time.perf_counter()
        fallback_ms = None
        try:
            output = self._run(tensor)
        except Exception as exc:
            if self.backend == "CPU":
                raise
            failed_backend = self.backend
            self.session = None
            fallback_at = time.perf_counter()
            self.provider_node_counts = {}
            self.backend = "CPU"
            self._ort_session(False)
            self.predictor.trackers = None
            self.warning = f"GPU 推論失敗（{type(exc).__name__}），已降級 CPU；短期追蹤重新編號。"
            self.backend_diagnostics = {
                "requested_device": "intel:gpu" if failed_backend == "OpenVINO GPU" else "onnx:cuda",
                "status": "fallback", "backend": failed_backend,
                "stage": "runtime_inference", "error_type": type(exc).__name__,
            }
            output = self._run(tensor)
            fallback_ms = (time.perf_counter() - fallback_at) * 1000
        post_at = time.perf_counter()
        rows = output[0].T
        if rows.shape[1] != 84:
            raise RuntimeError("YOLO ONNX 輸出契約不符。")
        scores = rows[:, 4 + 15]
        # 保留真正最可能類別為 cat 的框；不把人的低 cat 分數誤當貓。
        selected = (scores >= conf) & (np.argmax(rows[:, 4:], axis=1) == 15)
        xywh, scores = rows[selected, :4], scores[selected]
        xywh[:, :2] -= xywh[:, 2:] / 2
        indices = cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), conf, 0.7)
        boxes = []
        for index in np.asarray(indices).reshape(-1):
            x, y, w, h = xywh[index]
            x1, y1 = (x - left) / ratio, (y - top) / ratio
            boxes.append([max(0, x1), max(0, y1), min(width, x1 + w / ratio),
                          min(height, y1 + h / ratio), float(scores[index]), 15])
        result = [Results(source, path="shared-frame", names={15: "cat"},
                          boxes=np.asarray(boxes, dtype=np.float32).reshape(-1, 6))]
        self.last_timings = {"model_input_prepare_ms": (run_at - prepared_at) * 1000,
                             "model_run_completed_output_ms": (post_at - run_at) * 1000,
                             "yolo_postprocess_ms": (time.perf_counter() - post_at) * 1000,
                             "cpu_fallback_ms": fallback_ms}
        return result

    def track(self, *, tracker: str, persist: bool = True, **kwargs: Any) -> list:
        from ultralytics.trackers.bot_sort import BOTSORT
        from ultralytics.utils import YAML, IterableSimpleNamespace
        results = self.predict(**kwargs)
        track_at = time.perf_counter()
        if self.predictor.trackers is None or not persist:
            config = YAML.load(tracker)
            # 匯出偵測模型不含 backbone ReID 特徵；不額外下載／啟動模型。
            config["with_reid"] = False
            self.predictor.trackers = BOTSORT(IterableSimpleNamespace(**config))
        self._install_gmc_timing()
        self._last_tracker_gmc_apply_ms = None
        result = results[0]
        transfer_at = time.perf_counter()
        tracker_input = result.boxes.cpu().numpy()
        tracker_input_transfer_ms = (time.perf_counter() - transfer_at) * 1000
        tracker_update_at = time.perf_counter()
        tracks = self.predictor.trackers.update(tracker_input, kwargs["source"])
        tracker_engine_update_ms = (time.perf_counter() - tracker_update_at) * 1000
        format_at = time.perf_counter()
        if len(tracks):
            result = result[tracks[:, -1].astype(int)]
            result.update(boxes=tracks[:, :-1])
        else:
            result.update(boxes=self.np.empty((0, 7), dtype=self.np.float32))
        tracker_output_format_ms = (time.perf_counter() - format_at) * 1000
        self.last_timings.update({
            "tracking_ms": (time.perf_counter() - track_at) * 1000,
            "tracker_input_transfer_ms": tracker_input_transfer_ms,
            "tracker_engine_update_ms": tracker_engine_update_ms,
            "tracker_output_format_ms": tracker_output_format_ms,
            "tracker_gmc_apply_ms": self._last_tracker_gmc_apply_ms,
        })
        return [result]

    def _install_gmc_timing(self) -> None:
        """Measure GMC as a nested stage without changing tracker inputs or results."""
        tracker = self.predictor.trackers
        gmc = getattr(tracker, "gmc", None)
        if gmc is None or gmc is self._timed_gmc_tracker:
            return
        apply = getattr(gmc, "apply", None)
        self._timed_gmc_tracker = gmc
        if not callable(apply):
            return
        original_apply = apply

        def timed_apply(*args: Any, **call_kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return original_apply(*args, **call_kwargs)
            finally:
                elapsed_ms = max(0.0, time.perf_counter() - started) * 1000
                self._last_tracker_gmc_apply_ms = (
                    (self._last_tracker_gmc_apply_ms or 0.0) + elapsed_ms
                )

        try:
            gmc.apply = timed_apply
        except (AttributeError, TypeError):
            return
        self._tracker_gmc_timing_available = True


def select_accelerated(
    model_path: Path, device: str | None, *, diagnostics: dict[str, Any] | None = None,
) -> AcceleratedYolo | None:
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update({"requested_device": device, "status": "not_attempted", "attempts": []})
    if device == "cpu":
        if diagnostics is not None:
            diagnostics.update({"status": "cpu_requested", "stage": "selection"})
        return None
    path = verified_export(model_path)
    if path is None:
        if diagnostics is not None:
            diagnostics.update({"status": "fallback", "stage": "export_verification",
                                "error_type": "ModelExportUnavailable"})
        return None
    candidates = {"intel:gpu": ["OpenVINO GPU"], "onnx:cpu": ["CPU"],
                  "onnx:cuda": ["ONNX Runtime CUDA"], "cuda:0": ["ONNX Runtime CUDA"]}
    for backend in candidates.get(device or "auto", ["ONNX Runtime CUDA", "OpenVINO GPU"]):
        try:
            model = AcceleratedYolo(path, backend)
            if diagnostics is not None:
                diagnostics.update({"status": "active", "backend": backend,
                                    "stage": "ready", "execution_devices": model.execution_devices})
                model.backend_diagnostics = dict(diagnostics)
            return model
        except Exception as exc:  # noqa: BLE001 - optional backend cannot stop the Host
            stage = getattr(exc, "stage", "backend_initialization")
            error_type = getattr(exc, "error_type", type(exc).__name__)
            if diagnostics is not None:
                diagnostics["attempts"].append({"backend": backend, "stage": stage,
                                                "error_type": error_type})
            continue
    if diagnostics is not None:
        diagnostics["status"] = "fallback"
        if diagnostics["attempts"]:
            diagnostics.update(diagnostics["attempts"][-1])
    return None
