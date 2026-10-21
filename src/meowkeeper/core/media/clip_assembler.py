"""以 FFmpeg 組合壓縮分段為可播放 MP4。"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from meowkeeper.runtime import bundled_binary

from .media_validation import MediaValidationResult, validate_media
from .segment_index import SegmentRecord


@dataclass(frozen=True)
class ClipAssemblyResult:
    path: Path | None
    status: str
    actual_duration_seconds: float | None
    used_stream_copy: bool
    error: str | None = None
    size_bytes: int = 0
    sha256: str | None = None
    validation: MediaValidationResult | None = None


class ClipAssembler:
    """bounded、可驗證的 concat 組片器。"""

    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        timeout_seconds: float = 120.0,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("FFmpeg 組片逾時秒數必須大於零。")
        self.ffmpeg_path = ffmpeg_path or bundled_binary("ffmpeg")
        self.ffprobe_path = ffprobe_path or bundled_binary("ffprobe")
        self.timeout_seconds = timeout_seconds
        self._run = run

    def assemble(
        self,
        records: Iterable[SegmentRecord],
        output_path: str | Path,
        *,
        prefer_stream_copy: bool = True,
    ) -> ClipAssemblyResult:
        ordered = tuple(sorted(records, key=lambda item: item.start_at))
        destination = Path(output_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not ordered:
            return ClipAssemblyResult(None, "failed", None, False, "沒有可組片的影音分段。")
        if not self.ffmpeg_path:
            return ClipAssemblyResult(None, "failed", None, False, "找不到 FFmpeg，無法組合事件影片。")
        if any(not record.path.is_file() for record in ordered):
            return ClipAssemblyResult(None, "failed", None, False, "事件引用的影音分段已不存在。")

        with tempfile.TemporaryDirectory(prefix="meowkeeper-clip-") as temporary:
            list_path = Path(temporary) / "concat.txt"
            list_path.write_text(self._concat_manifest(ordered), encoding="utf-8")
            copy_result = self._run_mode(list_path, destination, stream_copy=True) if prefer_stream_copy else None
            if copy_result is not None and copy_result.valid:
                return self._success(destination, copy_result, used_stream_copy=True)
            if destination.exists():
                destination.unlink(missing_ok=True)
            transcoded = self._run_mode(list_path, destination, stream_copy=False)
            if transcoded.valid:
                return self._success(destination, transcoded, used_stream_copy=False)
            error = transcoded.error or (copy_result.error if copy_result is not None else None)
            return ClipAssemblyResult(None, "failed", transcoded.duration_seconds, False, error)

    def transcode_for_telegram(
        self,
        source_path: str | Path,
        output_path: str | Path,
        *, annotation_filter_file: Path | None = None,
    ) -> ClipAssemblyResult:
        """在保留原始事件影片下產生低負載 Telegram 相容版本。"""

        source = Path(source_path).resolve()
        destination = Path(output_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not self.ffmpeg_path or not source.is_file():
            return ClipAssemblyResult(None, "failed", None, False, "Telegram 壓縮素材不存在或找不到 FFmpeg。")
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            "scale=w='min(640,iw)':h=-2:force_original_aspect_ratio=decrease:out_range=tv,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-tag:v",
            "avc1",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-map_metadata",
            "-1",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(destination),
        ]
        if annotation_filter_file is not None:
            # Local numeric-only drawbox filters, before the existing scale.
            index = command.index("-vf")
            scaling = command[index + 1]
            filters = annotation_filter_file.read_text(encoding="ascii")
            annotation_filter_file.write_text(filters + "," + scaling, encoding="ascii")
            command[index:index + 2] = ["-filter_script:v", str(annotation_filter_file)]
        try:
            completed = self._run(
                command,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self.timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ClipAssemblyResult(None, "failed", None, False, f"Telegram 影片壓縮失敗：{type(exc).__name__}")
        if completed.returncode != 0:
            return ClipAssemblyResult(None, "failed", None, False, "Telegram 影片壓縮失敗。")
        validation = validate_media(destination, ffprobe_path=self.ffprobe_path)
        if not validation.valid:
            return ClipAssemblyResult(None, "failed", validation.duration_seconds, False, validation.error, validation=validation)
        if not validation.telegram_compatible:
            return ClipAssemblyResult(
                None,
                "failed",
                validation.duration_seconds,
                False,
                "Telegram 相容影片驗證未通過。",
                validation=validation,
            )
        return self._success(destination, validation, used_stream_copy=False)

    @staticmethod
    def _concat_manifest(records: tuple[SegmentRecord, ...]) -> str:
        lines: list[str] = []
        for record in records:
            # concat demuxer 的單引號跳脫語法，不把路徑交給 shell。
            path = record.path.as_posix().replace("'", "'\\''")
            lines.append(f"file '{path}'")
        return "\n".join(lines) + "\n"

    def _run_mode(
        self,
        list_path: Path,
        destination: Path,
        *,
        stream_copy: bool,
    ) -> MediaValidationResult:
        command = [
            self.ffmpeg_path or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
        ]
        if stream_copy:
            command.extend(["-c", "copy", "-movflags", "+faststart"])
        else:
            command.extend(
                [
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "28",
                    "-pix_fmt",
                    "yuv420p",
                    "-tag:v",
                    "avc1",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-map_metadata",
                    "-1",
                    "-avoid_negative_ts",
                    "make_zero",
                    "-movflags",
                    "+faststart",
                ]
            )
        command.append(str(destination))
        try:
            completed = self._run(
                command,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self.timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return MediaValidationResult(destination, False, False, False, None, f"FFmpeg 組片失敗：{type(exc).__name__}")
        if completed.returncode != 0:
            return MediaValidationResult(destination, False, False, False, None, "FFmpeg stream copy／轉碼組片失敗。")
        return validate_media(destination, ffprobe_path=self.ffprobe_path)

    @staticmethod
    def _success(
        destination: Path,
        validation: MediaValidationResult,
        *,
        used_stream_copy: bool,
    ) -> ClipAssemblyResult:
        digest = hashlib.sha256(destination.read_bytes()).hexdigest().upper()
        return ClipAssemblyResult(
            destination,
            "completed",
            validation.duration_seconds,
            used_stream_copy,
            size_bytes=destination.stat().st_size,
            sha256=digest,
            validation=validation,
        )
