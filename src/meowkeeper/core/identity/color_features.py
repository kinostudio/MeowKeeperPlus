"""低成本 HSV 毛色輔助特徵。"""

from __future__ import annotations

from typing import Any


def extract_hsv_histogram(crop: bytes | Any, *, bins_h: int = 16, bins_s: int = 8) -> Any:
    """從 JPEG bytes 或 BGR ndarray 取得 L2-normalized HSV 直方圖。"""

    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("毛色特徵需要 opencv-python 與 numpy。") from exc

    if isinstance(crop, (bytes, bytearray, memoryview)):
        image = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        image = crop
    if image is None:
        raise ValueError("無法解碼身分 crop。")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [bins_h, bins_s], [0, 180, 0, 256])
    vector = histogram.astype(np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def cosine_similarity(first: Any, second: Any) -> float:
    """計算兩個已知向量的 cosine similarity。"""

    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("毛色特徵需要 numpy。") from exc
    left = np.asarray(first, dtype=np.float32).reshape(-1)
    right = np.asarray(second, dtype=np.float32).reshape(-1)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 0 else 0.0
