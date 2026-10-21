"""參考影像品質與重複排除。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualityResult:
    accepted: bool
    reason: str
    width: int = 0
    height: int = 0
    blur_score: float = 0.0
    duplicate: bool = False


class ReferenceQualityFilter:
    """排除過小、模糊、無法解碼與高度重複的參考 crop。"""

    def __init__(
        self,
        *,
        minimum_width: int = 96,
        minimum_height: int = 96,
        minimum_blur_score: float = 20.0,
        duplicate_distance: int = 4,
    ) -> None:
        self.minimum_width = minimum_width
        self.minimum_height = minimum_height
        self.minimum_blur_score = minimum_blur_score
        self.duplicate_distance = duplicate_distance
        self._hashes: list[Any] = []

    def assess(self, jpeg_bytes: bytes) -> QualityResult:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("參考影像品質檢查需要 opencv-python 與 numpy。") from exc
        image = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return QualityResult(False, "無法解碼影像。")
        height, width = image.shape[:2]
        if width < self.minimum_width or height < self.minimum_height:
            return QualityResult(False, "影像尺寸過小。", width, height)
        blur_score = float(cv2.Laplacian(image, cv2.CV_64F).var())
        if blur_score < self.minimum_blur_score:
            return QualityResult(False, "影像過於模糊。", width, height, blur_score)
        small = cv2.resize(image, (16, 16), interpolation=cv2.INTER_AREA)
        mean = float(small.mean())
        image_hash = small > mean
        for previous in self._hashes:
            distance = int(np.count_nonzero(image_hash != previous))
            if distance <= self.duplicate_distance:
                return QualityResult(True, "高度重複影像，已略過。", width, height, blur_score, True)
        self._hashes.append(image_hash)
        return QualityResult(True, "影像品質合格。", width, height, blur_score)
