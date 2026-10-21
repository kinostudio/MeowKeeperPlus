"""不依賴 GUI、媒體框架或網路服務的領域契約。"""

from .behavior import BehaviorState, EventTag, MotionState
from .settings import (
    COCO_CAT_CLASS_ID,
    DEFAULT_VISION_ANALYSIS,
    MX250_MINIMUM,
    MediaBufferSettings,
    PerformanceProfile,
    VisionAnalysisSettings,
    get_performance_profile,
)

__all__ = [
    "COCO_CAT_CLASS_ID",
    "DEFAULT_VISION_ANALYSIS",
    "MX250_MINIMUM",
    "BehaviorState",
    "EventTag",
    "MediaBufferSettings",
    "MotionState",
    "PerformanceProfile",
    "VisionAnalysisSettings",
    "get_performance_profile",
]
