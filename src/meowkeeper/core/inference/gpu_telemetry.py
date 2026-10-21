"""Read-only NVIDIA telemetry, with device identity checks and bounded queries."""
from __future__ import annotations

import csv
import math
import shutil
import subprocess
import threading
import time
from collections import deque
from io import StringIO

_GPU_FIELDS = (
    "gpu_model",
    "gpu_kernel_busy_percent",
    "memory_controller_busy_percent",
    "temperature_c",
    "dedicated_memory_used_mib",
    "dedicated_memory_total_mib",
    "memory_free_mib",
    "graphics_clock_mhz",
    "memory_clock_mhz",
    "power_draw_w",
    "power_limit_w",
    "throttle_active_reasons",
)


def _empty_details() -> dict:
    return {field: None for field in _GPU_FIELDS}


def _normalize_details(value) -> dict:
    """Accept the old 3-tuple reader contract used by local probes and tests."""
    if isinstance(value, dict):
        return {field: value.get(field) for field in _GPU_FIELDS}
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return {
            **_empty_details(),
            "gpu_kernel_busy_percent": value[0],
            "temperature_c": value[1],
            "memory_free_mib": value[2],
        }
    return _empty_details()


class CachedGpuSampler:
    """One bounded background read; slow NVIDIA utilities never block analysis.

    Cached results are device-wide (not process-specific). Missing/expired
    values stay None. A close invalidates in-flight results.
    """

    def __init__(self, *, reader=None, clock=None, interval=2.0, max_age=6.0):
        self.reader = reader or read_gpu_details
        self.clock = clock or time.monotonic
        self.interval, self.max_age = interval, max_age
        self._lock = threading.Lock()
        self._pending = False
        self._closed = False
        self._requested_at = -float("inf")
        self._device = None
        self._value = _empty_details()
        self._observed_at = None
        self._duration_ms = None
        self._sample_sequence = 0
        self._history = deque(maxlen=128)

    def _read(self, device):
        started = self.clock()
        try:
            value = _normalize_details(self.reader(device))
        except Exception:  # noqa: BLE001 - optional telemetry cannot stop video
            value = _empty_details()
        finished = self.clock()
        with self._lock:
            if not self._closed and device == self._device:
                self._value = value
                self._observed_at = finished
                self._duration_ms = max(0, finished - started) * 1000
                self._sample_sequence += 1
                gpu_busy = value["gpu_kernel_busy_percent"]
                memory_busy = value["memory_controller_busy_percent"]
                if gpu_busy is not None or memory_busy is not None:
                    self._history.append((finished, value.copy()))
            self._pending = False

    def sample(self, device):
        now = self.clock()
        with self._lock:
            if self._closed:
                return None, None, None
            if device != self._device:
                self._device = device
                self._observed_at = None
                self._history.clear()
            if not self._pending and now - self._requested_at >= self.interval:
                self._requested_at = now
                self._pending = True
                threading.Thread(
                    target=self._read,
                    args=(device,),
                    name="meowkeeper-gpu-telemetry",
                    daemon=True,
                ).start()
            fresh = self._observed_at is not None and now - self._observed_at <= self.max_age
            if not fresh:
                return None, None, None
            return (
                self._value["gpu_kernel_busy_percent"],
                self._value["temperature_c"],
                self._value["memory_free_mib"],
            )

    def snapshot(self):
        now = self.clock()
        with self._lock:
            fresh = self._observed_at is not None and now - self._observed_at <= self.max_age
            recent = [row[1] for row in self._history if row[0] >= now - 60]
            gpu_values = [row["gpu_kernel_busy_percent"] for row in recent
                          if row["gpu_kernel_busy_percent"] is not None]
            memory_values = [row["memory_controller_busy_percent"] for row in recent
                             if row["memory_controller_busy_percent"] is not None]
            temperature_values = [row["temperature_c"] for row in recent if row["temperature_c"] is not None]
            memory_used_values = [row["dedicated_memory_used_mib"] for row in recent
                                  if row["dedicated_memory_used_mib"] is not None]
            graphics_clock_values = [row["graphics_clock_mhz"] for row in recent
                                     if row["graphics_clock_mhz"] is not None]
            current = self._value if fresh else _empty_details()
            return {
                "source": "nvidia-smi",
                "sampling": "background_shared_per_analyzer",
                "scope": "whole_gpu_device_not_process_specific",
                "status": "available" if fresh and current["gpu_kernel_busy_percent"] is not None else "unavailable",
                "sample_age_seconds": round(now - self._observed_at, 3) if self._observed_at is not None else None,
                "sample_sequence": self._sample_sequence,
                "query_duration_ms": round(self._duration_ms, 1) if self._duration_ms is not None else None,
                "load_mean_60s_percent": sum(gpu_values) / len(gpu_values) if gpu_values else None,
                "load_max_60s_percent": max(gpu_values) if gpu_values else None,
                "sample_count_60s": len(gpu_values),
                "memory_busy_mean_60s_percent": sum(memory_values) / len(memory_values) if memory_values else None,
                "memory_busy_max_60s_percent": max(memory_values) if memory_values else None,
                "memory_sample_count_60s": len(memory_values),
                "temperature_mean_60s_c": round(sum(temperature_values) / len(temperature_values), 1)
                if temperature_values else None,
                "temperature_max_60s_c": max(temperature_values) if temperature_values else None,
                "temperature_sample_count_60s": len(temperature_values),
                "dedicated_memory_used_mean_60s_mib": round(sum(memory_used_values) / len(memory_used_values), 1)
                if memory_used_values else None,
                "dedicated_memory_used_max_60s_mib": max(memory_used_values) if memory_used_values else None,
                "dedicated_memory_sample_count_60s": len(memory_used_values),
                "graphics_clock_mean_60s_mhz": round(sum(graphics_clock_values) / len(graphics_clock_values), 1)
                if graphics_clock_values else None,
                "history_coverage_seconds": round(min(60, now - self._history[0][0]), 1)
                if self._history else 0,
                "window_seconds": 60,
                **current,
            }

    def close(self):
        with self._lock:
            self._closed = True
            self._observed_at = None


def _number(value: str, *, minimum: float = 0, maximum: float | None = None):
    text = value.strip()
    if not text or text.casefold() in {"n/a", "na", "not supported", "[not supported]"}:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        return None
    return result


def parse_gpu_sample(text: str, *, uuid: str | None, name: str | None) -> tuple:
    """Backward-compatible parser for the original minimal 5-field query."""
    rows = list(csv.reader(StringIO(text)))
    matches = [row for row in rows if len(row) == 5 and uuid and row[0].strip().casefold() == uuid.casefold()]
    if (not matches and not uuid and len(rows) == 1 and len(rows[0]) == 5 and name
            and rows[0][1].strip().casefold() == name.casefold()):
        matches = rows
    if len(matches) != 1:
        return None, None, None
    load, temperature, free_mib = (_number(value) for value in matches[0][2:])
    if (load is None or load > 100 or temperature is None or temperature > 130 or free_mib is None):
        return None, None, None
    return load, temperature, free_mib


def parse_gpu_details(text: str, *, uuid: str | None, name: str | None) -> dict:
    """Parse device-wide NVML metrics. Unsupported individual fields stay None."""
    empty = _empty_details()
    rows = list(csv.reader(StringIO(text)))
    matches = [row for row in rows if len(row) == 18 and uuid and row[0].strip().casefold() == uuid.casefold()]
    if not matches and not uuid and name:
        named_rows = [row for row in rows if len(row) == 18 and row[1].strip().casefold() == name.casefold()]
        if len(named_rows) == 1:
            matches = named_rows
    if len(matches) != 1:
        return empty
    row = matches[0]
    throttle_labels = (
        "軟體功率限制", "硬體降頻", "硬體過熱降頻", "軟體溫度限制", "同步 Boost 限制", "GPU 閒置降頻",
    )
    throttle_values = [value.strip().casefold() for value in row[12:18]]
    active_reasons = [label for label, value in zip(throttle_labels, throttle_values, strict=True)
                      if value == "active"]
    throttle_available = any(value in {"active", "not active"} for value in throttle_values)
    return {
        "gpu_model": row[1].strip() or None,
        "gpu_kernel_busy_percent": _number(row[2], maximum=100),
        "memory_controller_busy_percent": _number(row[3], maximum=100),
        "temperature_c": _number(row[4], maximum=130),
        "dedicated_memory_used_mib": _number(row[5]),
        "dedicated_memory_total_mib": _number(row[6]),
        "memory_free_mib": _number(row[7]),
        "graphics_clock_mhz": _number(row[8]),
        "memory_clock_mhz": _number(row[9]),
        "power_draw_w": _number(row[10]),
        "power_limit_w": _number(row[11]),
        "throttle_active_reasons": active_reasons if throttle_available else None,
    }


def read_gpu_details(device: str) -> dict:
    """Read extended, device-wide NVIDIA metrics for a specific CUDA device."""
    executable = shutil.which("nvidia-smi")
    if not executable or not device.startswith("cuda"):
        return _empty_details()
    try:
        import torch

        try:
            properties = torch.cuda.get_device_properties(device)
            uuid = getattr(properties, "uuid", None)
            name = getattr(properties, "name", None)
        except (RuntimeError, AssertionError):
            uuid, name = None, None  # ORT CUDA 不依賴 CUDA 版 torch。
        if name and not uuid and torch.cuda.device_count() != 1:
            return _empty_details()
        query = (
            "--query-gpu=uuid,name,utilization.gpu,utilization.memory,temperature.gpu,"
            "memory.used,memory.total,memory.free,clocks.gr,clocks.mem,power.draw,power.limit,"
            "clocks_throttle_reasons.sw_power_cap,clocks_throttle_reasons.hw_slowdown,"
            "clocks_throttle_reasons.hw_thermal_slowdown,clocks_throttle_reasons.sw_thermal_slowdown,"
            "clocks_throttle_reasons.sync_boost,clocks_throttle_reasons.gpu_idle"
        )
        result = subprocess.run(
            [executable, query, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return _empty_details()
        if name is None:
            rows = list(csv.reader(StringIO(result.stdout)))
            if len(rows) != 1 or len(rows[0]) != 18 or device not in {"cuda", "cuda:0"}:
                return _empty_details()
            uuid = rows[0][0].strip()
        return parse_gpu_details(result.stdout, uuid=str(uuid) if uuid else None, name=name)
    except (ImportError, OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        return _empty_details()


def read_gpu_sample(device: str) -> tuple:
    """Compatibility tuple for adaptive policy; rich data lives in the sampler snapshot."""
    value = read_gpu_details(device)
    return value["gpu_kernel_busy_percent"], value["temperature_c"], value["memory_free_mib"]
