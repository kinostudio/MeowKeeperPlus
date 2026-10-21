"""將 track history 動作特徵映射為可配置的狀態。"""

from __future__ import annotations

from dataclasses import dataclass

from .track_history import MotionFeatures


@dataclass(frozen=True)
class MotionThresholds:
    stationary_speed: float = 8.0
    running_speed: float = 120.0
    jumping_vertical_speed: float = 160.0
    direction_changes_for_play: int = 2

    def validate(self) -> None:
        if self.stationary_speed < 0 or self.running_speed <= self.stationary_speed:
            raise ValueError("動作速度門檻順序不正確。")
        if self.jumping_vertical_speed <= 0:
            raise ValueError("跳躍垂直速度門檻必須大於零。")


def classify_motion(features: MotionFeatures, thresholds: MotionThresholds | None = None) -> str:
    config = thresholds or MotionThresholds()
    config.validate()
    if abs(features.vertical_speed_px_per_sec) >= config.jumping_vertical_speed:
        return "jumping"
    if features.speed_px_per_sec <= config.stationary_speed:
        return "stationary"
    if features.speed_px_per_sec >= config.running_speed:
        return "running"
    return "walking"
