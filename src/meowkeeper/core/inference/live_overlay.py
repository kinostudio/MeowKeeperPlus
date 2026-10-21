"""短暫保留最新正向即時偵測，避免輪詢間隔造成框閃爍。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

LIVE_OVERLAY_TTL_SECONDS = 2.0


def update_live_overlay(
    previous: Mapping[str, Any] | None,
    detections: Sequence[Mapping[str, Any]] | None,
    *,
    source_epoch: str,
    captured_at: datetime,
    ttl_seconds: float = LIVE_OVERLAY_TTL_SECONDS,
) -> dict[str, Any] | None:
    """用新正向結果更新疊圖；空結果只保留仍新鮮、同來源的前一個正向結果。

    這是前端即時顯示的短效取樣，不會把舊框延伸成事件證據，也不跨監控工作階段。
    時間倒退、時間戳無效、來源 epoch 改變或超過 TTL 時一律清除。
    """
    current = (
        list(detections[:20])
        if isinstance(detections, Sequence) and not isinstance(detections, (str, bytes))
        else []
    )
    if current:
        return {
            "source_epoch": source_epoch,
            "captured_at": captured_at.isoformat(),
            "detections": current,
        }

    if (
        not isinstance(previous, Mapping)
        or previous.get("source_epoch") != source_epoch
        or not isinstance(previous.get("detections"), list)
        or not previous["detections"]
    ):
        return None

    try:
        previous_time = datetime.fromisoformat(str(previous["captured_at"]))
        age_seconds = captured_at.timestamp() - previous_time.timestamp()
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not 0 <= age_seconds <= max(0.0, ttl_seconds):
        return None
    return dict(previous)
