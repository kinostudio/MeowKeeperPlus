"""只以獨立影格證據建立短期、實驗性身分共識。"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, replace
from datetime import datetime
from statistics import median

from .base import IdentityPrediction, IdentityThresholds


@dataclass
class _TrackIdentityState:
    samples: deque
    latest: IdentityPrediction | None = None


class TemporalIdentity:
    def __init__(self, thresholds: IdentityThresholds | None = None, *, minimum_unique_frames=5,
                 minimum_time_bins=3, minimum_vote_fraction=0.75) -> None:
        self.thresholds = thresholds or IdentityThresholds()
        self.thresholds.validate()
        if minimum_unique_frames < 1 or minimum_time_bins < 1 or not 0 < minimum_vote_fraction <= 1:
            raise ValueError("身分共識參數不合法。")
        self.minimum_unique_frames = minimum_unique_frames
        self.minimum_time_bins = minimum_time_bins
        self.minimum_vote_fraction = minimum_vote_fraction
        self._states = {}

    def update(self, track_id, prediction, *, captured_at: datetime | None = None,
               frame_id: str | None = None, conflict: bool = False) -> IdentityPrediction:
        state = self._states.setdefault(track_id, _TrackIdentityState(
            deque(maxlen=max(12, self.thresholds.temporal_vote_window))))
        while len(self._states) > 128:
            self._states.pop(next(iter(self._states)))
        def signature(value):
            return (value.model_version, tuple(sorted(
                (c.get("pet_id", ""), c.get("gallery_version", "")) for c in value.candidates)))
        if state.samples and signature(state.samples[-1][2]) != signature(prediction):
            state.samples.clear()
            state.latest = None
        if captured_at is not None and captured_at.tzinfo is None:
            conflict = True
        if captured_at is not None:
            while state.samples and state.samples[0][1] is not None and (
                captured_at-state.samples[0][1]).total_seconds() > 12:
                state.samples.popleft()
        duplicate = any((frame_id is not None and frame_id == key) or (
            captured_at is not None and captured_at == timestamp) for key, timestamp, _ in state.samples)
        if not duplicate:
            state.samples.append((frame_id, captured_at, prediction))
        samples = list(state.samples)
        total = len(samples)
        times = [timestamp for _, timestamp, _ in samples if timestamp is not None]
        seconds = (max(times)-min(times)).total_seconds() if times else 0.
        bins = len({int(timestamp.timestamp()) for timestamp in times})
        known = [p for _, _, p in samples if not p.is_unknown]
        counts = Counter(p.predicted_pet_id for p in known)
        winner_id, votes = counts.most_common(1)[0] if counts else (None, 0)
        fraction = votes/total if total else 0.
        reason = None
        if conflict:
            reason = "track_ambiguous"
        elif (total < (self.minimum_unique_frames if times else self.thresholds.minimum_valid_samples)
              or times and (bins < self.minimum_time_bins or seconds < self.minimum_time_bins-1)):
            reason = "insufficient_evidence"
        elif fraction < self.minimum_vote_fraction:
            reason = "unstable_votes"
        if reason or winner_id is None:
            state.latest = replace(prediction, predicted_pet_id=None, predicted_name=None, stable=False,
                                   unknown_reason=reason or prediction.unknown_reason or "unstable_votes",
                                   reason="獨立影格證據不足或不一致，保留身分尚未確認。",
                                   valid_frames=total, vote_fraction=fraction, evidence_seconds=seconds)
        else:
            winner = next(p for p in reversed(known) if p.predicted_pet_id == winner_id)
            winning_samples = [p for p in known if p.predicted_pet_id == winner_id]
            state.latest = replace(winner, stable=True, unknown_reason=None,
                                   similarity=median(p.similarity for p in winning_samples),
                                   confidence_margin=median(p.confidence_margin for p in winning_samples),
                                   reason="多影格一致的實驗性候選，非家庭校準確認。",
                                   valid_frames=total, vote_fraction=fraction, evidence_seconds=seconds)
        return state.latest

    def reset(self, track_id=None) -> None:
        if track_id is None:
            self._states.clear()
        else:
            self._states.pop(track_id, None)
