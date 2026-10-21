"""單一媒體入口與 YouTube/RTSP 影格來源。

本模組只負責解析來源與從 FFmpeg 取得 JPEG 影格，不負責 AI 或通知。
YouTube 正式入口使用固定版 ``yt-dlp`` 與 ``yt-dlp-ejs``；本程式不會自動
下載或安裝外部工具。RTSP 來源只使用使用者提供的 URI，並以 TCP/554
作為 MeowKeeper+ 的安全預設，不啟用 ONVIF。
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from meowkeeper.runtime import bundled_binary, installed_script


class MediaSourceError(RuntimeError):
    """來源解析或 FFmpeg 影格擷取失敗。"""


@dataclass(frozen=True)
class BackoffPolicy:
    """網路媒體重連的有限指數退避設定。"""

    max_retries: int = 3
    initial_seconds: float = 0.5
    maximum_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("媒體重試次數不可小於零。")
        if self.initial_seconds < 0 or self.maximum_seconds < 0:
            raise ValueError("媒體退避秒數不可小於零。")
        if self.maximum_seconds < self.initial_seconds:
            raise ValueError("退避上限不可小於初始秒數。")

    def delay_seconds(self, retry_number: int) -> float:
        if retry_number < 1:
            raise ValueError("重試編號必須從 1 開始。")
        return min(self.maximum_seconds, self.initial_seconds * (2 ** (retry_number - 1)))


@dataclass(frozen=True)
class ResolvedStream:
    """解析後的串流資訊；實際簽名 URL 只留在記憶體，不寫入日誌。"""

    requested_url: str
    stream_url: str
    resolver: str

    @property
    def safe_host(self) -> str:
        parsed = urlsplit(self.stream_url)
        return parsed.hostname or "未知主機"


@dataclass(frozen=True)
class FramePacket:
    """FFmpeg 輸出的單張 JPEG 影格。"""

    sequence: int
    captured_at: datetime
    source_url: str
    jpeg_bytes: bytes
    audio_samples: bytes | None = None
    audio_sample_rate: int | None = None


def is_youtube_url(source_url: str) -> bool:
    """判斷是否為 YouTube 影片或直播頁面。"""

    try:
        host = (urlsplit(source_url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def _safe_command_error(stderr: str) -> str:
    """將外部解析器錯誤壓成不含完整 URL 的簡短訊息。"""

    cleaned = re.sub(r"https?://[^\s]+", "<來源網址>", stderr or "")
    cleaned = re.sub(r"rtsp://[^\s]+", "<RTSP來源>", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())
    return cleaned[-500:] if cleaned else "外部解析器沒有提供錯誤摘要。"


class YouTubeStreamResolver:
    """相容舊呼叫端的解析器；預設路徑已改用固定版 yt-dlp adapter。

    ``streamlink_path`` 僅為舊測試／相容整合保留的明確 opt-in；正式 YouTube
    入口不會從 PATH 自動選擇 streamlink。
    """

    def __init__(
        self,
        *,
        streamlink_path: str | None = None,
        yt_dlp_path: str | None = None,
        timeout_seconds: float = 20.0,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        retry_policy: BackoffPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.streamlink_path = streamlink_path
        self.yt_dlp_path = yt_dlp_path or installed_script("yt-dlp")
        self.timeout_seconds = timeout_seconds
        self._run = run
        self.retry_policy = retry_policy or BackoffPolicy(max_retries=2)
        self._sleep = sleep

    def resolve(self, source_url: str, quality: str = "best") -> ResolvedStream:
        if not is_youtube_url(source_url):
            raise MediaSourceError("來源不是可解析的 YouTube 網址。")
        if not quality or any(char.isspace() for char in quality):
            raise MediaSourceError("YouTube 畫質參數格式不正確。")

        if self.streamlink_path is None:
            from .youtube import YouTubeSourceAdapter, YouTubeSourceConfig

            max_height = int(quality) if quality.isdigit() and int(quality) in {360, 480, 720, 1080} else 720
            adapter = YouTubeSourceAdapter(
                YouTubeSourceConfig(source_url, max_height=max_height),
                yt_dlp_path=self.yt_dlp_path,
            )
            return adapter.resolve()

        candidates: list[tuple[str, str, list[str]]] = []
        if self.streamlink_path:
            candidates.append(
                (
                    "streamlink",
                    self.streamlink_path,
                    [self.streamlink_path, "--stream-url", source_url, quality],
                )
            )
        if self.yt_dlp_path:
            candidates.append(
                (
                    "yt-dlp",
                    self.yt_dlp_path,
                    [
                        self.yt_dlp_path,
                        "--no-warnings",
                        "--no-playlist",
                        "--get-url",
                        "-f",
                        quality,
                        source_url,
                    ],
                )
            )

        if not candidates:
            raise MediaSourceError(
                "找不到內含的 YouTube 解析 runtime；請修復或重新安裝 MeowKeeper+。"
            )

        errors: list[str] = []
        for resolver_name, _path, command in candidates:
            for attempt in range(self.retry_policy.max_retries + 1):
                try:
                    result = self._run(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                        shell=False,
                        timeout=self.timeout_seconds,
                        encoding="utf-8",
                        errors="replace",
                    )
                except FileNotFoundError:
                    result = None
                except subprocess.TimeoutExpired:
                    result = None
                    errors.append(f"{resolver_name} 解析逾時")
                if result is not None:
                    if result.returncode == 0:
                        urls = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                        stream_url = next(
                            (
                                line
                                for line in reversed(urls)
                                if line.startswith(("http://", "https://"))
                            ),
                            None,
                        )
                        if stream_url:
                            return ResolvedStream(source_url, stream_url, resolver_name)
                        errors.append(f"{resolver_name} 沒有回傳可用串流網址")
                    else:
                        errors.append(f"{resolver_name}：{_safe_command_error(result.stderr)}")
                if attempt < self.retry_policy.max_retries:
                    self._sleep(self.retry_policy.delay_seconds(attempt + 1))
            if result is None and not errors:
                errors.append(f"{resolver_name} 無法啟動")

        detail = "；".join(errors) if errors else "沒有可用的解析結果。"
        raise MediaSourceError(f"YouTube 串流網址解析失敗：{detail}")


def resolve_source(
    source_url: str,
    *,
    resolver: YouTubeStreamResolver | None = None,
    quality: str = "best",
) -> ResolvedStream:
    """解析 YouTube；RTSP 或其他直接來源只原樣傳遞，不做猜測。"""

    if not source_url or not source_url.strip():
        raise MediaSourceError("未提供媒體來源網址。")
    source_url = source_url.strip()
    parsed = urlsplit(source_url)
    if parsed.scheme.lower() in {"rtsp", "rtsps", "http", "https"} and not is_youtube_url(
        source_url
    ):
        return ResolvedStream(source_url, source_url, "direct")
    if is_youtube_url(source_url):
        return (resolver or YouTubeStreamResolver()).resolve(source_url, quality)
    if Path(source_url).is_file():
        return ResolvedStream(source_url, source_url, "local_file")
    raise MediaSourceError("來源必須是 RTSP、HTTP(S)、YouTube 網址或存在的影片檔。")


def iter_jpeg_frames(stream: BinaryIO, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    """從 FFmpeg image2pipe 輸出切出 JPEG 影格。"""

    if chunk_size <= 0:
        raise ValueError("chunk_size 必須大於零。")
    buffer = bytearray()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > 1:
                    del buffer[:-1]
                break
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start:
                    del buffer[:start]
                break
            end += 2
            yield bytes(buffer[start:end])
            del buffer[:end]


class FfmpegFrameSource:
    """以 FFmpeg 將串流轉成低頻 JPEG 影格，維持單一來源程序。"""

    def __init__(
        self,
        source_url: str,
        *,
        resolver: YouTubeStreamResolver | None = None,
        ffmpeg_path: str | None = None,
        fps: int = 5,
        width: int = 640,
        quality: int = 5,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        retry_policy: BackoffPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if fps <= 0 or fps > 5:
            raise ValueError("MX250 預設 AI 採樣必須介於 1 到 5 FPS。")
        if width <= 0 or width > 640:
            raise ValueError("MX250 預設影像寬度不可大於 640 像素。")
        if not 1 <= quality <= 31:
            raise ValueError("JPEG 品質參數必須介於 1 到 31。")
        self.source_url = source_url
        self.resolver = resolver
        self.ffmpeg_path = ffmpeg_path or bundled_binary("ffmpeg")
        self.fps = fps
        self.width = width
        self.quality = quality
        self._popen = popen
        self.retry_policy = retry_policy or BackoffPolicy()
        self._sleep = sleep
        self.restart_count = 0
        self.last_error: str | None = None

    def build_command(self, stream_url: str, *, max_frames: int | None = None) -> list[str]:
        command = [self.ffmpeg_path or "ffmpeg", "-hide_banner", "-loglevel", "error"]
        if urlsplit(stream_url).scheme.lower() in {"rtsp", "rtsps"}:
            command.extend(["-rtsp_transport", "tcp"])
        command.extend(
            [
                "-i",
                stream_url,
                "-an",
                "-vf",
                f"fps={self.fps},scale={self.width}:-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                str(self.quality),
            ]
        )
        if max_frames is not None:
            command.extend(["-frames:v", str(max_frames)])
        command.append("pipe:1")
        return command

    def iter_frames(
        self,
        max_frames: int | None = None,
        *,
        resolved: ResolvedStream | None = None,
    ) -> Iterator[FramePacket]:
        if max_frames is not None and max_frames < 1:
            return
        if not self.ffmpeg_path:
            raise MediaSourceError("找不到內含的 FFmpeg runtime；請修復或重新安裝 MeowKeeper+。")
        resolved = resolved or resolve_source(self.source_url, resolver=self.resolver)
        count = 0
        retries = 0
        is_network_source = urlsplit(resolved.stream_url).scheme.lower() in {
            "rtsp",
            "rtsps",
            "http",
            "https",
        }
        while True:
            process = self._popen(
                self.build_command(
                    resolved.stream_url,
                    max_frames=max_frames - count if max_frames is not None else None,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                shell=False,
                bufsize=0,
            )
            stderr_chunks: deque[bytes] = deque(maxlen=4)
            stderr_reader = self._drain_process_stderr(process, stderr_chunks)
            if process.stdout is None:
                self.last_error = "FFmpeg 沒有建立影格輸出管線。"
                self._close_process(process)
                stderr_reader.join(timeout=2)
                raise MediaSourceError(self.last_error)
            frames_before_attempt = count
            try:
                for jpeg in iter_jpeg_frames(process.stdout):
                    count += 1
                    yield FramePacket(count, datetime.now(timezone.utc), self.source_url, jpeg)
                    if max_frames is not None and count >= max_frames:
                        return
            finally:
                return_code = self._close_process(process)
                stderr_reader.join(timeout=2)
                stderr_summary = b"".join(stderr_chunks)[-4096:].decode("utf-8", errors="replace")
                if stderr_summary:
                    self.last_error = _safe_command_error(stderr_summary)

            if max_frames is not None and count >= max_frames:
                return
            if not is_network_source:
                if count == 0 and return_code not in (0, None):
                    self.last_error = "FFmpeg 未輸出影格；請檢查來源網址、工具與串流權限。"
                    raise MediaSourceError(self.last_error)
                return
            if retries >= self.retry_policy.max_retries:
                if count == frames_before_attempt and return_code not in (0, None):
                    self.last_error = "FFmpeg 重連次數已用盡；請檢查網路、來源網址與串流權限。"
                    raise MediaSourceError(self.last_error)
                return
            retries += 1
            self.restart_count += 1
            self.last_error = "FFmpeg 串流中斷，準備以指數退避重啟。"
            self._sleep(self.retry_policy.delay_seconds(retries))

    @staticmethod
    def _close_process(process: subprocess.Popen[bytes]) -> int | None:
        """終止子程序並回傳結束碼；清理失敗不得掩蓋來源錯誤。"""

        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            return process.returncode
        except (OSError, subprocess.TimeoutExpired):
            return getattr(process, "returncode", None)

    @staticmethod
    def _drain_process_stderr(
        process: subprocess.Popen[bytes], chunks: deque[bytes]
    ) -> threading.Thread:
        """Continuously drain FFmpeg errors so a noisy decoder cannot block JPEG output."""

        stream = getattr(process, "stderr", None)

        def drain() -> None:
            if stream is None:
                return
            try:
                while payload := stream.read(4096):
                    chunks.append(payload if isinstance(payload, bytes) else str(payload).encode())
            except (OSError, ValueError):
                return

        reader = threading.Thread(target=drain, name="meowkeeper-ffmpeg-stderr", daemon=True)
        reader.start()
        return reader
