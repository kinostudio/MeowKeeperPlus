"""以區域、停留時間與動作特徵判斷 V0.2 主要行為。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meowkeeper.domain.behavior import BehaviorState, EventTag, MotionState

from .confidence import bounded_confidence
from .event_merger import BehaviorEventMerger
from .models import BehaviorConfig, BehaviorEvent, BehaviorObservation, TrackFeature
from .state_machine import BehaviorStateMachine


@dataclass(frozen=True)
class RuleClassification:
    behavior_state: BehaviorState
    event_tags: tuple[EventTag, ...]
    confidence: float
    evidence: dict[str, object]


class RuleBehaviorClassifier:
    """規則優先的保守分類器；running 不會自動等於 playing。"""

    def __init__(self, config: BehaviorConfig | None = None) -> None:
        self.config = config or BehaviorConfig()
        self.config.validate()

    def classify(self, feature: TrackFeature) -> RuleClassification:
        if "excluded" in feature.active_zone_types:
            return RuleClassification(BehaviorState.UNKNOWN, (), 0.95, {"excluded": True})

        zone = set(feature.active_zone_types)
        stationary = feature.motion_state in {MotionState.STATIONARY, MotionState.LOW_MOTION}
        evidence: dict[str, object] = {
            "identity": "resolved" if feature.pet_id is not None else "unknown",
            "zones": sorted(zone),
            "motion_state": feature.motion_state.value,
            "dwell_seconds": feature.dwell_seconds,
            "direction_changes": feature.direction_changes,
            "jump_count": feature.jump_count,
            "zone_dwell_seconds": feature.zone_dwell_seconds,
        }
        zone_dwell = max(feature.zone_dwell_seconds.values(), default=feature.dwell_seconds)
        if "food" in zone and stationary and zone_dwell >= self.config.minimum_eating_seconds:
            return RuleClassification(
                BehaviorState.EATING,
                (EventTag.ZONE_ENTER,),
                bounded_confidence(
                    0.8 if feature.pet_id is not None else 0.65,
                    min(zone_dwell / 30.0, 1.0),
                ),
                evidence,
            )
        if "water" in zone and stationary and zone_dwell >= self.config.minimum_drinking_seconds:
            return RuleClassification(
                BehaviorState.DRINKING,
                (EventTag.ZONE_ENTER,),
                bounded_confidence(
                    0.75 if feature.pet_id is not None else 0.62,
                    min(zone_dwell / 15.0, 1.0),
                ),
                evidence,
            )
        if "bed" in zone and stationary and zone_dwell >= self.config.sleeping_minimum_seconds:
            return RuleClassification(
                BehaviorState.SLEEPING,
                (),
                bounded_confidence(
                    0.8 if feature.pet_id is not None else 0.65,
                    min(zone_dwell / 300.0, 1.0),
                ),
                evidence,
            )
        if (
            ("litter_box" in zone or "litter" in zone)
            and stationary
            and zone_dwell >= self.config.minimum_litter_seconds
        ):
            return RuleClassification(
                BehaviorState.LITTER_BOX,
                (EventTag.ZONE_ENTER,),
                bounded_confidence(
                    0.7 if feature.pet_id is not None else 0.58,
                    min(zone_dwell / 30.0, 1.0),
                ),
                evidence,
            )
        if stationary and feature.dwell_seconds >= self.config.sleeping_minimum_seconds / 2.0:
            return RuleClassification(
                BehaviorState.RESTING,
                (),
                0.65 if feature.pet_id is not None else 0.55,
                evidence,
            )
        playing = (
            bool({"play_area", "play"} & zone)
            and (
                feature.jump_count >= self.config.play_jump_count
                or feature.direction_changes >= self.config.play_direction_changes
                or feature.interacting
            )
        )
        if playing:
            tags = (EventTag.CAT_INTERACTION,) if feature.interacting else ()
            return RuleClassification(
                BehaviorState.PLAYING,
                tags,
                0.7 if feature.pet_id is not None else 0.58,
                evidence,
            )
        if feature.motion_state in {MotionState.WALKING, MotionState.RUNNING, MotionState.JUMPING}:
            return RuleClassification(
                BehaviorState.OTHER_ACTIVE,
                (),
                0.55 if feature.pet_id is not None else 0.45,
                evidence,
            )
        return RuleClassification(
            BehaviorState.UNKNOWN,
            (),
            0.3 if feature.pet_id is not None else 0.25,
            evidence,
        )

    @staticmethod
    def classify_out_of_view() -> RuleClassification:
        return RuleClassification(
            BehaviorState.OUT_OF_VIEW,
            (),
            0.8,
            {"source": "track_timeout", "sleeping_excluded": True},
        )


class BehaviorEngine:
    """將規則分類、hysteresis 與事件合併串成一個可測試的引擎。"""

    def __init__(self, config: BehaviorConfig | None = None) -> None:
        self.config = config or BehaviorConfig()
        self.classifier = RuleBehaviorClassifier(self.config)
        self.states = BehaviorStateMachine(switch_minimum_seconds=self.config.state_switch_minimum_seconds)
        self.events = BehaviorEventMerger(
            default_merge_gap_seconds=self.config.meal_merge_gap_seconds,
            merge_gaps={
                BehaviorState.EATING: self.config.meal_merge_gap_seconds,
                BehaviorState.DRINKING: self.config.drinking_merge_gap_seconds,
                BehaviorState.UNKNOWN: 0.0,
                BehaviorState.OUT_OF_VIEW: 0.0,
            },
        )

    def observe(self, feature: TrackFeature) -> tuple[BehaviorObservation, tuple[BehaviorEvent, ...]]:
        classification = self.classifier.classify(feature)
        stable = self.states.update(feature.track_id, classification.behavior_state, feature.observed_at)
        observation = BehaviorObservation(
            pet_id=feature.pet_id,
            track_id=feature.track_id,
            behavior_state=stable,
            motion_state=feature.motion_state,
            event_tags=classification.event_tags,
            observed_at=feature.observed_at,
            raw_confidence=classification.confidence,
            smoothed_confidence=classification.confidence,
            inference_method="rules",
            model_version="rules-v0.2",
            rule_profile_version="mx250-v0.2",
            source_id=feature.source_id,
            camera_preset=feature.camera_preset,
            evidence=classification.evidence,
            identity_status="resolved" if feature.pet_id is not None else "unknown",
            identity_confidence=feature.identity_confidence,
        )
        return observation, self.events.ingest(observation)

    def observe_out_of_view(
        self,
        *,
        pet_id: str,
        track_id: str | int,
        observed_at: datetime,
        source_id: str,
        camera_preset: str | None = None,
    ) -> tuple[BehaviorObservation, tuple[BehaviorEvent, ...]]:
        classification = self.classifier.classify_out_of_view()
        # A track timeout is an explicit lifecycle signal, so it should not wait
        # for the regular visible-state hysteresis window.
        stable = classification.behavior_state
        observation = BehaviorObservation(
            pet_id=pet_id,
            track_id=track_id,
            behavior_state=stable,
            motion_state=MotionState.STATIONARY,
            event_tags=(),
            observed_at=observed_at,
            raw_confidence=classification.confidence,
            smoothed_confidence=classification.confidence,
            inference_method="track-timeout",
            model_version="rules-v0.2",
            rule_profile_version="mx250-v0.2",
            source_id=source_id,
            camera_preset=camera_preset,
            evidence=classification.evidence,
            identity_status="resolved" if pet_id else "unknown",
            identity_confidence=1.0 if pet_id else 0.0,
        )
        return observation, self.events.ingest(observation)

    def flush(self):
        return self.events.flush()

    def active_event(self, track_id: str | int) -> BehaviorEvent | None:
        return self.events.active_event(track_id)

    def update_identity(
        self,
        track_id: str | int,
        *,
        pet_id: str | None,
        confidence: float,
        model_version: str | None,
        resolved_at: datetime,
    ) -> BehaviorEvent | None:
        """Apply a completed low-priority identity result to the open event."""

        return self.events.update_identity(
            track_id,
            pet_id=pet_id,
            confidence=confidence,
            model_version=model_version,
            resolved_at=resolved_at,
        )

    def reset(self) -> tuple[BehaviorEvent, ...]:
        self.states.reset()
        return self.events.flush()
