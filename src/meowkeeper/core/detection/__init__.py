"""可替換的視覺與音訊偵測後端介面。"""

from .frame_analysis import (
    AnalysisConfigurationError,
    FrameAnalysis,
    FrameAnalyzer,
    NoopFrameAnalyzer,
    YoloFrameAnalyzer,
)

__all__ = [
    "AnalysisConfigurationError",
    "FrameAnalysis",
    "FrameAnalyzer",
    "NoopFrameAnalyzer",
    "YoloFrameAnalyzer",
]
