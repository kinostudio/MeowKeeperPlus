"""事件合併、冷卻、前後錄與人工確認。"""
"""事件合併、冷卻、通知與人工確認。"""

from .analysis_pipeline import AnalysisPipeline, CooldownGate, PipelineEvent

__all__ = ["AnalysisPipeline", "CooldownGate", "PipelineEvent"]
from .v02_pipeline import V02AnalysisPipeline, V02FrameResult, V02TrackResult

__all__ = ["V02AnalysisPipeline", "V02FrameResult", "V02TrackResult"]
