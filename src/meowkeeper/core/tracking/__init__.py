"""短期多貓追蹤與動作特徵。"""

from .base import BoundingBox, TrackingBackend, TrackObservation
from .botsort_tracker import BoTSORTTracker, TrackingConfigurationError
from .motion_features import MotionThresholds, classify_motion
from .track_history import MotionFeatures, TrackHistory

__all__ = [
    "BoTSORTTracker",
    "BoundingBox",
    "MotionFeatures",
    "MotionThresholds",
    "TrackHistory",
    "TrackObservation",
    "TrackingBackend",
    "TrackingConfigurationError",
    "classify_motion",
]
