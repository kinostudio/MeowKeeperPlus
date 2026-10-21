"""有限狀態與 hysteresis，避免行為逐影格抖動。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meowkeeper.domain.behavior import BehaviorState


@dataclass
class _State:
    current: BehaviorState = BehaviorState.UNKNOWN
    candidate: BehaviorState = BehaviorState.UNKNOWN
    candidate_since: datetime | None = None


class BehaviorStateMachine:
    def __init__(self, *, switch_minimum_seconds: float = 2.0) -> None:
        if switch_minimum_seconds < 0:
            raise ValueError("行為切換遲滯時間不可小於零。")
        self.switch_minimum_seconds = switch_minimum_seconds
        self._states: dict[str | int, _State] = {}

    def update(self, key: str | int, candidate: BehaviorState, observed_at: datetime) -> BehaviorState:
        state = self._states.setdefault(key, _State())
        if candidate == state.current:
            state.candidate = candidate
            state.candidate_since = None
            return state.current
        if candidate != state.candidate or state.candidate_since is None:
            state.candidate = candidate
            state.candidate_since = observed_at
            if self.switch_minimum_seconds == 0:
                state.current = candidate
            return state.current
        elapsed = (observed_at - state.candidate_since).total_seconds()
        if elapsed >= self.switch_minimum_seconds:
            state.current = candidate
            state.candidate_since = None
        return state.current

    def reset(self, key: str | int | None = None) -> None:
        if key is None:
            self._states.clear()
        else:
            self._states.pop(key, None)
