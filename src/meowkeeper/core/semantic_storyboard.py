"""既有貓框裁切的有界短期快取；組圖只在低優先分析 worker 執行。"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO


@dataclass(frozen=True)
class FrameEvidence:
    track_id: str
    epoch: str
    captured_at: datetime
    jpeg: bytes
    zones: tuple[tuple[str,str,str], ...] = ()


@dataclass(frozen=True)
class TrackStoryboard:
    track_id: str
    alias: str
    jpeg: bytes
    offsets_seconds: tuple[float, ...]
    epoch: str
    zones: tuple[tuple[int,str,str,str], ...] = ()

    def __post_init__(self):
        if (not isinstance(self.track_id, str) or not 1 <= len(self.track_id) <= 128
                or not re.fullmatch(r"track_0[1-8]", self.alias)
                or not self.epoch or not self.jpeg or len(self.jpeg) > 512 * 1024
                or not 2 <= len(self.offsets_seconds) <= 8
                or any(type(value) not in {int, float} or not math.isfinite(value) or not -4 <= value <= 2
                       for value in self.offsets_seconds)
                or any(right - left < .249 for left, right in zip(self.offsets_seconds, self.offsets_seconds[1:]))):
            raise ValueError("時序參考圖的軌跡或觀察時間不正確。")
        if len(self.zones) > 384 or any(type(index) is not int or not 0 <= index < len(self.offsets_seconds)
            or not re.fullmatch(r"[0-9a-f-]{36}",view) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",zone)
            or kind not in {"food","water","bed","litter_box","litter","play_area","play"} for index,view,zone,kind in self.zones):
            raise ValueError("時序參考圖的區域對位證據不正確。")


def build_storyboards(samples: tuple[FrameEvidence, ...], tracks: tuple[str, ...],
                      trigger_at: datetime, *, frame_count: int = 4, canvas_size: str = "1536x1024",
                      jpeg_quality: int = 82, include_timestamps: bool = True,
                      include_zones: bool = True) -> tuple[TrackStoryboard, ...]:
    """時間為相對事件觸發的觀察點，不冒充 MP4 PTS 或精準動作片段。"""
    from PIL import Image, ImageDraw, ImageOps

    sizes = {"1280x960": (1280, 960), "1536x1024": (1536, 1024), "2048x1365": (2048, 1365)}
    if (not tracks or len(tracks) > 8 or len(set(tracks)) != len(tracks)
            or type(frame_count) is not int or not 2 <= frame_count <= 8
            or canvas_size not in sizes or type(jpeg_quality) is not int or not 60 <= jpeg_quality <= 95
            or type(include_timestamps) is not bool or type(include_zones) is not bool):
        return ()
    boards = []
    epochs = {sample.epoch for sample in samples if sample.track_id in tracks}
    if len(epochs) != 1 or not next(iter(epochs), ""):
        return ()
    for number, track in enumerate(tracks, 1):
        selected = []
        hashes = set()
        for sample in sorted((sample for sample in samples if sample.track_id == track),
                             key=lambda sample: sample.captured_at):
            offset = (sample.captured_at - trigger_at).total_seconds()
            digest = hashlib.sha256(sample.jpeg).digest()
            if not -4 <= offset <= 2 or digest in hashes:
                continue
            if selected and (sample.captured_at - selected[-1].captured_at).total_seconds() < .25:
                continue
            try:
                with Image.open(BytesIO(sample.jpeg)) as image:
                    if image.width < 48 or image.height < 48 or image.width * image.height > 4_000_000:
                        continue
                    image.verify()
            except (OSError, ValueError):
                continue
            selected.append(sample)
            hashes.add(digest)
        selected = selected[-frame_count:]
        if len(selected) < frame_count or (selected[-1].captured_at - selected[0].captured_at).total_seconds() < .25:
            return ()  # 不暗中漏掉其中一隻貓，改為本次證據不足。
        width, height = sizes[canvas_size]
        rows = (len(selected) + 1) // 2
        cell_width, cell_height = width // 2, height // rows
        sheet = Image.new("RGB", (width, height), "#f4ede3")
        draw = ImageDraw.Draw(sheet)
        offsets = []
        zones = []
        for index, sample in enumerate(selected):
            offset = round((sample.captured_at - trigger_at).total_seconds(), 3)
            offsets.append(offset)
            if include_zones:
                zones.extend((index,view,zone,kind) for view,zone,kind in sample.zones)
            x, y = (index % 2) * cell_width, (index // 2) * cell_height
            label_height = 28 if include_timestamps else 8
            if include_timestamps:
                draw.text((x + 8, y + 6), f"Track {number} | frame {index} | {offset:+.3f}s", fill="#302a24")
            with Image.open(BytesIO(sample.jpeg)) as image:
                thumb = ImageOps.contain(image.convert("RGB"), (cell_width - 16, cell_height - label_height - 8))
                sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + label_height))
        output = BytesIO()
        sheet.save(output, format="JPEG", quality=jpeg_quality)
        boards.append(TrackStoryboard(track, f"track_{number:02d}", output.getvalue(),
                                      tuple(offsets), selected[0].epoch,tuple(zones)))
    return tuple(boards) if sum(len(board.jpeg) for board in boards) <= 4 * 1024 * 1024 else ()


class FrameEvidenceBuffer:
    """最多 64 個既有裁切／8 MB；通知或錄影不等待它。"""
    def __init__(self, storyboard_options=None):
        self._samples: deque[FrameEvidence] = deque()
        self._bytes = 0
        self._condition = threading.Condition()
        self._closed = False
        self._storyboard_options = dict(storyboard_options or {})

    def observe(self, samples: tuple[FrameEvidence, ...]) -> None:
        with self._condition:
            if self._closed:
                return
            for sample in samples:
                if (sample.captured_at.tzinfo is None or not sample.epoch or not sample.jpeg
                        or len(sample.jpeg) > 256 * 1024):
                    continue
                self._samples.append(sample)
                self._bytes += len(sample.jpeg)
                while len(self._samples) > 64 or self._bytes > 8 * 1024 * 1024:
                    self._bytes -= len(self._samples.popleft().jpeg)
            self._condition.notify_all()

    def collect(self, tracks: tuple[str, ...], trigger_at: datetime, *, wait_seconds: float = 2):
        if trigger_at.tzinfo is None or not 0 <= wait_seconds <= 2:
            raise ValueError("分析觀察視窗設定不正確。")
        deadline = time.monotonic() + wait_seconds
        while True:
            with self._condition:
                if self._closed:
                    return ()
                samples = tuple(self._samples)
            boards = build_storyboards(samples, tracks, trigger_at, **self._storyboard_options)
            if boards:
                return boards
            with self._condition:
                remaining = deadline - time.monotonic()
                if self._closed or remaining <= 0:
                    return ()
                self._condition.wait(min(.25, remaining))

    def close(self):
        with self._condition:
            self._closed = True
            self._samples.clear()
            self._bytes = 0
            self._condition.notify_all()
