"""單一 FFmpeg ingest：同一個來源同時產生 AI 影格與壓縮分段。"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from meowkeeper.runtime import bundled_binary

from .ring_buffer import DiskSegmentRingBuffer
from .sources import (
    BackoffPolicy,
    FramePacket,
    MediaSourceError,
    ResolvedStream,
    YouTubeStreamResolver,
    is_youtube_url,
    iter_jpeg_frames,
    resolve_source,
)

_MPEGTS_STREAM_COPY_VIDEO_CODECS = {"h264", "avc1", "hevc", "h265", "hvc1", "hev1"}


class FfmpegMediaIngest:
    """避免 AI、錄影及 `/live` 各自再次拉取攝影機來源。

    FFmpeg 的第一個輸出是低 FPS JPEG pipe，第二個輸出是 H.264/H.265（及可選
    音訊）MPEG-TS 分段；兩者共享同一個輸入程序與同一條來源連線。
    """

    def __init__(
        self,
        source_url: str,
        ring_buffer: DiskSegmentRingBuffer,
        *,
        resolver: YouTubeStreamResolver | None = None,
        ffmpeg_path: str | None = None,
        fps: int = 5,
        width: int = 640,
        quality: int = 5,
        prefer_stream_copy: bool = True,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        retry_policy: BackoffPolicy | None = None,
    ) -> None:
        if fps <= 0 or fps > 5:
            raise ValueError("MX250 基線 AI 影格採樣不可超過 5 FPS。")
        if width <= 0 or width > 1920:
            raise ValueError("共享影格寬度必須介於 1 到 1920；推論尺寸由 AI 設定控制。")
        if not 1 <= quality <= 31:
            raise ValueError("JPEG 品質必須介於 1 到 31。")
        self.source_url = source_url
        self.ring_buffer = ring_buffer
        self.resolver = resolver
        self.ffmpeg_path = ffmpeg_path or bundled_binary("ffmpeg")
        self.fps = fps
        self.width = width
        self.quality = quality
        self.prefer_stream_copy = prefer_stream_copy
        self.stream_copy_active: bool | None = None
        self._popen = popen
        self._sleep = sleep
        self._closed = threading.Event()
        # The unattended Host keeps network sources alive. An explicitly
        # supplied policy retains bounded retries for controlled diagnostics.
        self._continuous_network_retry = retry_policy is None
        if retry_policy is not None:
            self.retry_policy = retry_policy
        elif is_youtube_url(source_url):
            # Signed YouTube manifests need a fresh yt-dlp resolution after
            # EOF/403; keep the adapter's bounded 3/6/12/24/60s schedule.
            self.retry_policy = BackoffPolicy(
                max_retries=5,
                initial_seconds=3.0,
                maximum_seconds=60.0,
            )
        else:
            self.retry_policy = BackoffPolicy()
        self.restart_count = 0
        self.last_error: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._upstream_process: subprocess.Popen[bytes] | None = None
        self._session_dir: Path | None = None
        self._fallback_attempted = False

    def build_command(
        self,
        stream_url: str,
        *,
        stream_copy: bool | None = None,
        input_from_pipe: bool = False,
    ) -> list[str]:
        if not self.ffmpeg_path:
            raise MediaSourceError("找不到 FFmpeg，無法啟動單一媒體 ingest。")
        session = self._session_dir or self.ring_buffer.directory
        output_pattern = session / "segment_%08d.ts"
        command = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if not input_from_pipe and urlsplit(stream_url).scheme.lower() in {"rtsp", "rtsps"}:
            command.extend(["-rtsp_transport", "tcp"])
        command.extend(
            [
                "-i",
                "pipe:0" if input_from_pipe else stream_url,
                "-map",
                "0:v:0?",
                "-an",
                "-vf",
                f"fps={self.fps},scale=w='min({self.width},iw)':h=-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                str(self.quality),
                "pipe:1",
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
            ]
        )
        use_stream_copy = self.prefer_stream_copy if stream_copy is None else stream_copy
        if use_stream_copy:
            command.extend(["-c", "copy"])
        else:
            keyframe_interval = max(1, round(self.fps * self.ring_buffer.segment_seconds))
            command.extend(
                [
                    "-vf",
                    f"fps={self.fps},scale=w='min({min(self.width, 640)},iw)':h=-2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-tune",
                    "zerolatency",
                    "-crf",
                    "30",
                    "-g",
                    str(keyframe_interval),
                    "-keyint_min",
                    str(keyframe_interval),
                    "-sc_threshold",
                    "0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                ]
            )
        command.extend(
            [
                "-f",
                "segment",
                "-segment_time",
                str(self.ring_buffer.segment_seconds),
                "-segment_list",
                str(session / "segments.csv"),
                "-segment_list_type",
                "csv",
                "-segment_list_size",
                "4096",
                "-reset_timestamps",
                "1",
                "-segment_format",
                "mpegts",
                str(output_pattern),
            ]
        )
        return command

    def iter_frames(
        self,
        max_frames: int | None = None,
        *,
        resolved: ResolvedStream | None = None,
    ) -> Iterator[FramePacket]:
        if max_frames is not None and max_frames < 1:
            return
        if self._closed.is_set():
            return
        if not self.ffmpeg_path:
            raise MediaSourceError("找不到 FFmpeg，無法啟動單一媒體 ingest。")
        resolved = resolved or resolve_source(self.source_url, resolver=self.resolver)
        self._session_dir = self.ring_buffer.directory / f"ingest_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._fallback_attempted = False
        count = 0
        retries = 0
        stream_copy = self.prefer_stream_copy
        is_network = urlsplit(resolved.stream_url).scheme.lower() in {"rtsp", "rtsps", "http", "https"}
        is_youtube = is_youtube_url(self.source_url)
        if stream_copy and is_youtube:
            media_info = getattr(self.resolver, "media_info", None)
            video_codec = str(getattr(media_info, "video_codec", "") or "").casefold()
            codec_family = video_codec.split(".", 1)[0]
            if codec_family and codec_family not in _MPEGTS_STREAM_COPY_VIDEO_CODECS:
                # MPEG-TS cannot carry every codec yt-dlp may choose (for example VP9).
                # Transcode within the MX250 budget instead of accepting data-only TS output.
                stream_copy = False
                self.last_error = (
                    "來源影片編碼不適用 MPEG-TS；事件快取改用 640 像素、最高 5 FPS 的低負載 H.264 轉碼。"
                )
        resolve_again = False
        while True:
            if self._closed.is_set():
                return
            if resolve_again and is_youtube_url(self.source_url):
                # YouTube signed URLs are short-lived.  A failed FFmpeg
                # producer must obtain a fresh URL through yt-dlp instead of
                # retrying the expired media URL.
                resolved = resolve_source(self.source_url, resolver=self.resolver)
                resolve_again = False
            if self._closed.is_set():
                return
            upstream = None
            input_from_pipe = is_youtube and hasattr(self.resolver, "start_download_process")
            if input_from_pipe:
                upstream = self.resolver.start_download_process()
                self._upstream_process = upstream
                if upstream.stdout is None:
                    self._close_upstream_process()
                    raise MediaSourceError("yt-dlp 沒有提供媒體串流輸出。")
            process = self._popen(
                self.build_command(
                    resolved.stream_url,
                    stream_copy=stream_copy,
                    input_from_pipe=input_from_pipe,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=upstream.stdout if upstream is not None else subprocess.DEVNULL,
                shell=False,
                bufsize=0,
            )
            if upstream is not None and upstream.stdout is not None:
                upstream.stdout.close()
            self._process = process
            self.stream_copy_active = stream_copy
            self.ring_buffer.begin_session(self._session_dir, realtime=is_network)
            if process.stdout is None:
                self._close_process()
                self.ring_buffer.end_session(self._session_dir)
                raise MediaSourceError("FFmpeg 沒有提供 JPEG 影格輸出。")
            frames_before_attempt = count
            try:
                for jpeg in iter_jpeg_frames(process.stdout):
                    count += 1
                    self.ring_buffer.scan()
                    yield FramePacket(count, datetime.now(timezone.utc), self.source_url, jpeg)
                    if max_frames is not None and count >= max_frames:
                        return
            except (OSError, ValueError):
                # close() deliberately closes the pipe to unblock a waiting
                # reader. That is a normal shutdown, not a source failure.
                if not self._closed.is_set():
                    raise
                return
            finally:
                return_code = self._close_process()
                self.ring_buffer.end_session(self._session_dir)
            if self._closed.is_set():
                return
            info = getattr(self.resolver, "media_info", None) if is_youtube else None
            vod_complete = (
                is_youtube
                and info is not None
                and not info.is_live
                and count > frames_before_attempt
                and return_code in (0, None)
            )
            # A clean FFmpeg EOF is normal for a file/VOD, but not for RTSP
            # or a live YouTube source. Reconnect those without a second
            # simultaneous producer.
            if vod_complete or not is_network:
                if count == 0 and return_code not in (0, None):
                    self.last_error = "單一媒體 ingest 未產生影格。"
                    raise MediaSourceError(self.last_error)
                return
            if return_code not in (0, None) and stream_copy and not self._fallback_attempted:
                self._fallback_attempted = True
                stream_copy = False
                self.last_error = "FFmpeg stream copy 失敗，改用低負載轉碼。"
                if is_youtube:
                    resolve_again = True
                continue
            if not self._continuous_network_retry and retries >= self.retry_policy.max_retries:
                self.last_error = "單一媒體 ingest 重試次數已達上限。"
                raise MediaSourceError(self.last_error)
            retries += 1
            self.restart_count += 1
            self.last_error = "單一媒體 ingest 中斷，正在以退避策略重啟。"
            delay = self.retry_policy.delay_seconds(retries)
            if self._sleep is time.sleep:
                self._closed.wait(delay)
            else:
                self._sleep(delay)
            if self._closed.is_set():
                return
            resolve_again = is_youtube_url(self.source_url)
            if count == frames_before_attempt and return_code not in (0, None):
                continue

    def close(self) -> None:
        self._closed.set()
        self._close_process()

    def _close_upstream_process(self) -> None:
        process = self._upstream_process
        self._upstream_process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            stdout = getattr(process, "stdout", None)
            if stdout is not None:
                stdout.close()
        except (OSError, subprocess.TimeoutExpired):
            return

    def _close_process(self) -> int | None:
        process = self._process
        self._process = None
        if process is None:
            self._close_upstream_process()
            return None
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            stdout = getattr(process, "stdout", None)
            if stdout is not None:
                try:
                    stdout.close()
                except OSError:
                    pass
            return process.returncode
        except (OSError, subprocess.TimeoutExpired):
            return getattr(process, "returncode", None)
        finally:
            self._close_upstream_process()
