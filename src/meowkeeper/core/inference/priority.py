"""推論工作優先級；數值越小越先執行。"""

from __future__ import annotations

from enum import IntEnum


class InferencePriority(IntEnum):
    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    BACKGROUND = 40

    @property
    def label_zh_tw(self) -> str:
        return {
            InferencePriority.CRITICAL: "必要異常分析",
            InferencePriority.HIGH: "貓咪偵測與主要行為",
            InferencePriority.NORMAL: "短片動作確認",
            InferencePriority.LOW: "身分辨識",
            InferencePriority.BACKGROUND: "背景統計",
        }[self]


Priority = InferencePriority
