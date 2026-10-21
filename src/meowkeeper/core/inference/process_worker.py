"""Bounded, restartable process boundary for YOLO detect/track calls."""
from __future__ import annotations

import multiprocessing as mp
import time
from collections import deque
from multiprocessing.connection import Connection
from typing import Any

from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.inference.timings import StageTimings
from meowkeeper.core.media.sources import FramePacket


def _safe_failure_type(value: Any) -> str | None:
    if isinstance(value, str) and len(value) <= 64 and value.isascii() and value.isidentifier():
        return value
    return None


def _safe_backend_diagnostics(value: Any) -> dict[str, Any]:
    """Allow only bounded backend identifiers across the worker boundary."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("requested_device", "status", "backend", "stage", "error_type"):
        item = value.get(key)
        if key == "backend":
            if isinstance(item, str) and item in {"OpenVINO GPU", "ONNX Runtime CUDA", "CPU"}:
                result[key] = item
        elif isinstance(item, str) and len(item) <= 64 and item.isascii() and item.replace(":", "").replace("_", "").replace(".", "").isalnum():
            result[key] = item
    attempts = value.get("attempts")
    if isinstance(attempts, list):
        safe_attempts = []
        for attempt in attempts[:3]:
            if not isinstance(attempt, dict):
                continue
            safe = {}
            backend = attempt.get("backend")
            if isinstance(backend, str) and backend in {"OpenVINO GPU", "ONNX Runtime CUDA", "CPU"}:
                safe["backend"] = backend
            for key in ("stage", "error_type"):
                item = attempt.get(key)
                if isinstance(item, str) and len(item) <= 64 and item.isascii() and item.replace("_", "").isalnum():
                    safe[key] = item
            if safe:
                safe_attempts.append(safe)
        if safe_attempts:
            result["attempts"] = safe_attempts
    devices = value.get("execution_devices")
    if isinstance(devices, list):
        result["execution_devices"] = [item for item in devices[:4]
                                        if isinstance(item, str) and len(item) <= 32
                                        and item.isascii() and item.replace(".", "").isalnum()]
    return result


class InferenceWorkerError(RuntimeError):
    """Safe, value-free worker failure for the Host boundary."""

    def __init__(self, message: str, *, failure_type: str | None = None) -> None:
        super().__init__(message)
        self.failure_type = _safe_failure_type(failure_type)


def _worker_main(connection: Connection, role: str, config: dict[str, Any]) -> None:
    analyzer: Any = None
    try:
        import cv2
        cv2.setNumThreads(1)  # Bound preprocessing CPU; does not change ingest.
        try:
            import torch
            torch.set_num_threads(1)
        except (ImportError, RuntimeError):
            pass
        if role == "detect":
            from meowkeeper.core.detection.frame_analysis import YoloFrameAnalyzer
            analyzer = YoloFrameAnalyzer(**config)
        elif role == "track":
            from meowkeeper.core.tracking.botsort_tracker import BoTSORTTracker
            analyzer = BoTSORTTracker(**config)
        else:
            raise ValueError("worker role")
        connection.send(("ready", analyzer.inference_backend, analyzer.device,
                         _safe_backend_diagnostics(getattr(analyzer, "backend_diagnostics", {}))))
        while True:
            message = connection.recv()
            operation = message[0]
            action_at = time.perf_counter()
            if operation == "close":
                break
            if operation == "reset":
                reset = getattr(analyzer, "reset", None)
                if callable(reset):
                    reset("worker_restart")
                result: Any = bool(callable(reset))
            elif operation in {"detect", "track"}:
                _, sequence, captured_at, jpeg_bytes, input_size = message
                if input_size is not None:
                    analyzer.input_size = input_size
                frame = FramePacket(sequence, captured_at, "isolated-inference", jpeg_bytes)
                result = analyzer.analyze(frame) if operation == "detect" else analyzer.update(frame)
            else:
                raise ValueError("worker operation")
            model = getattr(analyzer, "_model", None)
            timings = {**getattr(analyzer, "last_timings", {}),
                       "worker_action_ms": (time.perf_counter() - action_at) * 1000}
            connection.send(("ok", result, analyzer.inference_backend, analyzer.device,
                             {"stages": timings,
                              "provider_node_counts": getattr(model, "provider_node_counts", {}),
                              "backend_diagnostics": _safe_backend_diagnostics(
                                  getattr(analyzer, "backend_diagnostics", {}))}))
    except EOFError:
        pass
    except Exception as exc:  # noqa: BLE001 - isolate arbitrary third-party model errors at the IPC boundary
        try:
            connection.send(("error", type(exc).__name__))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if analyzer is not None:
            try:
                analyzer.close()
            except (AttributeError, OSError, RuntimeError):
                pass
        connection.close()


class IsolatedInferenceWorker:
    """One synchronous RPC at a time; parent frame queue stays bounded at one."""

    def __init__(self, role: str, config: dict[str, Any], *, startup_timeout: float = 120,
                 inference_timeout: float = 15, restart_limit: int = 2,
                 restart_window: float = 600, worker_target: Any = None) -> None:
        if role not in {"detect", "track"}:
            raise ValueError("不支援的 AI worker 類型。")
        self.role = role
        self.config = dict(config)
        self.startup_timeout = startup_timeout
        self.inference_timeout = inference_timeout
        self.restart_limit = restart_limit
        self.restart_window = restart_window
        self.worker_target = worker_target or _worker_main
        self.restarts: deque[float] = deque()
        self.process: Any = None
        self.connection: Connection | None = None
        self.backend = "starting"
        self.device = "unknown"
        self.input_size = int(config.get("input_size", 640))
        self._ever_started = False
        self.timings = StageTimings()
        self.provider_node_counts = {}
        self.last_failure_type: str | None = None
        self.first_failure_type: str | None = None
        self.backend_diagnostics: dict[str, Any] = {}
        self._ready = False
        if self.input_size not in {640, 960, 1280, 1920}:
            raise ValueError("AI worker 輸入尺寸只支援 640／960／1280／1920。")

    def _record_failure_type(self, value: Any) -> str | None:
        failure_type = _safe_failure_type(value)
        self.last_failure_type = failure_type
        if self.first_failure_type is None:
            self.first_failure_type = failure_type
        return failure_type

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.is_alive()

    @property
    def inference_backend(self) -> str:
        return self.backend

    @inference_backend.setter
    def inference_backend(self, value: str) -> None:
        self.backend = value

    def _start(self) -> None:
        if self._ever_started:
            self._record_restart()
        parent, child = mp.get_context("spawn").Pipe(duplex=True)
        process = mp.get_context("spawn").Process(
            target=self.worker_target, args=(child, self.role, self.config),
            name="MeowKeeper+ AI worker", daemon=True,
        )
        try:
            process.start()
        except (OSError, RuntimeError) as exc:
            parent.close()
            child.close()
            failure_type = _safe_failure_type(type(exc).__name__)
            self._record_failure_type(failure_type)
            raise InferenceWorkerError("AI worker 無法啟動。", failure_type=failure_type) from exc
        self._ever_started = True
        child.close()
        self.process, self.connection = process, parent
        self.backend = "starting"
        self.device = "unknown"
        self._ready = False
        if not parent.poll(self.startup_timeout):
            self._stop()
            self._record_failure_type("StartupTimeout")
            raise InferenceWorkerError("AI worker 啟動逾時。", failure_type=self.last_failure_type)
        try:
            message = parent.recv()
        except (EOFError, OSError) as exc:
            self._stop()
            self._record_failure_type("StartupTransportError")
            raise InferenceWorkerError("AI worker 啟動失敗。", failure_type=self.last_failure_type) from exc
        if message[0] != "ready":
            error_type = _safe_failure_type(message[1] if len(message) > 1 else None) or "WorkerError"
            self._record_failure_type(error_type)
            self._stop()
            raise InferenceWorkerError(f"AI worker 載入失敗：{error_type}。", failure_type=error_type)
        self.backend, self.device = message[1], message[2]
        self.backend_diagnostics = _safe_backend_diagnostics(message[3] if len(message) > 3 else {})
        self._ready = True

    def _stop(self) -> None:
        process, connection = self.process, self.connection
        self.process = self.connection = None
        self._ready = False
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if process is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive() and callable(getattr(process, "kill", None)):
                    process.kill()
                    process.join(timeout=2)
            else:
                process.join(timeout=0)

    def _record_restart(self) -> None:
        now = time.monotonic()
        while self.restarts and self.restarts[0] < now - self.restart_window:
            self.restarts.popleft()
        if len(self.restarts) >= self.restart_limit:
            self._record_failure_type("WorkerRestartLimit")
            raise InferenceWorkerError("AI worker 已達重啟上限，請檢查狀態後手動重試。",
                                       failure_type=self.last_failure_type)
        self.restarts.append(now)

    def _request(self, message: tuple[Any, ...]) -> Any:
        if not self.alive:
            if self.process is not None:
                self._stop()
            self._start()
        assert self.connection is not None
        request_at = time.perf_counter()
        try:
            self.connection.send(message)
            sent_at = time.perf_counter()
            if not self.connection.poll(self.inference_timeout):
                # Count a restart only when a replacement process is actually
                # attempted in _start(). A timed-out process is first reaped;
                # the next request may then consume one bounded restart slot.
                self._stop()
                self._record_failure_type("InferenceTimeout")
                raise InferenceWorkerError("AI 推論逾時，已隔離並回收 worker。",
                                           failure_type=self.last_failure_type)
            response = self.connection.recv()
        except (EOFError, BrokenPipeError, OSError) as exc:
            self._stop()
            self._record_failure_type("WorkerTransportError")
            raise InferenceWorkerError("AI worker 意外結束，將受限重建。",
                                       failure_type=self.last_failure_type) from exc
        if response[0] == "error":
            error_type = _safe_failure_type(response[1] if len(response) > 1 else None) or "WorkerError"
            self._record_failure_type(error_type)
            self._stop()
            raise InferenceWorkerError(f"AI worker 執行失敗：{error_type}。", failure_type=error_type)
        _, result, self.backend, self.device = response[:4]
        stages = response[4].get("stages", {}) if len(response) > 4 else {}
        self.provider_node_counts = response[4].get("provider_node_counts", {}) if len(response) > 4 else {}
        if len(response) > 4:
            self.backend_diagnostics = _safe_backend_diagnostics(response[4].get("backend_diagnostics", {}))
        roundtrip = (time.perf_counter() - request_at) * 1000
        self.timings.observe({**stages, "ipc_send_ms": (sent_at - request_at) * 1000,
                              "rpc_roundtrip_ms": roundtrip,
                              "rpc_overhead_ms": max(0, roundtrip - stages["worker_action_ms"]) if "worker_action_ms" in stages else None})
        return result

    def analyze(self, frame: FramePacket) -> FrameAnalysis:
        if len(frame.jpeg_bytes) > 16 * 1024 * 1024:
            self._record_failure_type("FrameTooLarge")
            raise InferenceWorkerError("AI 影格超過程序通訊上限，已略過。",
                                       failure_type=self.last_failure_type)
        return self._request(("detect", frame.sequence, frame.captured_at,
                              frame.jpeg_bytes, self.input_size))

    def update(self, frame: FramePacket) -> list[Any]:
        if len(frame.jpeg_bytes) > 16 * 1024 * 1024:
            self._record_failure_type("FrameTooLarge")
            raise InferenceWorkerError("AI 影格超過程序通訊上限，已略過。",
                                       failure_type=self.last_failure_type)
        return self._request(("track", frame.sequence, frame.captured_at,
                              frame.jpeg_bytes, self.input_size))

    def reset(self, reason: str = "manual") -> None:
        del reason  # reset is local to this worker's tracking namespace
        self._request(("reset",))

    def close(self) -> None:
        if self.connection is not None and self.alive:
            try:
                self.connection.send(("close",))
            except (BrokenPipeError, OSError):
                pass
            if self.process is not None:
                self.process.join(timeout=2)
        self._stop()

    def cancel_pending(self) -> None:
        """Kill only the child process so Host shutdown never waits on native inference."""
        process = self.process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive() and callable(getattr(process, "kill", None)):
                process.kill()
                process.join(timeout=2)

    def snapshot(self) -> dict[str, Any]:
        return {"pid": self.process.pid if self.alive else None,
                "alive": self.alive, "ready": self._ready,
                "backend": self.backend, "device": self.device,
                "restart_count": len(self.restarts),
                "isolation": "process", "timings": self.timings.snapshot(),
                "provider_node_counts": self.provider_node_counts,
                "backend_diagnostics": dict(self.backend_diagnostics),
                "last_failure_type": self.last_failure_type,
                "first_failure_type": self.first_failure_type,
                "timing_note": "同程序單調時鐘量測區間；RPC差值包含傳輸、排程與序列化，不當作純GPU時間。"}


class IsolatedTracker(IsolatedInferenceWorker):
    """BoT-SORT tracker proxy; behavior, identity, and SQLite stay in Host."""

    def __init__(self, model_path: str, *, confidence_threshold: float, input_size: int,
                 device: str = "auto") -> None:
        super().__init__("track", {
            "model_path": model_path, "confidence_threshold": confidence_threshold,
            "input_size": input_size, "device": device,
        })
        self.inference_backend = "CPU"  # visible safe default before worker reports readiness
        self.device = "cpu"

    def update(self, frame: FramePacket) -> list[Any]:
        result = super().update(frame)
        self.inference_backend = self.backend
        return result
