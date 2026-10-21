"""磁碟影音分段索引與事件引用保護。

索引只保存壓縮分段的路徑與時間 metadata，不保存解碼後的 BGR 影格。事件
組片期間以 ``reference_count`` 鎖住分段，輪替器便不會刪掉正在使用的檔案。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class SegmentRecord:
    """一個可由 FFmpeg 產生的壓縮影音分段。"""

    path: Path
    start_at: datetime
    duration_seconds: float = 1.0
    size_bytes: int = 0
    keyframe: bool = False
    reference_count: int = 0

    def __post_init__(self) -> None:
        self.path = Path(self.path).resolve()
        if self.start_at.tzinfo is None:
            self.start_at = self.start_at.replace(tzinfo=timezone.utc)
        if self.duration_seconds <= 0:
            raise ValueError("影音分段長度必須大於零。")
        if self.size_bytes < 0:
            raise ValueError("影音分段大小不可小於零。")

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(seconds=self.duration_seconds)


class SegmentIndex:
    """執行緒安全的分段索引。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[Path, SegmentRecord] = {}

    def register(
        self,
        path: str | Path,
        *,
        start_at: datetime,
        duration_seconds: float = 1.0,
        size_bytes: int | None = None,
        keyframe: bool = False,
    ) -> SegmentRecord:
        candidate = Path(path).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        record = SegmentRecord(
            path=candidate,
            start_at=start_at,
            duration_seconds=duration_seconds,
            size_bytes=candidate.stat().st_size if size_bytes is None else size_bytes,
            keyframe=keyframe,
        )
        with self._lock:
            previous = self._records.get(candidate)
            if previous is not None:
                record.reference_count = previous.reference_count
            self._records[candidate] = record
        return record

    def get(self, path: str | Path) -> SegmentRecord | None:
        with self._lock:
            return self._records.get(Path(path).resolve())

    def all(self) -> tuple[SegmentRecord, ...]:
        with self._lock:
            return tuple(sorted(self._records.values(), key=lambda item: item.start_at))

    def between(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[SegmentRecord, ...]:
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        with self._lock:
            return tuple(
                record
                for record in sorted(self._records.values(), key=lambda item: item.start_at)
                if record.end_at >= start_at and record.start_at <= end_at
            )

    def acquire(self, records: Iterable[SegmentRecord]) -> tuple[SegmentRecord, ...]:
        acquired: list[SegmentRecord] = []
        with self._lock:
            for record in records:
                current = self._records.get(record.path)
                if current is None:
                    continue
                current.reference_count += 1
                acquired.append(current)
        return tuple(acquired)

    def release(self, records: Iterable[SegmentRecord]) -> None:
        with self._lock:
            for record in records:
                current = self._records.get(record.path)
                if current is not None and current.reference_count > 0:
                    current.reference_count -= 1

    def remove(self, path: str | Path) -> SegmentRecord | None:
        candidate = Path(path).resolve()
        with self._lock:
            record = self._records.get(candidate)
            if record is None or record.reference_count > 0:
                return None
            return self._records.pop(candidate)

    def prune_missing(self) -> tuple[Path, ...]:
        removed: list[Path] = []
        with self._lock:
            for path, record in list(self._records.items()):
                if not path.is_file() and record.reference_count == 0:
                    self._records.pop(path, None)
                    removed.append(path)
        return tuple(removed)
