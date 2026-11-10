"""Reach a saved PTZ pose with bounded short pulses and verified readback.

No Xiaomi model has a verified absolute-move command yet, but several have a
verified position readback.  This loop only uses the already-proven pieces:
one short relative pulse (always followed by stop), then a fresh readback.
It learns each axis direction from the readback instead of assuming it, and
stops immediately when the readback is missing, the motor stalls, moves
inconsistently or makes no progress.  It never extrapolates a position.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

# (direction assumed to increase the coordinate, direction assumed to decrease it)
AXES: dict[str, tuple[str, str]] = {"angle": ("right", "left"), "elevation": ("up", "down")}
DEFAULT_TOLERANCE = 2
DEFAULT_MAX_PULSES = 40
DEFAULT_TIMEOUT_SECONDS = 45.0
STALL_PULSES = 3
NO_PROGRESS_PULSES = 4

REASONS_ZH_TW = {
    "reached": "已逐步移到目標位置，並由裝置讀回確認。",
    "readback_unavailable": "移動前沒有可驗證的位置讀回；未移動鏡頭。",
    "readback_lost": "移動途中位置讀回失敗；已停止，未宣稱到位。",
    "pulse_unconfirmed": "短步移動或停止未確認；已停止，請檢查畫面。",
    "axis_stalled": "連續數步座標沒有變化（可能已到機械極限或馬達未轉）；已停止。",
    "direction_inconsistent": "移動方向與讀回不一致；已停止，未繼續推算。",
    "no_progress": "多步後距離沒有縮短；已停止避免來回擺動。",
    "max_pulses": "已達最多步數仍未到位；已停止。",
    "timeout": "定位超過時限；已停止。",
    "invalid_target": "目標座標不在 1～101；未移動鏡頭。",
}


@dataclass
class ClosedLoopResult:
    success: bool
    reason: str
    position: dict[str, int] | None
    pulses: int = 0
    axis: str | None = None
    learned_inverted: dict[str, bool] = field(default_factory=dict)

    @property
    def message_zh_tw(self) -> str:
        return REASONS_ZH_TW.get(self.reason, "定位未完成；已停止。")


def _valid(position: Mapping[str, object] | None) -> dict[str, int] | None:
    if not isinstance(position, Mapping):
        return None
    result = {axis: position.get(axis) for axis in AXES}
    if any(type(value) is not int or not 0 <= value <= 101 for value in result.values()):
        return None
    return result  # type: ignore[return-value]


def near(first: Mapping[str, object] | None, second: Mapping[str, object] | None,
         tolerance: int = DEFAULT_TOLERANCE) -> bool:
    a, b = _valid(first), _valid(second)
    return a is not None and b is not None and all(abs(a[axis] - b[axis]) <= tolerance for axis in AXES)


def move_to(
    target: Mapping[str, int],
    *,
    read: Callable[[], Mapping[str, object] | None],
    pulse: Callable[[str], bool],
    tolerance: int = DEFAULT_TOLERANCE,
    max_pulses: int = DEFAULT_MAX_PULSES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> ClosedLoopResult:
    goal = _valid(target)
    if goal is None or any(not 1 <= goal[axis] <= 101 for axis in AXES):
        return ClosedLoopResult(False, "invalid_target", None)
    position = _valid(read())
    if position is None:
        return ClosedLoopResult(False, "readback_unavailable", None)
    deadline = clock() + timeout_seconds
    sign = {axis: 1 for axis in AXES}
    flipped = {axis: False for axis in AXES}
    stalls = {axis: 0 for axis in AXES}
    best = {axis: abs(goal[axis] - position[axis]) for axis in AXES}
    no_progress = {axis: 0 for axis in AXES}
    pulses = 0

    def result(success: bool, reason: str, axis: str | None = None) -> ClosedLoopResult:
        return ClosedLoopResult(success, reason, position, pulses, axis,
                                {name: sign[name] < 0 for name in AXES})

    while True:
        pending = [axis for axis in AXES if abs(goal[axis] - position[axis]) > tolerance]
        if not pending:
            return result(True, "reached")
        if pulses >= max_pulses:
            return result(False, "max_pulses")
        if clock() >= deadline:
            return result(False, "timeout")
        axis = max(pending, key=lambda name: abs(goal[name] - position[name]))
        increase = goal[axis] > position[axis]
        forward, backward = AXES[axis]
        direction = forward if increase == (sign[axis] > 0) else backward
        if not pulse(direction):
            return result(False, "pulse_unconfirmed", axis)
        pulses += 1
        reading = _valid(read())
        if reading is None:
            return result(False, "readback_lost", axis)
        delta = reading[axis] - position[axis]
        position = reading
        if delta == 0:
            stalls[axis] += 1
            if stalls[axis] >= STALL_PULSES:
                return result(False, "axis_stalled", axis)
        elif (delta > 0) != increase:
            if flipped[axis]:
                return result(False, "direction_inconsistent", axis)
            # First readback shows this model maps the axis the other way.
            sign[axis] = -sign[axis]
            flipped[axis] = True
            stalls[axis] = 0
            continue
        else:
            stalls[axis] = 0
        error = abs(goal[axis] - position[axis])
        if error < best[axis]:
            best[axis] = error
            no_progress[axis] = 0
        else:
            no_progress[axis] += 1
            if no_progress[axis] >= NO_PROGRESS_PULSES:
                return result(False, "no_progress", axis)
