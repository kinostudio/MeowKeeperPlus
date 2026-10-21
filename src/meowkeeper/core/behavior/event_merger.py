"""行為事件開始／結束與短暫中斷合併。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from .models import BehaviorEvent, BehaviorObservation


class BehaviorEventMerger:
    def __init__(
        self,
        *,
        default_merge_gap_seconds: float = 30.0,
        merge_gaps: dict[object, float] | None = None,
    ) -> None:
        if default_merge_gap_seconds < 0:
            raise ValueError("事件合併時間不可小於零。")
        self.default_merge_gap_seconds = default_merge_gap_seconds
        self.merge_gaps = dict(merge_gaps or {})
        if any(value < 0 for value in self.merge_gaps.values()):
            raise ValueError("事件合併時間不可小於零。")
        # A track is the stable key while identity is being resolved.  Using
        # ``(pet_id, track_id)`` would split one anonymous event into a second
        # event as soon as DINO assigns a name.
        self._active: dict[str | int, BehaviorEvent] = {}
        self._last_seen: dict[str | int, datetime] = {}
        self._pending_state: dict[str | int, object] = {}
        self._pending_since: dict[str | int, datetime] = {}

    def ingest(self, observation: BehaviorObservation) -> tuple[BehaviorEvent, ...]:
        key = observation.track_id
        active = self._active.get(key)
        if active is None:
            self._active[key] = self._start(observation)
            self._last_seen[key] = observation.observed_at
            return ()
        if observation.behavior_state == active.behavior_state:
            active.ended_at = observation.observed_at
            active.raw_confidence = observation.raw_confidence
            active.smoothed_confidence = observation.smoothed_confidence
            self._pending_state.pop(key, None)
            self._pending_since.pop(key, None)
            self._last_seen[key] = observation.observed_at
            return ()
        pending = self._pending_state.get(key)
        if pending != observation.behavior_state:
            self._pending_state[key] = observation.behavior_state
            self._pending_since[key] = observation.observed_at
            return ()
        merge_gap = self.merge_gaps.get(active.behavior_state, self.default_merge_gap_seconds)
        if (observation.observed_at - self._last_seen[key]).total_seconds() <= merge_gap:
            return ()
        finished = replace(active, ended_at=self._last_seen[key])
        pending_since = self._pending_since.get(key, observation.observed_at)
        started = self._start(observation)
        started.started_at = pending_since
        self._active[key] = started
        self._last_seen[key] = observation.observed_at
        self._pending_state.pop(key, None)
        self._pending_since.pop(key, None)
        return (finished,)

    def flush(self, ended_at=None) -> tuple[BehaviorEvent, ...]:
        events: list[BehaviorEvent] = []
        for key, active in list(self._active.items()):
            end = ended_at or self._last_seen.get(key) or active.started_at
            events.append(replace(active, ended_at=end))
        self._active.clear()
        self._last_seen.clear()
        self._pending_state.clear()
        self._pending_since.clear()
        return tuple(events)

    def active_event(self, track_id: str | int) -> BehaviorEvent | None:
        """Return the mutable in-flight event for a track, if any."""

        return self._active.get(track_id)

    def active_events(self) -> tuple[BehaviorEvent, ...]:
        return tuple(self._active.values())

    def update_identity(
        self,
        track_id: str | int,
        *,
        pet_id: str | None,
        confidence: float,
        model_version: str | None,
        resolved_at: datetime,
    ) -> BehaviorEvent | None:
        """Late-bind the identity without replacing the event id or timeline."""

        event = self._active.get(track_id)
        if event is None:
            return None
        event.pet_id = pet_id
        event.identity_status = "resolved" if pet_id is not None else "unknown"
        event.identity_confidence = max(0.0, min(1.0, float(confidence)))
        event.identity_resolved_at = resolved_at if pet_id is not None else None
        event.identity_model_version = model_version
        event.evidence = {
            **event.evidence,
            "identity_resolution": {
                "status": event.identity_status,
                "confidence": event.identity_confidence,
                "model_version": model_version,
            },
        }
        return event

    @staticmethod
    def _start(observation: BehaviorObservation) -> BehaviorEvent:
        return BehaviorEvent(
            pet_id=observation.pet_id,
            track_id=observation.track_id,
            behavior_state=observation.behavior_state,
            motion_state=observation.motion_state,
            event_tags=observation.event_tags,
            started_at=observation.observed_at,
            ended_at=observation.observed_at,
            raw_confidence=observation.raw_confidence,
            smoothed_confidence=observation.smoothed_confidence,
            inference_method=observation.inference_method,
            model_version=observation.model_version,
            rule_profile_version=observation.rule_profile_version,
            source_id=observation.source_id,
            camera_preset=observation.camera_preset,
            evidence=dict(observation.evidence),
            identity_status=observation.identity_status,
            identity_confidence=observation.identity_confidence,
            identity_model_version=observation.identity_model_version,
        )
