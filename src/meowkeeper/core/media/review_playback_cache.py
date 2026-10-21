"""Bounded local cache for browser-compatible event review videos."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4


class ReviewPlaybackCache:
    """Cache derived playback copies without modifying the original event media.

    Cache files are kept under the local data directory, use opaque names, and
    are capped by both age and total size. They are disposable and never contain
    credentials or remote upload data.
    """

    MAX_BYTES = 512 * 1024 * 1024
    MAX_AGE_SECONDS = 14 * 24 * 60 * 60
    _CACHE_NAME = re.compile(r"^[0-9a-f]{64}\.mp4$")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _target(self, source: str | Path, event_id: str) -> Path:
        path = Path(source).resolve()
        stat = path.stat()
        identity = f"{event_id}\0{path}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
        return self.root / f"{hashlib.sha256(identity).hexdigest()}.mp4"

    def get(self, source: str | Path, event_id: str) -> Path | None:
        try:
            target = self._target(source, event_id)
            stat = target.stat()
            if not target.is_file() or stat.st_size <= 0 or time.time() - stat.st_mtime > self.MAX_AGE_SECONDS:
                return None
            os.utime(target, None)  # LRU age; cache hits remain warm for the TTL.
            return target
        except (OSError, ValueError):
            return None

    def store(self, source: str | Path, event_id: str, converted: str | Path) -> Path | None:
        converted_path = Path(converted).resolve()
        try:
            size = converted_path.stat().st_size
            if not converted_path.is_file() or size <= 0 or size > self.MAX_BYTES:
                return None
            target = self._target(source, event_id)
            self.root.mkdir(parents=True, exist_ok=True)
            now = time.time()
            if target.exists():
                try:
                    stat = target.stat()
                    if target.is_file() and stat.st_size > 0:
                        os.utime(target, None)
                        return target
                    target.unlink()
                except OSError:
                    return None
            entries: list[tuple[float, Path, int]] = []
            total = 0
            for candidate in self.root.iterdir():
                if not candidate.is_file() or not self._CACHE_NAME.fullmatch(candidate.name):
                    continue
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                if now - stat.st_mtime > self.MAX_AGE_SECONDS:
                    try:
                        candidate.unlink()
                    except OSError:
                        entries.append((stat.st_mtime, candidate, stat.st_size))
                        total += stat.st_size
                    continue
                entries.append((stat.st_mtime, candidate, stat.st_size))
                total += stat.st_size

            for _modified, candidate, existing_size in sorted(entries, key=lambda item: item[0]):
                if total + size <= self.MAX_BYTES:
                    break
                try:
                    candidate.unlink()
                    total -= existing_size
                except OSError:
                    continue
            if total + size > self.MAX_BYTES:
                return None

            temporary = self.root / f".{target.stem}.{uuid4().hex}.tmp"
            try:
                shutil.copyfile(converted_path, temporary)
                os.replace(temporary, target)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return target
        except (OSError, ValueError):
            return None
