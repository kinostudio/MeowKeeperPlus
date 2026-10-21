"""FFprobe 驗證事件影片是否可讀、含影像及具有合理時長。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meowkeeper.runtime import bundled_binary


@dataclass(frozen=True)
class MediaValidationResult:
    path: Path
    readable: bool
    has_video: bool
    has_audio: bool
    duration_seconds: float | None
    error: str | None = None
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    container_format: str | None = None
    mp4_faststart: bool = False

    @property
    def valid(self) -> bool:
        return self.readable and self.has_video and self.duration_seconds is not None and self.duration_seconds > 0

    @property
    def telegram_compatible(self) -> bool:
        return (
            self.valid
            and self.video_codec == "h264"
            and self.pixel_format == "yuv420p"
            and self.width is not None and self.width > 0
            and self.height is not None and self.height > 0
            and self.container_format is not None
            and any(name in self.container_format.split(",") for name in ("mov", "mp4"))
            and self.mp4_faststart
        )


def validate_media(
    path: str | Path,
    *,
    ffprobe_path: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> MediaValidationResult:
    """以本地 ffprobe 驗證，不將任何來源 URL 或秘密寫入錯誤內容。"""

    candidate = Path(path).resolve()
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return MediaValidationResult(candidate, False, False, False, None, "影片檔案不存在或為空。")
    probe = ffprobe_path or bundled_binary("ffprobe")
    if not probe:
        return MediaValidationResult(candidate, False, False, False, None, "找不到 FFprobe。")
    command = [
        probe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,codec_name,width,height,pix_fmt",
        "-of",
        "json",
        str(candidate),
    ]
    try:
        completed = run(
            command,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=15.0,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MediaValidationResult(candidate, False, False, False, None, f"FFprobe 無法完成：{type(exc).__name__}")
    if completed.returncode != 0:
        return MediaValidationResult(candidate, False, False, False, None, "FFprobe 無法讀取影片。")
    try:
        payload: dict[str, Any] = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return MediaValidationResult(candidate, False, False, False, None, "FFprobe 回傳格式不正確。")
    streams = payload.get("streams") or []
    typed_streams = [stream for stream in streams if isinstance(stream, dict)]
    types = {str(stream.get("codec_type")) for stream in typed_streams}
    video = next((stream for stream in typed_streams if stream.get("codec_type") == "video"), {})
    format_data = payload.get("format") or {}
    try:
        duration = float(format_data.get("duration"))
    except (TypeError, ValueError):
        duration = None
    has_video = "video" in types
    has_audio = "audio" in types
    container_format = str(format_data.get("format_name") or "") or None
    video_codec = str(video.get("codec_name") or "") or None
    pixel_format = str(video.get("pix_fmt") or "") or None
    try:
        width = int(video.get("width"))
        height = int(video.get("height"))
    except (TypeError, ValueError):
        width = height = None
    mp4_faststart = (
        _has_mp4_faststart(candidate)
        if candidate.suffix.lower() in {".mp4", ".m4v", ".mov"}
        and container_format
        and any(name in container_format.split(",") for name in ("mov", "mp4"))
        else False
    )
    if not has_video:
        return MediaValidationResult(candidate, True, False, has_audio, duration, "影片沒有影像串流。", container_format=container_format)
    if duration is None or duration <= 0:
        return MediaValidationResult(candidate, True, True, has_audio, duration, "影片時長無法判定。", video_codec, width, height, pixel_format, container_format, mp4_faststart)
    return MediaValidationResult(candidate, True, True, has_audio, duration, None, video_codec, width, height, pixel_format, container_format, mp4_faststart)


def _has_mp4_faststart(path: Path) -> bool:
    """檢查 MP4 top-level atom 順序，不讀取大型 media payload。"""

    try:
        file_size = path.stat().st_size
        with path.open("rb") as handle:
            offset = 0
            moov_offset: int | None = None
            mdat_offset: int | None = None
            atom_count = 0
            while offset + 8 <= file_size and atom_count < 4096:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    return False
                atom_size = int.from_bytes(header[:4], "big")
                atom_type = header[4:8]
                header_size = 8
                if atom_size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        return False
                    atom_size = int.from_bytes(extended, "big")
                    header_size = 16
                elif atom_size == 0:
                    atom_size = file_size - offset
                if atom_size < header_size or offset + atom_size > file_size:
                    return False
                if atom_type == b"moov" and moov_offset is None:
                    moov_offset = offset
                elif atom_type == b"mdat" and mdat_offset is None:
                    mdat_offset = offset
                if moov_offset is not None and mdat_offset is not None:
                    return moov_offset < mdat_offset
                offset += atom_size
                atom_count += 1
    except OSError:
        return False
    return False


def assert_valid_media(path: str | Path, *, ffprobe_path: str | None = None) -> MediaValidationResult:
    """供組片器使用的明確驗證函式。"""

    result = validate_media(path, ffprobe_path=ffprobe_path)
    if not result.valid:
        raise ValueError(result.error or "事件影片驗證失敗。")
    return result
