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
    def __init__(self, thresholds: IdentityThresholds | None = None, *, minimum_unique_frames=None,
                 minimum_time_bins=None, minimum_vote_fraction=None) -> None:
        self.thresholds = thresholds or IdentityThresholds()
        self.thresholds.validate()
        self.minimum_unique_frames = (
            minimum_unique_frames if minimum_unique_frames is not None
            else getattr(self.thresholds, "minimum_unique_frames", 2)
        )
        self.minimum_time_bins = (
            minimum_time_bins if minimum_time_bins is not None
            else getattr(self.thresholds, "minimum_time_bins", 1)
        )
        self.minimum_vote_fraction = (
            minimum_vote_fraction if minimum_vote_fraction is not None
            else getattr(self.thresholds, "minimum_vote_fraction", 0.70)
        )
        if self.minimum_unique_frames < 1 or self.minimum_time_bins < 1 or not 0 < self.minimum_vote_fraction <= 1:
            raise ValueError("身分共識參數不合法。")
        self._states = {}

    def inherit_history(self, from_track_id: object, to_track_id: object) -> bool:
        """跨微小跳號平滑關聯：若同一位置 1 秒內追蹤號碼跳號，將身分特徵樣本自動平滑接續。"""
        src_samples = []
        for k, v in list(self._states.items()):
            if k == from_track_id or (isinstance(k, tuple) and len(k) == 2 and k[1] == from_track_id):
                if v and v.samples:
                    for s in v.samples:
                        if s not in src_samples:
                            src_samples.append(s)
        if not src_samples:
            return False

        target_keys = [k for k in self._states if k == to_track_id or (isinstance(k, tuple) and len(k) == 2 and k[1] == to_track_id)]
        if not target_keys:
            target_keys = [to_track_id]

        transferred = 0
        for tk in target_keys:
            dst = self._states.setdefault(tk, _TrackIdentityState(
                deque(maxlen=max(12, self.thresholds.temporal_vote_window))))
            for sample in src_samples:
                if sample not in dst.samples:
                    dst.samples.append(sample)
                    transferred += 1
        return transferred > 0

    def update(self, track_id, prediction, *, captured_at: datetime | None = None,
               frame_id: str | None = None, conflict: bool = False) -> IdentityPrediction:
        state = self._states.setdefault(track_id, _TrackIdentityState(
            deque(maxlen=max(12, self.thresholds.temporal_vote_window))))
        if not state.samples and isinstance(track_id, tuple) and len(track_id) == 2:
            bare_state = self._states.get(track_id[1])
            if bare_state and bare_state.samples:
                for s in bare_state.samples:
                    if s not in state.samples:
                        state.samples.append(s)
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
                                   valid_frames=total, vote_fraction=fraction, evidence_seconds=seconds, posture=prediction.posture or winner.posture)
        return state.latest

    def reset(self, track_id=None) -> None:
        if track_id is None:
            self._states.clear()
        else:
            self._states.pop(track_id, None)
