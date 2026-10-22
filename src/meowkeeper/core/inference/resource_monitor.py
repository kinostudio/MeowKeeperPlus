"""GPU 資源狀態與 24 小時分析速率控制。"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .priority import InferencePriority


class GPUResourceState(str, Enum):
    NORMAL = "NORMAL"
    THROTTLED = "THROTTLED"
    CRITICAL = "CRITICAL"
    RECOVERY = "RECOVERY"


class InferenceMode(str, Enum):
    NORMAL = "normal"
    ACTIVE = "active"
    QUIET = "quiet"
    THROTTLED = "throttled"
    CRITICAL = "critical"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class GPUResourceSnapshot:
    observed_at: datetime
    name: str | None = None
    driver_version: str | None = None
    utilization_percent: float | None = None
    temperature_c: float | None = None
    vram_used_mib: int | None = None
    vram_total_mib: int | None = None
    queue_length: int = 0
    state: GPUResourceState = GPUResourceState.NORMAL
    cuda_available: bool | None = None
    cuda_error_count: int = 0
    oom_count: int = 0


class GPUResourceMonitor:
    """低頻率資源監控；沒有 NVML 或 CUDA 時安全回報未知。"""

    def __init__(
        self,
        *,
        poll_interval_seconds: float = 10.0,
        throttled_temperature_c: float = 80.0,
        critical_temperature_c: float = 90.0,
        throttled_queue_length: int = 4,
        critical_error_count: int = 3,
        provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("GPU 資源輪詢間隔必須大於零。")
        if not 0 < throttled_temperature_c < critical_temperature_c:
            raise ValueError("GPU 溫度門檻順序不正確。")
        self.poll_interval_seconds = poll_interval_seconds
        self.throttled_temperature_c = throttled_temperature_c
        self.critical_temperature_c = critical_temperature_c
        self.throttled_queue_length = max(1, throttled_queue_length)
        self.critical_error_count = max(1, critical_error_count)
        self.provider = provider
        self.state = GPUResourceState.NORMAL
        self.queue_length = 0
        self.cuda_error_count = 0
        self.oom_count = 0
        # Do not import PyTorch on the first HIGH frame just to answer a
        # diagnostic question. An explicit test provider is polled immediately;
        # the default provider waits for the first low-frequency sample.
        self._last_poll_monotonic = 0.0 if provider is not None else time.monotonic()
        self._snapshot = GPUResourceSnapshot(datetime.now(timezone.utc))

    @property
    def snapshot(self) -> GPUResourceSnapshot:
        return self._snapshot

    def update_queue_length(self, queue_length: int) -> GPUResourceSnapshot:
        self.queue_length = max(0, int(queue_length))
        self._recompute_state(self._snapshot.temperature_c)
        self._snapshot = self._snapshot_with(queue_length=self.queue_length, state=self.state)
        return self._snapshot

    def poll(self, *, force: bool = False) -> GPUResourceSnapshot:
        now = time.monotonic()
        if not force and now - self._last_poll_monotonic < self.poll_interval_seconds:
            return self._snapshot
        self._last_poll_monotonic = now
        values: dict[str, Any] = {}
        if self.provider is not None:
            try:
                values = dict(self.provider() or {})
            except Exception:  # noqa: BLE001 - optional provider is diagnostic only
                values = {}
        else:
            values = self._default_provider()
        temperature = _float_or_none(values.get("temperature_c"))
        self._recompute_state(temperature)
        self._snapshot = GPUResourceSnapshot(
            observed_at=datetime.now(timezone.utc),
            name=values.get("name"),
            driver_version=values.get("driver_version"),
            utilization_percent=_float_or_none(values.get("utilization_percent")),
            temperature_c=temperature,
            vram_used_mib=_int_or_none(values.get("vram_used_mib")),
            vram_total_mib=_int_or_none(values.get("vram_total_mib")),
            queue_length=self.queue_length,
            state=self.state,
            cuda_available=values.get("cuda_available"),
            cuda_error_count=self.cuda_error_count,
            oom_count=self.oom_count,
        )
        return self._snapshot

    def allow(self, priority: InferencePriority) -> bool:
        """高優先行為工作可在降級狀態執行，低優先工作延後。"""

        self.poll()
        if self.state in {GPUResourceState.THROTTLED, GPUResourceState.CRITICAL}:
            return priority <= InferencePriority.NORMAL
        return True

    def record_success(self) -> None:
        if self.state == GPUResourceState.CRITICAL:
            self.state = GPUResourceState.RECOVERY
        elif self.state == GPUResourceState.THROTTLED:
            # Keep the conservative state until the next temperature poll.
            return
        self._snapshot = self._snapshot_with(state=self.state)

    def record_error(self, error: BaseException) -> None:
        self.cuda_error_count += 1
        if "out of memory" in str(error).casefold() or "cuda" in str(error).casefold():
            self.oom_count += 1
        if self.cuda_error_count >= self.critical_error_count or self.oom_count > 0:
            self.state = GPUResourceState.CRITICAL
        self._snapshot = self._snapshot_with(
            state=self.state,
            cuda_error_count=self.cuda_error_count,
            oom_count=self.oom_count,
        )

    def recover(self) -> None:
        """由服務 watchdog 在確認資源穩定後逐步解除錯誤降級。"""

        self.cuda_error_count = 0
        self.oom_count = 0
        self.state = GPUResourceState.RECOVERY
        self._snapshot = self._snapshot_with(
            state=self.state,
            cuda_error_count=0,
            oom_count=0,
        )

    reset_errors = recover

    def _recompute_state(self, temperature_c: float | None) -> None:
        previous = self.state
        if self.oom_count > 0 or self.cuda_error_count >= self.critical_error_count or temperature_c is not None and temperature_c >= self.critical_temperature_c:
            self.state = GPUResourceState.CRITICAL
        elif (
            (temperature_c is not None and temperature_c >= self.throttled_temperature_c)
            or self.queue_length >= self.throttled_queue_length
        ):
            self.state = GPUResourceState.THROTTLED
        elif previous in {GPUResourceState.THROTTLED, GPUResourceState.CRITICAL}:
            self.state = GPUResourceState.RECOVERY
        elif previous == GPUResourceState.RECOVERY:
            self.state = GPUResourceState.NORMAL
        else:
            self.state = GPUResourceState.NORMAL

    def _snapshot_with(self, **changes: Any) -> GPUResourceSnapshot:
        values = self._snapshot.__dict__.copy()
        values.update(changes)
        return GPUResourceSnapshot(**values)

    @staticmethod
    def _default_provider() -> dict[str, Any]:
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                completed = subprocess.run(
                    [
                        nvidia_smi,
                        "--query-gpu=name,driver_version,utilization.gpu,temperature.gpu,memory.used,memory.total",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    check=False,
                    shell=False,
                    timeout=2.0,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                values = [part.strip() for part in completed.stdout.splitlines()[0].split(",")]
                if completed.returncode == 0 and len(values) >= 6:
                    return {
                        "name": values[0],
                        "driver_version": values[1],
                        "utilization_percent": _float_or_none(values[2]),
                        "temperature_c": _float_or_none(values[3]),
                        "vram_used_mib": _int_or_none(values[4]),
                        "vram_total_mib": _int_or_none(values[5]),
                        "cuda_available": True,
                    }
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        if importlib.util.find_spec("torch") is None:
            return {"cuda_available": False}
        try:
            import torch  # type: ignore[import-not-found]

            if not torch.cuda.is_available():
                return {"cuda_available": False}
            index = torch.cuda.current_device()
            props = torch.cuda.get_device_properties(index)
            return {
                "name": torch.cuda.get_device_name(index),
                "vram_total_mib": int(props.total_memory / 1024**2),
                "cuda_available": True,
            }
        except (ImportError, OSError, RuntimeError, AttributeError):
            return {"cuda_available": False}


class InferenceLoadController:
    """依活動與資源狀態選擇 YOLO 目標 FPS，不影響原始影音接收。"""

    def __init__(
        self,
        *,
        normal_fps: float = 3.0,
        active_fps: float = 5.0,
        quiet_fps: float = 1.0,
        active_seconds: float = 30.0,
        quiet_after_seconds: float = 60.0,
        monitor: GPUResourceMonitor | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        if not 1 <= quiet_fps <= normal_fps <= active_fps <= 5:
            raise ValueError("動態分析 FPS 必須滿足 1 <= quiet <= normal <= active <= 5。")
        if active_seconds <= 0 or quiet_after_seconds <= 0:
            raise ValueError("活動／安靜模式時間必須大於零。")
        self.normal_fps = normal_fps
        self.active_fps = active_fps
        self.quiet_fps = quiet_fps
        self.active_seconds = active_seconds
        self.quiet_after_seconds = quiet_after_seconds
        self.monitor = monitor
        self.last_activity_monotonic: float | None = None
        self.last_seen_cat_monotonic: float | None = None
        self.mode = InferenceMode.NORMAL
        self.logger = logger
        self.active_transitions: int = 0
        self.last_mode: str = "normal"
        self.last_active_at: str | None = None
        self._last_analyzed_monotonic: float | None = None
        self._last_analyzed_captured_at: Any | None = None

    @property
    def target_fps(self) -> float:
        if self.monitor is not None:
            state = self.monitor.poll().state
            if state == GPUResourceState.CRITICAL:
                return self.quiet_fps
            if state == GPUResourceState.THROTTLED:
                return min(self.normal_fps, 2.0)
        now = time.monotonic()
        if self.last_activity_monotonic is not None and now - self.last_activity_monotonic <= self.active_seconds:
            return self.active_fps
        if self.last_seen_cat_monotonic is None:
            return self.normal_fps
        if now - self.last_seen_cat_monotonic >= self.quiet_after_seconds:
            return self.quiet_fps
        return self.normal_fps

    @property
    def activity_mode(self) -> str:
        fps = self.target_fps
        if fps >= self.active_fps:
            return "active"
        if fps <= self.quiet_fps:
            return "quiet"
        return "normal"

    def observe_activity(self, *, cat_seen: bool = False, active: bool = False) -> None:
        now = time.monotonic()
        if cat_seen:
            self.last_seen_cat_monotonic = now
        if active:
            self.last_activity_monotonic = now
        mode = self.activity_mode
        if mode != self.last_mode:
            if mode == "active":
                self.active_transitions += 1
                self.last_active_at = datetime.now(timezone.utc).isoformat()
                if self.logger:
                    self.logger("⚡ AI 推論已切換至活躍模式（5 FPS，偵測到動態）")
            elif mode == "quiet":
                if self.logger:
                    self.logger("🌙 AI 推論已進入待機節能模式（1 FPS，畫面無貓咪動態）")
            elif mode == "normal":
                if self.logger:
                    self.logger("🐾 AI 推論已切換至一般常態模式（3 FPS）")
            self.last_mode = mode

    def should_analyze(self, captured_at: Any | None = None) -> bool:
        if captured_at is not None and hasattr(captured_at, "timestamp"):
            if self._last_analyzed_captured_at is None:
                self._last_analyzed_captured_at = captured_at
                return True
            elapsed = (captured_at - self._last_analyzed_captured_at).total_seconds()
            if elapsed >= 1.0 / self.target_fps:
                self._last_analyzed_captured_at = captured_at
                return True
            return False
        now = time.monotonic()
        interval = 1.0 / self.target_fps
        if self._last_analyzed_monotonic is None or now - self._last_analyzed_monotonic >= interval:
            self._last_analyzed_monotonic = now
            return True
        return False

    def set_mode_for_resource(self) -> InferenceMode:
        if self.monitor is None:
            self.mode = InferenceMode.NORMAL
        else:
            state = self.monitor.poll().state
            self.mode = {
                GPUResourceState.NORMAL: InferenceMode.NORMAL,
                GPUResourceState.THROTTLED: InferenceMode.THROTTLED,
                GPUResourceState.CRITICAL: InferenceMode.CRITICAL,
                GPUResourceState.RECOVERY: InferenceMode.RECOVERY,
            }[state]
        return self.mode

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.activity_mode,
            "activity_mode": self.activity_mode,
            "resource_mode": self.mode.value,
            "target_fps": self.target_fps,
            "normal_fps": self.normal_fps,
            "active_fps": self.active_fps,
            "quiet_fps": self.quiet_fps,
            "active_seconds": self.active_seconds,
            "active_transitions": self.active_transitions,
            "last_active_at": self.last_active_at,
        }


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
