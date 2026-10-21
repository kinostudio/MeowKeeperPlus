"""不依賴 GUI 的 24H 本地資源保護契約。

這個模組只處理可驗證的本地作業：日誌輪替、磁碟空間檢查與明確目錄的
事件檔案保留。它不會刪除 SQLite、設定、模型或任意資料夾，也不會偷偷
安裝或修改 Python、驅動與 CUDA。
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

GIB = 1024**3


@dataclass(frozen=True)
class RetentionPolicy:
    """事件檔案與磁碟保護的可配置設定。"""

    event_video_days: int = 7
    log_days: int = 30
    max_event_video_bytes: int = 20 * GIB
    minimum_free_bytes: int = 10 * GIB
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    def __post_init__(self) -> None:
        if self.event_video_days < 0 or self.log_days < 0:
            raise ValueError("檔案保留天數不可小於零。")
        if self.max_event_video_bytes <= 0 or self.minimum_free_bytes < 0:
            raise ValueError("檔案容量與最低剩餘空間設定不正確。")
        if self.log_max_bytes <= 0 or self.log_backup_count < 1:
            raise ValueError("日誌輪替設定不正確。")


@dataclass(frozen=True)
class DiskSpaceSnapshot:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    minimum_free_bytes: int

    @property
    def below_minimum(self) -> bool:
        return self.free_bytes < self.minimum_free_bytes


def inspect_disk_space(path: str | Path, *, minimum_free_bytes: int = 10 * GIB) -> DiskSpaceSnapshot:
    """只讀取指定路徑所在磁碟的空間，不會建立或刪除檔案。"""

    if minimum_free_bytes < 0:
        raise ValueError("最低剩餘空間不可小於零。")
    usage = shutil.disk_usage(str(path))
    return DiskSpaceSnapshot(
        path=str(path),
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        free_bytes=int(usage.free),
        minimum_free_bytes=int(minimum_free_bytes),
    )


@dataclass(frozen=True)
class RetentionResult:
    removed_files: tuple[str, ...]
    removed_bytes: int
    skipped_pinned: tuple[str, ...]


def _iter_files(directory: Path, suffixes: Iterable[str]) -> list[Path]:
    allowed = {suffix.lower() for suffix in suffixes}
    if not directory.is_dir():
        return []
    return [
        item
        for item in directory.iterdir()
        if item.is_file() and item.suffix.lower() in allowed
    ]


def cleanup_retained_files(
    directory: str | Path,
    *,
    max_age_days: int,
    suffixes: Iterable[str] = (".mp4", ".mkv", ".ts", ".jpg", ".jpeg"),
    max_bytes: int | None = None,
    pinned_paths: Iterable[str | Path] = (),
    now: datetime | None = None,
) -> RetentionResult:
    """清理明確事件目錄中的過期／超量檔案。

    只處理指定目錄的第一層檔案；`pinned_paths` 永遠不會被刪除。這個函式
    不會觸碰資料庫、設定檔、模型或目錄本身。
    """

    if max_age_days < 0:
        raise ValueError("檔案保留天數不可小於零。")
    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("檔案容量上限必須大於零。")
    root = Path(directory).resolve()
    pinned = {Path(item).resolve() for item in pinned_paths}
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max_age_days)
    candidates = _iter_files(root, suffixes)
    removed: list[str] = []
    skipped: list[str] = []
    removed_bytes = 0

    for item in sorted(candidates, key=lambda path: path.stat().st_mtime):
        if item in pinned:
            skipped.append(str(item))
            continue
        modified = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            size = item.stat().st_size
            item.unlink()
            removed.append(str(item))
            removed_bytes += size

    if max_bytes is not None:
        remaining = [item for item in _iter_files(root, suffixes) if item not in pinned]
        current_bytes = sum(item.stat().st_size for item in remaining)
        for item in sorted(remaining, key=lambda path: path.stat().st_mtime):
            if current_bytes <= max_bytes:
                break
            size = item.stat().st_size
            item.unlink()
            current_bytes -= size
            removed.append(str(item))
            removed_bytes += size

    return RetentionResult(tuple(removed), removed_bytes, tuple(skipped))


def configure_rotating_logging(
    log_path: str | Path,
    *,
    logger_name: str = "meowkeeper",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """建立繁體中文服務可用的 RotatingFileHandler。

    呼叫端可再附加 console handler；此函式只設定指定 logger，避免重複加入
    handler 造成 24H 模式每次重連都寫入多份日誌。
    """

    if max_bytes <= 0 or backup_count < 1:
        raise ValueError("日誌輪替設定不正確。")
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved = path.resolve()
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == resolved:
            return logger
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
