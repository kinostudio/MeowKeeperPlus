"""Pure, clock-injected load/temperature policy; never changes saved preferences."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class AdaptiveSettings:
    preferred_size: int = 640
    preferred_fps: int = 5
    target_percent: float = 40
    overload_seconds: float = 60
    recovery_seconds: float = 300
    temperature_warning_seconds: float = 30
    gpu_warning_c: float = 80
    gpu_critical_c: float = 85
    cpu_warning_c: float = 85
    cpu_critical_c: float = 95

    def __post_init__(self) -> None:
        if self.preferred_size not in {640, 960, 1280, 1920}:
            raise ValueError("AI 尺寸只允許 640／960／1280／1920。")
        if type(self.preferred_fps) is not int or not 1 <= self.preferred_fps <= 5:
            raise ValueError("AI FPS 必須介於 1 到 5。")
        if not math.isfinite(self.target_percent) or not 10 <= self.target_percent <= 90:
            raise ValueError("長期負載目標必須介於 10% 到 90%。")
        for seconds in (self.overload_seconds, self.recovery_seconds, self.temperature_warning_seconds):
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("回退與恢復時間必須為正數。")
        for warning, critical in ((self.gpu_warning_c, self.gpu_critical_c),
                                  (self.cpu_warning_c, self.cpu_critical_c)):
            if not all(math.isfinite(v) for v in (warning, critical)) or not 40 <= warning < critical <= 110:
                raise ValueError("溫度門檻必須有效，危急門檻需高於警告門檻。")


class AdaptivePolicy:
    def __init__(self, settings: AdaptiveSettings) -> None:
        self.settings = settings
        self.size = settings.preferred_size
        self.fps = settings.preferred_fps
        self.state = "running"
        self.reason = "使用已設定的保守分析上限。"
        self._over_since: float | None = None
        self._hot_since: float | None = None
        self._cool_since: float | None = None
        self._critical_samples = 0
        self.recovery_available = False
        self.last_trigger = None

    @staticmethod
    def _valid(value: float | None) -> bool:
        return value is not None and math.isfinite(value)

    def observe(self, now: float, *, backend: str, load_percent: float | None = None,
                temperature_c: float | None = None, too_slow: bool = False,
                memory_pressure: bool = False) -> bool:
        """One reliable sample; return whether effective state changed."""
        previous = (self.size, self.fps, self.state)
        cpu = backend == "CPU"
        warning = self.settings.cpu_warning_c if cpu else self.settings.gpu_warning_c
        critical = self.settings.cpu_critical_c if cpu else self.settings.gpu_critical_c
        has_temp = self._valid(temperature_c)
        hot = has_temp and temperature_c >= warning
        critical_hot = has_temp and temperature_c >= critical
        self._critical_samples = self._critical_samples + 1 if critical_hot else 0
        if memory_pressure or self._critical_samples >= 2:
            self.last_trigger = {"kind": "memory_pressure" if memory_pressure else "critical_temperature",
                                 "load_percent": load_percent, "temperature_c": temperature_c,
                                 "too_slow": too_slow}
            self.state = "suspended"
            self.recovery_available = False
            self.reason = "AI 已暫停：記憶體不足或溫度達危急門檻；錄影持續。"
            self._cool_since = None
            return previous != (self.size, self.fps, self.state)
        overloaded = too_slow or (self._valid(load_percent) and load_percent > self.settings.target_percent)
        self._over_since = (now if self._over_since is None else self._over_since) if overloaded else None
        self._hot_since = (now if self._hot_since is None else self._hot_since) if hot else None
        due = ((self._over_since is not None and now - self._over_since >= self.settings.overload_seconds)
               or (self._hot_since is not None and now - self._hot_since >= self.settings.temperature_warning_seconds))
        if due and self.state not in {"suspended", "recovery_available"}:
            self.last_trigger = {"kind": "temperature" if hot else "latency" if too_slow else "load",
                                 "load_percent": load_percent, "temperature_c": temperature_c,
                                 "too_slow": too_slow, "previous_fps": self.fps,
                                 "previous_size": self.size}
            smaller = [s for s in (640, 960, 1280, 1920) if s < self.size]
            if smaller:
                self.size = max(smaller)
            elif self.fps > 1:
                self.fps = max(f for f in (1, 2, 3, 5) if f < self.fps)
            else:
                self.state = "suspended"
            if self.state != "suspended":
                self.state = "degraded"
            self.reason = ("溫度持續偏高，已降低 AI 工作。" if hot else
                           "分析端到端耗時持續超過每張預算，已降低 AI 工作（不等同 GPU 滿載）。" if too_slow else
                           "運算裝置持續超過設定負載目標，已降低 AI 工作。")
            self._over_since = self._hot_since = self._cool_since = None
        # Missing telemetry cannot be treated as evidence that recovery is safe.
        # Suspended AI has no new latency samples; an old slow inference must
        # not permanently block recovery after load/temperature become safe.
        cool = (self._valid(load_percent) and load_percent < self.settings.target_percent - 10
                and (not too_slow or self.state in {"suspended", "recovery_available"})
                and (not has_temp or temperature_c < warning - 5))
        if cool and self.state in {"degraded", "suspended", "recovery_available"}:
            self._cool_since = now if self._cool_since is None else self._cool_since
            if now - self._cool_since >= self.settings.recovery_seconds:
                self.state = "recovery_available"
                if previous[2] == "degraded":
                    self.state = "degraded"
                self.recovery_available = True
                self.reason = "負載已穩定；可手動重新嘗試 AI，不會自動提高負載。"
        else:
            self._cool_since = None
            self.recovery_available = False
        return previous != (self.size, self.fps, self.state)

    def snapshot(self) -> dict:
        return {"state": self.state, "effective_input_size": self.size, "effective_fps": self.fps,
                "preferred_input_size": self.settings.preferred_size,
                "preferred_fps": self.settings.preferred_fps, "target_percent": self.settings.target_percent,
                "recovery_available": self.recovery_available, "reason_zh_tw": self.reason,
                "last_trigger": self.last_trigger, "recovery_mode": "manual"}
