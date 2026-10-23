"""Apply conservative FPS/thermal policy without stopping the media reader."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from meowkeeper.core.detection.frame_analysis import FrameAnalysis
from meowkeeper.core.inference.adaptive_policy import AdaptivePolicy, AdaptiveSettings
from meowkeeper.core.inference.gpu_telemetry import CachedGpuSampler


class AdaptiveAnalyzer:
    def __init__(self, analyzer: Any, *, fps: int, logger: Any, input_size: int = 640) -> None:
        self.analyzer = analyzer
        self.policy = AdaptivePolicy(AdaptiveSettings(preferred_fps=fps, preferred_size=input_size))
        self.logger = logger
        self._last_started: float | None = None
        self._latency = 0.0
        self._last_sample = -float("inf")
        self._previous_cpu: tuple[float, float] | None = None
        self._failures = 0
        self._temperature: float | None = None
        self._load: float | None = None
        self._backend = getattr(analyzer, "inference_backend", "unknown")
        self._gpu_telemetry = False
        self._retry = threading.Event()
        self._pressure = False
        self._completed: deque[float] = deque(maxlen=100)
        self._latencies: deque[float] = deque(maxlen=30)
        self._gpu_sampler = CachedGpuSampler()

    def set_clip_service(self, clip_service: object) -> None:
        if hasattr(self.analyzer, "set_clip_service"):
            self.analyzer.set_clip_service(clip_service)
        elif hasattr(self.analyzer, "pipeline"):
            self.analyzer.pipeline.clip_service = clip_service

    def measured_fps(self) -> float:
        now = time.monotonic()
        while self._completed and self._completed[0] < now - 5:
            self._completed.popleft()
        return round(len(self._completed) / 5, 2)

    def request_retry(self) -> bool:
        if self._pressure or self._temperature is not None and self._temperature >= (
                self.policy.settings.cpu_warning_c if self.inference_backend == "CPU" else self.policy.settings.gpu_warning_c):
            return False
        self._retry.set()
        return True

    def cloud_pressure_reason(self):
        if self._pressure:
            return "memory_pressure"
        threshold = self.policy.settings.cpu_warning_c if self.inference_backend == "CPU" else self.policy.settings.gpu_warning_c
        if self._temperature is not None and self._temperature >= threshold:
            return "thermal_pressure"
        if self._load is not None and self._load >= self.policy.settings.target_percent:
            return "compute_pressure"
        return None  # 感測器缺失不補造溫度；既有本機回退策略仍獨立執行。

    def _worker(self) -> Any:
        candidate = self.analyzer
        if not callable(getattr(candidate, "snapshot", None)):
            session_tracker = getattr(candidate, "tracker", None)
            candidate = getattr(session_tracker, "tracker", session_tracker)
        return candidate if callable(getattr(candidate, "snapshot", None)) else None

    def _activity_sampling(self):
        pipeline = getattr(self.analyzer, "pipeline", None)
        controller = getattr(pipeline, "load_controller", None)
        return controller.as_dict() if callable(getattr(controller, "as_dict", None)) else None

    def _set_input_size(self, value: int) -> None:
        self.policy.size = value
        candidate = self.analyzer
        visited: set[int] = set()
        while candidate is not None and id(candidate) not in visited:
            visited.add(id(candidate))
            if hasattr(candidate, "input_size"):
                candidate.input_size = value
            candidate = getattr(candidate, "tracker", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.analyzer, name)

    @property
    def inference_backend(self) -> str:
        return getattr(self.analyzer, "inference_backend", "noop")

    @property
    def device(self) -> str:
        return getattr(self.analyzer, "device", "cpu")

    def _sample(self, now: float) -> tuple[float | None, float | None, bool]:
        try:
            import psutil

            worker = self._worker()
            worker_pid = worker.snapshot().get("pid") if worker is not None else None
            process = psutil.Process(worker_pid) if worker_pid else None
            load = None
            if process is not None:
                current = process.cpu_times()
                total = current.user + current.system
                if self._previous_cpu is not None:
                    before, previous_total = self._previous_cpu
                    load = 100 * max(0, total - previous_total) / max(now - before, 0.001) / (psutil.cpu_count() or 1)
                self._previous_cpu = (now, total)
            else:
                self._previous_cpu = None
            memory = psutil.virtual_memory()
            memory_pressure = memory.available < max(1024 ** 3, memory.total * 0.1)
            # Only unambiguous CPU package/core sensor groups are used. Windows
            # often exposes none; absence is not zero degrees.
            temperature = None
            get_temperatures = getattr(psutil, "sensors_temperatures", None)
            if self.inference_backend == "CPU" and callable(get_temperatures):
                values = [item.current for name, items in get_temperatures().items()
                          if name.casefold() in {"coretemp", "k10temp", "cpu_thermal"}
                          for item in items if item.current is not None and math.isfinite(item.current)]
                if values:
                    temperature = max(values)
            if self.inference_backend in {"PyTorch CUDA", "ONNX Runtime CUDA"}:
                load, temperature, free_mib = self._gpu_sampler.sample(self.device)
                self._gpu_telemetry = load is not None
                memory_pressure = memory_pressure or (free_mib is not None and free_mib < 128)
            elif self.inference_backend == "OpenVINO GPU":
                load = None  # Worker CPU 用量不能冒充 Intel GPU 使用率。
            return load, temperature, memory_pressure
        except Exception:  # noqa: BLE001 - telemetry must never stop media or inference
            return None, None, False

    def snapshot(self) -> dict[str, Any]:
        worker = self.analyzer
        if not callable(getattr(worker, "snapshot", None)):
            session_tracker = getattr(worker, "tracker", None)
            worker = getattr(session_tracker, "tracker", session_tracker)
        worker_state = worker.snapshot() if callable(getattr(worker, "snapshot", None)) else {
            "pid": None, "alive": None, "restart_count": 0, "isolation": "unavailable",
        }
        tracker = getattr(getattr(self.analyzer, "tracker", None), "tracker", None)
        latency_average = sum(self._latencies) / len(self._latencies) if self._latencies else None
        latencies = sorted(self._latencies)
        trigger = self.policy.last_trigger
        age = max(0, time.monotonic() - self._completed[-1]) if self._completed else None
        current_slow = (latency_average > 1 / self.policy.fps) if latency_average is not None and age is not None and age <= 15 else None
        activity = self._activity_sampling()
        pipeline = getattr(self.analyzer, "pipeline", None)
        scheduler = getattr(pipeline, "inference_scheduler", None)
        scheduler_timings = getattr(scheduler, "timings", None)
        scheduler_metrics = getattr(scheduler, "metrics", None)
        adapter_timings = getattr(self.analyzer, "timings", None)
        pipeline_timings = getattr(pipeline, "timings", None)
        activity_fps = activity.get("target_fps") if activity else None
        combined_cap = min(self.policy.fps, activity_fps) if type(activity_fps) in (int, float) and activity_fps > 0 else self.policy.fps
        return {**self.policy.snapshot(), "latency_ms": round(self._latency * 1000, 1),
                "latency_scope": "analysis_end_to_end",
                "latency_average_ms": round(sum(self._latencies) / len(self._latencies) * 1000, 1) if self._latencies else None,
                "frame_budget_ms": round(1000 / self.policy.fps, 1),
                "load_percent": self._load,
                "load_scope": ("gpu_device_total" if self.inference_backend in {"PyTorch CUDA", "ONNX Runtime CUDA"}
                               else "gpu_unavailable" if self.inference_backend == "OpenVINO GPU"
                               else "ai_worker_cpu_normalized"),
                "temperature_c": self._temperature,
                "temperature_status": "available" if self._temperature is not None else "unavailable",
                "gpu_telemetry_status": "available" if self._gpu_telemetry and self._load is not None else "unavailable",
                "gpu_sampling": self._gpu_sampler.snapshot(),
                "latency_sample_count": len(latencies),
                "latency_p50_ms": round(latencies[len(latencies) // 2] * 1000, 1) if latencies else None,
                "latency_p95_ms": round(latencies[min(len(latencies) - 1, math.ceil(len(latencies) * .95) - 1)] * 1000, 1) if latencies else None,
                "currently_over_budget": current_slow,
                "degradation_is_historical": bool(trigger and trigger.get("kind") == "latency" and current_slow is False),
                "last_analysis_age_seconds": round(age, 2) if age is not None else None,
                "activity_sampling": activity,
                "host_scheduler_timings": scheduler_timings.snapshot() if scheduler_timings is not None else None,
                "host_scheduler_queue_metrics": scheduler_metrics.as_dict() if callable(getattr(scheduler_metrics, "as_dict", None)) else None,
                "host_adapter_stage_timings": adapter_timings.snapshot() if callable(getattr(adapter_timings, "snapshot", None)) else None,
                "v02_pipeline_stage_timings": pipeline_timings.snapshot() if callable(getattr(pipeline_timings, "snapshot", None)) else None,
                "stage_timing_contract": "每個階段獨立以程序內 monotonic/perf_counter 計時；平均／P50／P95不可相加成整體延遲。",
                "combined_sampling_fps_cap": combined_cap,
                "bottleneck_status": ("suspended" if self.policy.state in {"suspended", "recovery_available"}
                                      else "analysis_processing" if current_slow is True
                                      else "not_measured" if current_slow is None
                                      else "activity_sampling" if combined_cap < self.policy.fps
                                      else "waiting_or_non_model_work_unresolved" if self.measured_fps() < self.policy.fps * .7
                                      else "within_budget"),
                "fps_window_seconds": 5,
                "fps_definition": "completed_analyses_in_trailing_window",
                "worker_isolation": worker_state["isolation"], "worker": worker_state,
                "backend_warning": getattr(tracker, "backend_warning", None),
                "failure_count": self._failures}

    def analyze(self, frame: Any) -> FrameAnalysis:
        now = time.monotonic()
        if now - self._last_sample >= 2:
            self._last_sample = now
            self._load, self._temperature, pressure = self._sample(now)
            self._pressure = pressure
            # CPU measurement is child-worker CPU normalized to the logical-core count.
            previous_trigger = self.policy.last_trigger
            previous_fps, previous_size = self.policy.fps, self.policy.size
            changed = self.policy.observe(now, backend=self.inference_backend,
                                          load_percent=self._load,
                                          temperature_c=self._temperature,
                                          memory_pressure=pressure,
                                          too_slow=(len(self._latencies) >= 5 and
                                                    sum(self._latencies) / len(self._latencies) > 1 / self.policy.fps))
            if changed:
                if self.policy.last_trigger is not None and self.policy.last_trigger is not previous_trigger:
                    self.policy.last_trigger.update({
                        "previous_fps": previous_fps,
                        "previous_size": previous_size,
                        "latency_average_ms": round(sum(self._latencies) / len(self._latencies) * 1000, 1) if self._latencies else None,
                        "frame_budget_ms": round(1000 / self.policy.last_trigger.get("previous_fps", self.policy.fps), 1),
                        "triggered_at_monotonic": now,
                        "triggered_at_utc": datetime.now(timezone.utc).isoformat(),
                        "reason_zh_tw": self.policy.reason,
                        "effective_fps_after": self.policy.fps,
                        "effective_input_size_after": self.policy.size,
                        "latency_p95_ms": round(sorted(self._latencies)[min(len(self._latencies) - 1, math.ceil(len(self._latencies) * .95) - 1)] * 1000, 1) if self._latencies else None,
                    })
                self.logger("⚠️ " + self.policy.reason)
                self._set_input_size(self.policy.size)
                if self.policy.state == "suspended":
                    self.cancel_pending()
        if self._retry.is_set():
            self._retry.clear()
            if not self._pressure and (self._temperature is None or self._temperature < (
                    self.policy.settings.cpu_warning_c if self.inference_backend == "CPU" else self.policy.settings.gpu_warning_c)):
                self.policy = AdaptivePolicy(self.policy.settings)
                self._set_input_size(self.policy.size)
                self._latencies.clear()
                self._latency = 0
                self._failures = 0
                self.logger(f"依使用者操作，以 {self.policy.size}／{self.policy.fps} FPS 設定上限重新嘗試 AI；持續超載仍會降階，未重啟影音來源。")
        if self.policy.state in {"suspended", "recovery_available"}:
            return FrameAnalysis(False, None, None, self.policy.reason, {"analysis_skipped": True})
        # If the inner activity controller already imposes the lower cap,
        # do not independently decimate that same stream first. Two periodic
        # gates alias (5 FPS source -> outer 3 -> quiet 1 becomes ~0.8 FPS).
        # When the safety cap is lower, it still wins; no safety gate is removed.
        activity = self._activity_sampling()
        inner_fps = activity.get("target_fps") if activity else None
        inner_is_stricter = type(inner_fps) in (int, float) and 0 < inner_fps <= self.policy.fps
        if not inner_is_stricter and self._last_started is not None and now - self._last_started < 1 / self.policy.fps:
            return FrameAnalysis(False, None, None, "本張略過 AI；影音持續。", {"analysis_skipped": True})
        self._last_started = now
        measured = False
        inference_started = time.perf_counter()  # Exclude this wrapper's telemetry polling.
        try:
            result = self.analyzer.analyze(frame)
            measured = not result.details.get("analysis_skipped")
            if measured:
                self._completed.append(time.monotonic())
            self._failures = 0
            backend = self.inference_backend
            if backend != self._backend:
                self._backend = backend
                if backend == "CPU":
                    self.policy.fps = 1
                    self._set_input_size(640)
                    self.policy.state = "degraded"
                    self.policy.reason = "GPU 已降級 CPU；從 640／1 FPS 保守分析。"
                    self.logger("⚠️ " + self.policy.reason)
            return result
        except Exception as exc:  # noqa: BLE001 - AI faults must not stop recording
            self._failures += 1
            self.policy.fps = 1
            if self._failures >= 2:
                self.policy.state = "suspended"
                self.policy.reason = "AI 連續失敗，已暫停分析；影音仍持續。"
                self.cancel_pending()
            self.logger(f"⚠️ AI 分析失敗：{type(exc).__name__}；未停止影音。")
            return FrameAnalysis(False, None, None, "AI 分析失敗，本張無判斷結果。", {"analysis_skipped": True})
        finally:
            if measured:
                self._latency = time.perf_counter() - inference_started
                self._latencies.append(self._latency)

    def close(self) -> None:
        self._gpu_sampler.close()
        close = getattr(self.analyzer, "close", None)
        if callable(close):
            close()

    def cancel_pending(self) -> None:
        worker = self._worker()
        cancel = getattr(worker, "cancel_pending", None) if worker is not None else None
        if callable(cancel):
            cancel()
