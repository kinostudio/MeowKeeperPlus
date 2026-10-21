"""行為規則分數的保守校正。"""

from __future__ import annotations


def bounded_confidence(*scores: float) -> float:
    """將可用證據的平均值限制在 0 到 1；沒有證據時回傳 0。"""

    valid = [max(0.0, min(1.0, float(score))) for score in scores]
    return sum(valid) / len(valid) if valid else 0.0
