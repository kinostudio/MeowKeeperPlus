"""從追蹤框與動作歷史建立行為規則輸入。"""

from __future__ import annotations

from datetime import datetime

from meowkeeper.core.tracking.base import TrackObservation
from meowkeeper.core.tracking.motion_features import classify_motion
from meowkeeper.core.tracking.track_history import TrackHistory
from meowkeeper.domain.behavior import MotionState

from .models import TrackFeature
from .zones import ZoneRegistry


class BehaviorFeatureExtractor:
    def __init__(self, history: TrackHistory, zones: ZoneRegistry) -> None:
        self.history = history
        self.zones = zones
        self._zone_entered_at: dict[tuple[str | int, str], datetime] = {}
        self._jump_counts: dict[str | int, int] = {}
        self._last_motion_state: dict[str | int, str] = {}

    def extract(
        self,
        observation: TrackObservation,
        *,
        pet_id: str | None,
        interacting: bool = False,
        camera_preset: str | None = None,
        identity_confidence: float = 0.0,
    ) -> TrackFeature:
        motion = self.history.features(observation.track_id) or self.history.update(observation)
        normalized = observation.bbox.normalized(observation.frame_width, observation.frame_height)
        center = ((normalized[0] + normalized[2]) / 2.0, (normalized[1] + normalized[3]) / 2.0)
        active_zones = self.zones.classify_center(center)
        active_ids = tuple(zone.zone_id for zone in active_zones)
        active_types = tuple(zone.zone_type for zone in active_zones)
        for zone in active_zones:
            self._zone_entered_at.setdefault((observation.track_id, zone.zone_id), observation.captured_at)
        for key in [key for key in self._zone_entered_at if key[0] == observation.track_id and key[1] not in active_ids]:
            self._zone_entered_at.pop(key, None)
        zone_dwell = {
            zone.zone_id: max(
                (observation.captured_at - self._zone_entered_at[(observation.track_id, zone.zone_id)]).total_seconds(),
                0.0,
            )
            for zone in active_zones
        }
        current_motion = classify_motion(motion)
        if current_motion == "jumping" and self._last_motion_state.get(observation.track_id) != "jumping":
            self._jump_counts[observation.track_id] = self._jump_counts.get(observation.track_id, 0) + 1
        self._last_motion_state[observation.track_id] = current_motion
        return TrackFeature(
            track_id=observation.track_id,
            pet_id=pet_id,
            observed_at=observation.captured_at,
            source_id=observation.source_id,
            motion_state=MotionState(current_motion),
            speed_px_per_sec=motion.speed_px_per_sec,
            acceleration_px_per_sec2=motion.acceleration_px_per_sec2,
            vertical_speed_px_per_sec=motion.vertical_speed_px_per_sec,
            direction_changes=motion.direction_changes,
            dwell_seconds=motion.dwell_seconds,
            active_zone_ids=active_ids,
            active_zone_types=active_types,
            jump_count=self._jump_counts.get(observation.track_id, 0),
            interacting=interacting,
            identity_confidence=identity_confidence,
            camera_preset=camera_preset,
            zone_dwell_seconds=zone_dwell,
        )

    def reset(self) -> None:
        self._zone_entered_at.clear()
        self._jump_counts.clear()
        self._last_motion_state.clear()
