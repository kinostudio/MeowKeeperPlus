"""R3D18 影格前處理；保留為可替換函式，不在 V0.2 自動下載權重。"""

from __future__ import annotations

from typing import Any


def resize_clip(frames: tuple[Any, ...], *, width: int = 112, height: int = 112) -> tuple[Any, ...]:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("時序影格前處理需要 opencv-python。") from exc
    return tuple(cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA) for frame in frames)
