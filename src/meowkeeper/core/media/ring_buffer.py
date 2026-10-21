"""以磁碟壓縮分段實作的環形影音快取。"""

from __future__ import annotations

import csv
import math
import threading
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meowkeeper.core.health.stability import inspect_disk_space

from .segment_index import SegmentIndex, SegmentRecord


@dataclass(frozen=True)
class RingBufferStatus:
    """環形快取的可觀測狀態。"""

    segment_count: int
    duration_seconds: float
    size_bytes: int
    retention_seconds: float
    maximum_size_bytes: int
    disk_below_minimum: bool
    warning: str | None = None


class DiskSegmentRingBuffer:
    """保存 MPEG-TS／fragmented MP4 分段並限制時間與容量。

    ``register_file`` 由分段監看器呼叫；測試也可以直接寫入合法的本地素材後
    註冊。輪替永遠跳過 ``reference_count`` 大於零的事件分段。
    """

    ALLOWED_SUFFIXES = frozenset({".ts", ".m4s", ".mp4", ".mkv"})

    def __init__(
        self,
        directory: str | Path,
        *,
        retention_seconds: float = 60.0,
        segment_seconds: float = 1.0,
        maximum_size_mb: int = 512,
        minimum_free_bytes: int = 0,
        enabled: bool = True,
    ) -> None:
        if not 10 <= retention_seconds <= 120:
            raise ValueError("環形快取保留時間必須介於 10 到 120 秒。")
        if segment_seconds <= 0:
            raise ValueError("影音分段長度必須大於零。")
        if maximum_size_mb <= 0:
            raise ValueError("環形快取容量上限必須大於零。")
        if minimum_free_bytes < 0:
            raise ValueError("最低剩餘空間不可小於零。")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.retention_seconds = float(retention_seconds)
        self.segment_seconds = float(segment_seconds)
        self.maximum_size_bytes = int(maximum_size_mb * 1024 * 1024)
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.enabled = bool(enabled)
        self.index = SegmentIndex()
        self._lock = threading.RLock()
        self._active_paths: set[Path] = set()
        self._writing_sessions: set[Path] = set()
        self._realtime_sessions: set[Path] = set()
        self._session_started_at: dict[Path, datetime] = {}
        self._warning: str | None = None

    def begin_session(self, directory: str | Path, *, realtime: bool = False) -> None:
        """Keep the segment FFmpeg is currently writing out of the index."""

        session = Path(directory).resolve()
        with self._lock:
            self._writing_sessions.add(session)
            self._session_started_at[session] = datetime.now(timezone.utc)
            if realtime:
                self._realtime_sessions.add(session)

    def end_session(self, directory: str | Path) -> None:
        """Index the final completed files after FFmpeg has exited."""

        session = Path(directory).resolve()
        with self._lock:
            self._writing_sessions.discard(session)
        try:
            self._remove_empty_segments(session)
            self.scan()
        finally:
            with self._lock:
                self._realtime_sessions.discard(session)
                self._session_started_at.pop(session, None)

    def _remove_empty_segments(self, session: Path) -> None:
        """Remove only zero-byte media files inside a completed ingest session."""

        if session == self.directory:
            return
        try:
            session.relative_to(self.directory)
        except ValueError:
            return
        if not session.is_dir():
            return
        for candidate in session.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in self.ALLOWED_SUFFIXES:
                continue
            try:
                if candidate.stat().st_size == 0:
                    candidate.unlink(missing_ok=True)
            except OSError:
                continue

    @property
    def duration_seconds(self) -> float:
        records = self.index.all()
        if not records:
            return 0.0
        return sum(record.duration_seconds for record in records)

    @property
    def bytes_used(self) -> int:
        return sum(record.size_bytes for record in self.index.all() if record.path.is_file())

    @property
    def has_active_writer(self) -> bool:
        with self._lock:
            return bool(self._writing_sessions)

    def register_file(
        self,
        path: str | Path,
        *,
        captured_at: datetime | None = None,
        duration_seconds: float | None = None,
        keyframe: bool = False,
    ) -> SegmentRecord | None:
        """將已完成分段加入索引；不存在或正在寫入的空檔案會略過。"""

        candidate = Path(path).resolve()
        try:
            candidate.relative_to(self.directory)
        except ValueError as exc:
            raise ValueError("環形快取分段必須位於設定的快取目錄內。") from exc
        if not self.enabled or not candidate.is_file() or candidate.suffix.lower() not in self.ALLOWED_SUFFIXES:
            return None
        size = candidate.stat().st_size
        if size <= 0:
            return None
        length = float(duration_seconds or self.segment_seconds)
        if captured_at is None:
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            captured_at = modified - timedelta(seconds=length)
        record = self.index.register(
            candidate,
            start_at=captured_at,
            duration_seconds=length,
            size_bytes=size,
            keyframe=keyframe,
        )
        self.rotate(now=captured_at + timedelta(seconds=length))
        return record

    def scan(self) -> tuple[SegmentRecord, ...]:
        """掃描快取目錄（含 FFmpeg session 子目錄）並加入新分段。"""

        if not self.enabled or not self.directory.exists():
            return ()
        records: list[SegmentRecord] = []
        pending: dict[Path, list[Path]] = defaultdict(list)
        with self._lock:
            writing_sessions = set(self._writing_sessions)
            realtime_sessions = set(self._realtime_sessions)
            session_started_at = dict(self._session_started_at)
        for candidate in self.directory.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in self.ALLOWED_SUFFIXES:
                continue
            candidate = candidate.resolve()
            if candidate in self._active_paths:
                continue
            existing = self.index.get(candidate)
            if existing is not None:
                records.append(existing)
            else:
                # Files are grouped by FFmpeg session.  The file mtime is not
                # a media timestamp: local test files and finite YouTube
                # inputs can be consumed much faster than real time.  A
                # monotonic session timeline keeps recent-clip queries from
                # treating every segment as overlapping the current instant.
                pending[candidate.parent].append(candidate)
        now = datetime.now(timezone.utc)
        for session, candidates in pending.items():
            durations = self._segment_durations(session)
            candidates.sort(key=lambda item: item.name)
            if session in writing_sessions:
                # FFmpeg may have written bytes to its newest file without
                # completing it. A newer numbered file proves the prior one
                # has been closed; the last one remains unavailable to clips.
                candidates = candidates[:-1]
            existing_session = [item for item in self.index.all() if item.path.parent == session]
            if existing_session:
                next_start = max(item.end_at for item in existing_session)
            elif session in session_started_at and session not in writing_sessions:
                next_start = session_started_at[session]
            else:
                next_start = now - timedelta(seconds=len(candidates) * self.segment_seconds)
            for index, candidate in enumerate(candidates):
                if durations is not None and candidate.name not in durations:
                    # A partial CSV write is not proof that this segment closed.
                    continue
                captured_at = next_start
                duration_seconds = durations[candidate.name] if durations is not None else self.segment_seconds
                if session in realtime_sessions:
                    modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
                    captured_at = modified - timedelta(seconds=duration_seconds)
                elif durations is None and session in session_started_at and session not in writing_sessions and index == len(candidates) - 1:
                    # Stream-copy segmentation can leave one TS file open for
                    # an entire long GOP.  When the writer exits, span the
                    # final record to the session end so events observed while
                    # that file was active can acquire it.  This wall-clock
                    # duration is index metadata only; ClipAssembler/ffprobe
                    # still reports the real media duration.
                    duration_seconds = max(self.segment_seconds, (now - captured_at).total_seconds())
                registered = self.register_file(
                    candidate,
                    captured_at=captured_at,
                    duration_seconds=duration_seconds,
                )
                if registered is not None:
                    records.append(registered)
                    next_start = registered.end_at
        self.index.prune_missing()
        self.rotate()
        return tuple(records)

    @staticmethod
    def _segment_durations(session: Path) -> dict[str, float] | None:
        """Read bounded FFmpeg CSV metadata, never URLs or arbitrary file paths.

        Missing lists retain compatibility with old caches. Present but incomplete
        lists defer indexing instead of substituting the requested GOP duration.
        """
        manifest = session / "segments.csv"
        try:
            with manifest.open("r", encoding="utf-8", newline="") as handle:
                raw = handle.read(1024 * 1024 + 1)
        except FileNotFoundError:
            return None
        except OSError:
            return {}
        if len(raw) > 1024 * 1024:
            return {}
        durations: dict[str, float] = {}
        for line in raw.splitlines(keepends=True):
            if not line.endswith("\n"):
                continue
            try:
                row = next(csv.reader([line], strict=True))
                if len(row) != 3:
                    continue
                name, start, end = row
                if not name.startswith("segment_") or not name.endswith(".ts"):
                    continue
                if not name[8:-3].isdigit():
                    continue
                start_value, end_value = float(start), float(end)
                duration = end_value - start_value
                if not all(math.isfinite(v) for v in (start_value, end_value, duration)) or duration <= 0:
                    continue
                durations[name] = duration
            except (ValueError, csv.Error, StopIteration):
                continue
        return durations

    def mark_active(self, path: str | Path) -> None:
        with self._lock:
            self._active_paths.add(Path(path).resolve())

    def mark_complete(self, path: str | Path) -> SegmentRecord | None:
        candidate = Path(path).resolve()
        with self._lock:
            self._active_paths.discard(candidate)
        siblings = [item for item in self.index.all() if item.path.parent == candidate.parent]
        captured_at = max((item.end_at for item in siblings), default=datetime.now(timezone.utc))
        return self.register_file(candidate, captured_at=captured_at, duration_seconds=self.segment_seconds)

    def snapshot(
        self,
        pre_seconds: float,
        *,
        post_seconds: float = 0.0,
        now: datetime | None = None,
    ) -> tuple[SegmentRecord, ...]:
        if pre_seconds < 0 or post_seconds < 0:
            raise ValueError("事件前錄與後錄秒數不可小於零。")
        if pre_seconds > self.retention_seconds:
            raise ValueError("事件前錄秒數不可大於環形快取保留時間。")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        start = current - timedelta(seconds=pre_seconds)
        end = current + timedelta(seconds=post_seconds)
        return self.index.between(start, end)

    def acquire_window(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[SegmentRecord, ...]:
        return self.index.acquire(self.index.between(start_at, end_at))

    def release(self, records: Iterable[SegmentRecord]) -> None:
        self.index.release(records)

    def rotate(self, *, now: datetime | None = None) -> tuple[Path, ...]:
        """刪除逾時或超量且未被事件引用的分段。"""

        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        removed: list[Path] = []
        records = list(self.index.all())
        cutoff = current - timedelta(seconds=self.retention_seconds)
        candidates = [
            record
            for record in records
            if record.reference_count == 0
            and record.path not in self._active_paths
            and record.end_at < cutoff
        ]
        for record in sorted(candidates, key=lambda item: item.end_at):
            if self._delete_record(record):
                removed.append(record.path)

        records = list(self.index.all())
        total = sum(record.size_bytes for record in records if record.path.is_file())
        for record in sorted(records, key=lambda item: item.end_at):
            if total <= self.maximum_size_bytes:
                break
            if record.reference_count > 0 or record.path in self._active_paths:
                continue
            if self._delete_record(record):
                total -= record.size_bytes
                removed.append(record.path)

        try:
            disk = inspect_disk_space(self.directory, minimum_free_bytes=self.minimum_free_bytes)
        except OSError as exc:
            self._warning = f"無法檢查快取磁碟空間：{type(exc).__name__}"
        else:
            self._warning = "磁碟剩餘空間低於門檻，暫停新增一般事件影片。" if disk.below_minimum else None
        return tuple(removed)

    def _delete_record(self, record: SegmentRecord) -> bool:
        try:
            record.path.unlink(missing_ok=True)
        except OSError:
            return False
        self.index.remove(record.path)
        return True

    def estimated_bitrate(self, *, now: datetime | None = None, window_seconds: float = 30) -> dict[str, float] | None:
        """Estimate compressed cache rate, not whole-host network throughput.

        Completed recent segments only; container overhead and any ingest
        transcoding are included, so this is not an exact camera wire bitrate.
        """
        now = now or datetime.now(timezone.utc)
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("傳輸率估算時間必須為有限正數。")
        cutoff = now - timedelta(seconds=window_seconds)
        records = [item for item in self.index.all()
                   if item.start_at >= cutoff and item.end_at <= now and item.size_bytes > 0]
        if not records or (now - max(item.end_at for item in records)).total_seconds() > max(5, self.segment_seconds * 3):
            return None
        seconds = sum(item.duration_seconds for item in records)
        if seconds < 2:
            return None
        return {"bps": sum(item.size_bytes for item in records) * 8 / seconds,
                "sample_seconds": seconds}

    def status(self) -> RingBufferStatus:
        try:
            disk = inspect_disk_space(self.directory, minimum_free_bytes=self.minimum_free_bytes)
            below = disk.below_minimum
        except OSError:
            below = False
        records = self.index.all()
        return RingBufferStatus(
            segment_count=len(records),
            duration_seconds=sum(record.duration_seconds for record in records),
            size_bytes=self.bytes_used,
            retention_seconds=self.retention_seconds,
            maximum_size_bytes=self.maximum_size_bytes,
            disk_below_minimum=below,
            warning=self._warning,
        )
