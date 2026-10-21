"""影格分析、冷卻去重與 Telegram 事件推播。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from meowkeeper.core.detection.frame_analysis import FrameAnalysis, FrameAnalyzer
from meowkeeper.core.media.sources import FramePacket
from meowkeeper.core.notifications.telegram import (
    TelegramNotificationError,
    TelegramNotifier,
    TelegramSendResult,
)


@dataclass(frozen=True)
class PipelineEvent:
    """一次觸發結果與各 Chat ID 的傳送結果。"""

    frame: FramePacket
    analysis: FrameAnalysis
    sent: tuple[TelegramSendResult, ...] = ()
    errors: tuple[str, ...] = ()


class CooldownGate:
    """依事件類型抑制重複通知，不刪除或修改原始分析結果。"""

    def __init__(self, cooldown_seconds: float = 180.0) -> None:
        if cooldown_seconds < 0:
            raise ValueError("通知冷卻秒數不可小於零。")
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self._last_sent: dict[str, datetime] = {}

    def allow(self, event_type: str, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        previous = self._last_sent.get(event_type)
        if previous is not None and current - previous < self.cooldown:
            return False
        self._last_sent[event_type] = current
        return True


class AnalysisPipeline:
    """將來源影格送入分析器，並在事件觸發時選擇性發送圖片。"""

    def __init__(
        self,
        analyzer: FrameAnalyzer,
        *,
        notifier: TelegramNotifier | None = None,
        chat_ids: Iterable[str] = (),
        cooldown_seconds: float = 180.0,
    ) -> None:
        self.analyzer = analyzer
        self.notifier = notifier
        self.chat_ids = tuple(str(chat_id) for chat_id in chat_ids if str(chat_id).strip())
        self.cooldown = CooldownGate(cooldown_seconds)
        self.last_analysis: FrameAnalysis | None = None

    def process(self, frame: FramePacket) -> PipelineEvent | None:
        analysis = self.analyzer.analyze(frame)
        self.last_analysis = analysis
        if not analysis.triggered or not analysis.event_type:
            return None
        if not self.cooldown.allow(analysis.event_type, frame.captured_at):
            return None

        sent: list[TelegramSendResult] = []
        errors: list[str] = []
        if self.notifier and self.chat_ids:
            caption = (
                f"喵喵守護者+ 影像事件\n{analysis.message_zh_tw}\n"
                f"時間：{frame.captured_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            for chat_id in self.chat_ids:
                try:
                    sent.append(self.notifier.send_photo(chat_id, frame.jpeg_bytes, caption=caption))
                except (TelegramNotificationError, OSError, ValueError) as exc:
                    errors.append(f"Chat ID {chat_id}：{type(exc).__name__}")
        return PipelineEvent(frame, analysis, tuple(sent), tuple(errors))

    def run(self, frames: Iterable[FramePacket], *, max_frames: int | None = None) -> list[PipelineEvent]:
        events: list[PipelineEvent] = []
        for index, frame in enumerate(frames, start=1):
            event = self.process(frame)
            if event is not None:
                events.append(event)
            if max_frames is not None and index >= max_frames:
                break
        return events
