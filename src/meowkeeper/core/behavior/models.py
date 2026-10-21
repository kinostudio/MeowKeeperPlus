"""V0.2 行為、區域與事件的資料模型。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from meowkeeper.domain.behavior import BehaviorState, EventTag, MotionState


@dataclass(frozen=True)
class ZoneDefinition:
    zone_id: str
    name: str
    zone_type: str
    polygon: tuple[tuple[float, float], ...]
    enabled: bool = True

    def validate(self) -> None:
        if not self.zone_id or not self.name:
            raise ValueError("區域需要 zone_id 與名稱。")
        if self.zone_type not in {
            "food",
            "water",
            "bed",
            "litter_box",
            "litter",
            "play_area",
            "play",
            "excluded",
        }:
            raise ValueError("未知區域類型。")
        if len(self.polygon) < 3:
            raise ValueError("區域 polygon 至少需要三個點。")
        if any(not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0 for x, y in self.polygon):
            raise ValueError("區域座標必須是 0 到 1 的 normalized 值。")

    @classmethod
    def rectangle(cls, zone_id: str, name: str, zone_type: str, x: float, y: float, width: float, height: float) -> ZoneDefinition:
        return cls(zone_id, name, zone_type, ((x, y), (x + width, y), (x + width, y + height), (x, y + height)))

    def contains_center(self, center: tuple[float, float]) -> bool:
        if not self.enabled:
            return False
        x, y = center
        inside = False
        points = self.polygon
        for index, (px, py) in enumerate(points):
            qx, qy = points[index - 1]
            intersects = (py > y) != (qy > y) and x < (qx - px) * (y - py) / ((qy - py) or 1e-9) + px
            if intersects:
                inside = not inside
        return inside


@dataclass(frozen=True)
class BehaviorConfig:
    minimum_eating_seconds: float = 10.0
    meal_merge_gap_seconds: float = 30.0
    minimum_drinking_seconds: float = 5.0
    drinking_merge_gap_seconds: float = 20.0
    minimum_litter_seconds: float = 10.0
    sleeping_minimum_seconds: float = 60.0
    state_switch_minimum_seconds: float = 2.0
    zone_center_required: bool = True
    play_direction_changes: int = 2
    play_jump_count: int = 2

    def validate(self) -> None:
        values = (
            self.minimum_eating_seconds,
            self.meal_merge_gap_seconds,
            self.minimum_drinking_seconds,
            self.drinking_merge_gap_seconds,
            self.minimum_litter_seconds,
            self.sleeping_minimum_seconds,
            self.state_switch_minimum_seconds,
        )
        if any(value < 0 for value in values):
            raise ValueError("行為時間門檻不可小於零。")
        if self.play_direction_changes < 1 or self.play_jump_count < 1:
            raise ValueError("玩耍動作門檻必須至少為 1。")


@dataclass(frozen=True)
class TrackFeature:
    track_id: str | int
    pet_id: str | None
    observed_at: datetime
    source_id: str
    motion_state: MotionState
    speed_px_per_sec: float
    acceleration_px_per_sec2: float
    vertical_speed_px_per_sec: float
    direction_changes: int
    dwell_seconds: float
    active_zone_ids: tuple[str, ...] = ()
    active_zone_types: tuple[str, ...] = ()
    jump_count: int = 0
    interacting: bool = False
    identity_confidence: float = 0.0
    camera_preset: str | None = None
    zone_dwell_seconds: dict[str, float] = field(default_factory=dict)

    def in_zone_type(self, zone_type: str) -> bool:
        return zone_type in self.active_zone_types


@dataclass(frozen=True)
class BehaviorObservation:
    pet_id: str | None
    track_id: str | int
    behavior_state: BehaviorState
    motion_state: MotionState
    event_tags: tuple[EventTag, ...]
    observed_at: datetime
    raw_confidence: float
    smoothed_confidence: float | None
    inference_method: str
    model_version: str
    rule_profile_version: str
    source_id: str
    camera_preset: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    identity_status: str = "unknown"
    identity_confidence: float = 0.0
    identity_model_version: str | None = None


@dataclass
class BehaviorEvent:
    pet_id: str | None
    track_id: str | int
    behavior_state: BehaviorState
    motion_state: MotionState
    event_tags: tuple[EventTag, ...]
    started_at: datetime
    ended_at: datetime | None
    raw_confidence: float
    smoothed_confidence: float | None
    inference_method: str
    model_version: str
    rule_profile_version: str
    source_id: str
    camera_preset: str | None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    human_review: bool = False
    human_label: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    # Identity is deliberately late-bound. A behavior event may be created
    # while the track is still anonymous and patched after background work.
    identity_status: str = "unknown"
    identity_confidence: float = 0.0
    identity_resolved_at: datetime | None = None
    identity_model_version: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return max((self.ended_at - self.started_at).total_seconds(), 0.0)
