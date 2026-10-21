"""短期多目標追蹤的共用資料契約。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from meowkeeper.core.media.sources import FramePacket


@dataclass(frozen=True)
class BoundingBox:
    """影像像素座標的左上／右下框。"""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def normalized(self, frame_width: int, frame_height: int) -> tuple[float, float, float, float]:
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError("影像尺寸必須大於零。")
        return (
            self.x1 / frame_width,
            self.y1 / frame_height,
            self.x2 / frame_width,
            self.y2 / frame_height,
        )


@dataclass(frozen=True)
class TrackObservation:
    """追蹤器輸出的單次觀測；track_id 不代表永久寵物身分。"""

    track_id: str | int
    bbox: BoundingBox
    captured_at: datetime
    confidence: float
    source_id: str
    frame_width: int
    frame_height: int
    crop_jpeg: bytes | None = None


class TrackingBackend(Protocol):
    """可替換追蹤器介面。"""

    def update(self, frame: FramePacket) -> list[TrackObservation]:
        ...

    def reset(self, reason: str = "manual") -> None:
        ...
