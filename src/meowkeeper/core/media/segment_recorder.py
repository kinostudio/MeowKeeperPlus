"""FFmpeg 壓縮分段錄製器與有限 watchdog。

這個元件只負責產生壓縮分段，不讀取或保存 OpenCV BGR 影格。RTSP 來源若沒有
明確的本地 ingest 中繼網址會被拒絕，避免在攝影機端偷偷建立第二條連線。
"""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from meowkeeper.runtime import bundled_binary

from .ring_buffer import DiskSegmentRingBuffer
from .sources import BackoffPolicy, MediaSourceError, YouTubeStreamResolver, resolve_source


class SegmentRecorder:
    """以 FFmpeg segment muxer 寫入 MPEG-TS 分段。"""

    def __init__(
        self,
        source_url: str,
        ring_buffer: DiskSegmentRingBuffer,
        *,
        ffmpeg_path: str | None = None,
        resolver: YouTubeStreamResolver | None = None,
        segment_seconds: float = 1.0,
        prefer_stream_copy: bool = True,
        local_ingest_url: str | None = None,
        auto_restart: bool = True,
        retry_policy: BackoffPolicy | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if segment_seconds <= 0:
            raise ValueError("影音分段長度必須大於零。")
        if poll_interval_seconds <= 0:
            raise ValueError("FFmpeg watchdog 輪詢間隔必須大於零。")
        self.source_url = source_url
        self.ring_buffer = ring_buffer
        self.ffmpeg_path = ffmpeg_path or bundled_binary("ffmpeg")
        self.resolver = resolver
        self.segment_seconds = float(segment_seconds)
        self.prefer_stream_copy = bool(prefer_stream_copy)
        self.local_ingest_url = local_ingest_url
        self.auto_restart = auto_restart
        self.retry_policy = retry_policy or BackoffPolicy(max_retries=3, initial_seconds=0.5, maximum_seconds=8.0)
        self._popen = popen
        self._sleep = sleep
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._resolved_url: str | None = None
        self._session_dir: Path | None = None
        self._restart_count = 0
        self._last_error: str | None = None
        self._fallback_attempted = False
        self._is_network_source = False
        self._run_lock = threading.RLock()

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        return self._process

    def build_command(self, stream_url: str, *, stream_copy: bool | None = None) -> list[str]:
        if not self.ffmpeg_path:
            raise MediaSourceError("找不到 FFmpeg，無法啟動壓縮影音分段。")
        copy_mode = self.prefer_stream_copy if stream_copy is None else bool(stream_copy)
        session_dir = self._session_dir or self.ring_buffer.directory
        output_pattern = session_dir / "segment_%08d.ts"
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
        ]
        if urlsplit(stream_url).scheme.lower() in {"rtsp", "rtsps"}:
            command.extend(["-rtsp_transport", "tcp"])
        command.extend(["-i", stream_url, "-map", "0:v:0?"])
        command.extend(["-map", "0:a:0?"])
        if copy_mode:
            command.extend(["-c", "copy"])
        else:
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-tune",
                    "zerolatency",
                    "-crf",
                    "30",
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
                str(self.segment_seconds),
                "-segment_list",
                str(session_dir / "segments.csv"),
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

    def start(self) -> None:
        if self._watcher is not None and self._watcher.is_alive():
            return
        if not self.ffmpeg_path:
            raise MediaSourceError("找不到 FFmpeg，無法啟動壓縮影音分段。")
        resolved = resolve_source(
            self.local_ingest_url or self.source_url,
            resolver=self.resolver,
        )
        stream_url = resolved.stream_url
        scheme = urlsplit(stream_url).scheme.lower()
        if scheme in {"rtsp", "rtsps"} and not self.local_ingest_url:
            raise MediaSourceError(
                "RTSP 分段錄影需要同一個本地 ingest 中繼，拒絕建立第二條攝影機連線。"
            )
        self._resolved_url = stream_url
        self._is_network_source = scheme in {"rtsp", "rtsps", "http", "https"}
        self._session_dir = self.ring_buffer.directory / f"session_{uuid.uuid4().hex}"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._fallback_attempted = False
        self._start_process(stream_copy=self.prefer_stream_copy)
        self._watcher = threading.Thread(
            target=self._watch,
            name="meowkeeper-segment-watchdog",
            daemon=True,
        )
        self._watcher.start()

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        self._terminate_process()
        if wait and self._watcher is not None and self._watcher is not threading.current_thread():
            self._watcher.join(timeout=5.0)
        self.ring_buffer.scan()
        self._watcher = None

    close = stop

    def _start_process(self, *, stream_copy: bool) -> None:
        if self._resolved_url is None:
            raise MediaSourceError("尚未解析媒體來源。")
        command = self.build_command(self._resolved_url, stream_copy=stream_copy)
        try:
            process = self._popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                shell=False,
                bufsize=0,
            )
        except OSError as exc:
            self._last_error = f"FFmpeg 啟動失敗：{type(exc).__name__}"
            raise MediaSourceError(self._last_error) from exc
        self._process = process

    def _watch(self) -> None:
        retry_number = 0
        while not self._stop.is_set():
            self.ring_buffer.scan()
            process = self._process
            if process is None or process.poll() is None:
                self._stop.wait(self.poll_interval_seconds)
                continue
            return_code = process.returncode
            self._process = None
            self.ring_buffer.scan()
            if self._stop.is_set():
                break
            if return_code == 0 and not self._is_network_source:
                self._last_error = "本地影音來源已正常結束。"
                break
            if return_code not in (0, None) and self.prefer_stream_copy and not self._fallback_attempted:
                self._fallback_attempted = True
                self._last_error = "FFmpeg stream copy 失敗，改用低負載轉碼。"
                try:
                    self._start_process(stream_copy=False)
                except MediaSourceError:
                    pass
                continue
            if not self.auto_restart:
                self._last_error = "FFmpeg 分段程序已結束。"
                break
            if retry_number >= self.retry_policy.max_retries:
                self._last_error = "FFmpeg 分段程序重試次數已達上限。"
                break
            retry_number += 1
            self._restart_count += 1
            self._last_error = "FFmpeg 分段程序中斷，正在以退避策略重啟。"
            self._sleep(self.retry_policy.delay_seconds(retry_number))
            if self._stop.is_set():
                break
            try:
                self._start_process(stream_copy=not self._fallback_attempted)
            except MediaSourceError:
                continue

    def _terminate_process(self) -> None:
        with self._run_lock:
            process = self._process
            self._process = None
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
        except (OSError, subprocess.TimeoutExpired):
            self._last_error = "FFmpeg 關閉時發生例外，已停止後續重試。"
