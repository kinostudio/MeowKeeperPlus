"""公開 YouTube 來源 adapter。

這個模組只保存使用者提供的 YouTube 頁面網址。yt-dlp 解析出的短期媒體
網址只存在於單次 FFmpeg 啟動所需的記憶體物件中，不會出現在設定、資料庫、
狀態快照或日誌。adapter 不是下載器，也不提供 Cookie、OAuth、DRM 或登入
繞過流程。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from meowkeeper.runtime import bundled_binary, bundled_javascript_runtime, installed_script

from .sources import FramePacket, MediaSourceError, ResolvedStream, is_youtube_url, iter_jpeg_frames

YTDLP_PINNED_VERSION = "2026.7.4"
YTDLP_EJS_PINNED_VERSION = "0.8.0"
YOUTUBE_MAX_HEIGHTS = (360, 480, 720, 1080)
YOUTUBE_RECONNECT_BACKOFF_SECONDS = (3.0, 6.0, 12.0, 24.0, 60.0)


class YouTubeSourceStatus(str, Enum):
    RESOLVING = "resolving"
    CONNECTING = "connecting"
    STREAMING = "streaming"
    WAITING = "waiting"
    ENDED = "ended"
    RECONNECTING = "reconnecting"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True)
class YouTubeSourceConfig:
    """可保存的 YouTube 設定；不包含解析後媒體網址。"""

    url: str
    max_height: int = 720
    video: bool = True
    audio: bool = False
    source_type: str = "youtube"

    def validate(self) -> None:
        if self.source_type != "youtube":
            raise ValueError("YouTube 來源 type 必須是 youtube。")
        if not is_youtube_url(self.url):
            raise ValueError("YouTube 來源必須是 youtube.com 或 youtu.be 網址。")
        if self.max_height not in YOUTUBE_MAX_HEIGHTS:
            allowed = ", ".join(str(value) for value in YOUTUBE_MAX_HEIGHTS)
            raise ValueError(f"YouTube max_height 必須是 {allowed}。")
        if not self.video and not self.audio:
            raise ValueError("YouTube 來源至少需要啟用 video 或 audio。")

    def to_config(self) -> dict[str, Any]:
        """輸出可保存設定，永遠不輸出解析後媒體 URL。"""

        self.validate()
        return {
            "source_type": "youtube",
            "url": self.url,
            "max_height": self.max_height,
            "video": self.video,
            "audio": self.audio,
        }


@dataclass(frozen=True)
class YouTubeMediaInfo:
    video_id: str | None
    title: str | None
    is_live: bool
    live_status: str | None
    selected_height: int | None
    video_codec: str | None
    audio_codec: str | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "is_live": self.is_live,
            "live_status": self.live_status,
            "selected_height": self.selected_height,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
        }


@dataclass(frozen=True)
class YouTubeRuntimeCheck:
    status: str
    yt_dlp: dict[str, Any]
    ffmpeg: dict[str, Any]
    ffprobe: dict[str, Any]
    javascript_runtime: dict[str, Any]
    yt_dlp_ejs: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "yt_dlp": dict(self.yt_dlp),
            "ffmpeg": dict(self.ffmpeg),
            "ffprobe": dict(self.ffprobe),
            "javascript_runtime": dict(self.javascript_runtime),
            "yt_dlp_ejs": dict(self.yt_dlp_ejs),
        }


class YouTubeUnsupportedError(MediaSourceError):
    """YouTube 需要登入、Cookie、DRM 或其他第一版不支援的流程。"""


@dataclass(frozen=True)
class _ResolvedYouTube:
    info: YouTubeMediaInfo
    input_urls: tuple[str, ...]


def _safe_error(text: object) -> str:
    value = str(text or "")
    value = re.sub(r"https?://[^\s]+", "<YouTube來源>", value)
    value = re.sub(
        r"(?:cookie|token|authorization|password)\s*[=:]\s*[^\r\n]+",
        "<已遮罩>",
        value,
        flags=re.IGNORECASE,
    )
    value = " ".join(value.split())
    return value[-500:] if value else "外部工具沒有提供錯誤摘要。"


def _unsupported_error(text: str) -> bool:
    lowered = text.casefold()
    return any(
        marker in lowered
        for marker in (
            "private video",
            "members-only",
            "members only",
            "sign in to confirm your age",
            "sign in to confirm you're not a bot",
            "sign in to confirm you are not a bot",
            "age-restricted",
            "age restricted",
            "this video is private",
            "this video is unavailable",
            "login to view this video",
            "drm",
            "cookies are required",
            "login required",
            "authentication required",
        )
    )


def _tool_version(path: str | None, run: Callable[..., subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    if not path:
        return {"status": "not_checked", "available": False}
    try:
        version_flag = "--version" if Path(path).name.lower().startswith("yt-dlp") else "-version"
        result = run(
            [path, version_flag],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "available": False, "error": type(exc).__name__}
    version = (result.stdout or "").strip().splitlines()[0] if result.stdout else None
    is_yt_dlp = Path(path).name.lower().startswith("yt-dlp")
    normalized_version = _normalize_version(version)
    normalized_pin = _normalize_version(YTDLP_PINNED_VERSION)
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "available": result.returncode == 0,
        "version": version,
        "path": Path(path).name,
        "pinned_version": YTDLP_PINNED_VERSION if is_yt_dlp else None,
        "version_matches_pin": normalized_version == normalized_pin if is_yt_dlp else None,
    }


def _normalize_version(value: str | None) -> str:
    if not value:
        return ""
    parts = value.strip().split(".")
    normalized: list[str] = []
    for part in parts:
        if part.isdigit():
            normalized.append(str(int(part)))
        else:
            normalized.append(part)
    return ".".join(normalized)


def check_youtube_runtime(
    *,
    yt_dlp_path: str | None = None,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    js_runtime_path: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> YouTubeRuntimeCheck:
    """唯讀檢查 YouTube adapter 所需的外部 runtime。"""

    yt_dlp = yt_dlp_path or installed_script("yt-dlp")
    ffmpeg = ffmpeg_path or bundled_binary("ffmpeg")
    ffprobe = ffprobe_path or bundled_binary("ffprobe")
    runtime = js_runtime_path or bundled_javascript_runtime()
    yt_dlp_ejs_available = importlib.util.find_spec("yt_dlp_ejs") is not None
    try:
        yt_dlp_ejs_version = importlib.metadata.version("yt-dlp-ejs")
    except importlib.metadata.PackageNotFoundError:
        yt_dlp_ejs_version = None
    items = {
        "yt_dlp": _tool_version(yt_dlp, run),
        "ffmpeg": _tool_version(ffmpeg, run),
        "ffprobe": _tool_version(ffprobe, run),
        "javascript_runtime": {
            "status": "ok" if runtime else "not_checked",
            "available": bool(runtime),
            "path": Path(runtime).name if runtime else None,
        },
        "yt_dlp_ejs": {
            "status": "ok" if yt_dlp_ejs_available else "not_checked",
            "available": yt_dlp_ejs_available,
            "version": yt_dlp_ejs_version,
            "pinned_version": YTDLP_EJS_PINNED_VERSION,
            "version_matches_pin": yt_dlp_ejs_version == YTDLP_EJS_PINNED_VERSION,
        },
    }
    required = (items["yt_dlp"], items["ffmpeg"], items["ffprobe"])
    yt_dlp_matches = items["yt_dlp"].get("version_matches_pin") is True
    ejs_matches = items["yt_dlp_ejs"].get("version_matches_pin") is True
    status = "ok" if all(item["available"] for item in required) and yt_dlp_matches and runtime and ejs_matches else "warning"
    return YouTubeRuntimeCheck(status=status, **items)


class YouTubeSourceAdapter:
    """單一 resolver／FFmpeg producer 的公開 YouTube FrameSource。"""

    _lease_guard = threading.Lock()
    _leases: ClassVar[dict[str, dict[str, bool]]] = {}

    def __init__(
        self,
        config: YouTubeSourceConfig,
        *,
        yt_dlp_path: str | None = None,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        js_runtime_path: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        config.validate()
        self.config = config
        self.yt_dlp_path = yt_dlp_path or installed_script("yt-dlp")
        self.ffmpeg_path = ffmpeg_path or bundled_binary("ffmpeg")
        self.ffprobe_path = ffprobe_path or bundled_binary("ffprobe")
        self.js_runtime_path = js_runtime_path or bundled_javascript_runtime()
        self._run = run
        self._popen = popen
        self._sleep = sleep
        self._stop = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._downloader_process: subprocess.Popen[bytes] | None = None
        self._status = YouTubeSourceStatus.WAITING.value
        self._last_error: str | None = None
        self._last_info: YouTubeMediaInfo | None = None
        self._reconnect_count = 0
        self._source_key = hashlib.sha256(config.url.encode("utf-8")).hexdigest()[:16]

    @property
    def status(self) -> str:
        return self._status

    @property
    def media_info(self) -> YouTubeMediaInfo | None:
        return self._last_info

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def runtime_check(self) -> YouTubeRuntimeCheck:
        return check_youtube_runtime(
            yt_dlp_path=self.yt_dlp_path,
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
            js_runtime_path=self.js_runtime_path,
            run=self._run,
        )

    def status_snapshot(self) -> dict[str, Any]:
        info = self._last_info.to_public_dict() if self._last_info else None
        with self._lease_guard:
            lease = dict(self._leases.get(self._source_key, {}))
        return {
            "source_type": "youtube",
            "source_id": self._source_key,
            "status": self._status,
            "media_info": info,
            "resolver_active": bool(lease.get("resolver")),
            "producer_active": bool(lease.get("producer")),
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
        }

    def resolve(self, source_url: str | None = None, quality: str = "best") -> ResolvedStream:
        if source_url is not None and source_url.strip() != self.config.url:
            raise MediaSourceError("YouTube resolver 收到與設定不同的來源網址。")
        resolved = self._resolve_once()
        return ResolvedStream(self.config.url, resolved.input_urls[0], "yt-dlp")

    def iter_frames(
        self,
        max_frames: int | None = None,
        *,
        resolved: ResolvedStream | None = None,
    ) -> Iterator[FramePacket]:
        if max_frames is not None and max_frames < 1:
            return
        if not self.ffmpeg_path:
            self._status = YouTubeSourceStatus.FAILED.value
            raise MediaSourceError("找不到內含的 FFmpeg runtime；請修復或重新安裝 MeowKeeper+。")
        if not self._acquire("producer"):
            raise MediaSourceError("同一 YouTube 來源已有 FFmpeg producer，拒絕建立第二個來源程序。")

        self._stop.clear()
        count = 0
        backoff_index = 0
        initial_resolved = resolved
        try:
            while not self._stop.is_set():
                try:
                    if initial_resolved is not None and self._last_info is not None:
                        current = _ResolvedYouTube(self._last_info, (initial_resolved.stream_url,))
                        initial_resolved = None
                    else:
                        current = self._resolve_once()
                except YouTubeUnsupportedError:
                    raise
                except MediaSourceError as exc:
                    self._last_error = str(exc)[:500]
                    if backoff_index >= len(YOUTUBE_RECONNECT_BACKOFF_SECONDS):
                        self._status = YouTubeSourceStatus.FAILED.value
                        raise
                    self._reconnect_count += 1
                    self._status = YouTubeSourceStatus.RECONNECTING.value
                    self._wait_before_retry(backoff_index)
                    backoff_index += 1
                    continue
                self._last_info = current.info
                self._status = YouTubeSourceStatus.CONNECTING.value
                process = self._start_process(current)
                self._process = process
                if process.stdout is None:
                    self._close_process(process)
                    self._process = None
                    self._close_downloader_process()
                    self._fail_or_retry("FFmpeg 沒有提供 JPEG 影格輸出。", backoff_index)
                    backoff_index += 1
                    continue
                frames_before = count
                try:
                    self._status = YouTubeSourceStatus.STREAMING.value
                    for jpeg in iter_jpeg_frames(process.stdout):
                        count += 1
                        yield FramePacket(count, datetime.now(timezone.utc), self.config.url, jpeg)
                        if max_frames is not None and count >= max_frames:
                            return
                finally:
                    return_code, stderr = self._close_process(process)
                    self._process = None
                    self._close_downloader_process()
                if count > frames_before:
                    backoff_index = 0
                if self._stop.is_set() or (max_frames is not None and count >= max_frames):
                    return
                error_text = _safe_error(stderr)
                self._last_error = error_text if stderr else self._last_error
                normal_end = return_code in (0, None) and not current.info.is_live and count > frames_before
                if normal_end:
                    self._status = YouTubeSourceStatus.ENDED.value
                    return
                if backoff_index >= len(YOUTUBE_RECONNECT_BACKOFF_SECONDS):
                    self._status = YouTubeSourceStatus.FAILED.value
                    raise MediaSourceError("YouTube 來源重連次數已達上限。")
                self._reconnect_count += 1
                self._status = YouTubeSourceStatus.RECONNECTING.value
                self._wait_before_retry(backoff_index)
                backoff_index += 1
        finally:
            self.close()
            if self._status == YouTubeSourceStatus.STREAMING.value:
                self._status = YouTubeSourceStatus.ENDED.value
            self._release("producer")

    def close(self) -> None:
        self._stop.set()
        process = self._process
        self._process = None
        if process is not None:
            self._close_process(process)
        self._close_downloader_process()

    def build_ffmpeg_command(
        self, resolved: _ResolvedYouTube, *, input_from_pipe: bool = False
    ) -> list[str]:
        if not self.ffmpeg_path:
            raise MediaSourceError("找不到 FFmpeg runtime。")
        command = [self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin"]
        input_urls = ("pipe:0",) if input_from_pipe else resolved.input_urls
        for url in input_urls:
            command.extend(["-i", url])
        command.extend(["-map", "0:v:0?"])
        if self.config.audio:
            audio_input = "1:a:0?" if len(resolved.input_urls) > 1 else "0:a:0?"
            command.extend(["-map", audio_input])
        else:
            command.append("-an")
        command.extend(
            [
                "-vf",
                "fps=5,scale=640:-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "5",
                "pipe:1",
            ]
        )
        return command

    def build_download_command(self) -> list[str]:
        """Use finite HTTP chunks while keeping signed media URLs in memory."""

        if not self.yt_dlp_path:
            raise MediaSourceError("找不到固定版 yt-dlp runtime；請修復或重新安裝 MeowKeeper+。")
        command = [
            self.yt_dlp_path,
            "--no-warnings",
            "--quiet",
            "--no-playlist",
            "--no-cache-dir",
            "--no-remote-components",
            "--http-chunk-size",
            "1M",
            "--format",
            self._format_selector(),
        ]
        if self.js_runtime_path:
            command.extend(["--js-runtimes", f"node:{self.js_runtime_path}"])
        command.extend(["--output", "-", self.config.url])
        return command

    def start_download_process(self) -> subprocess.Popen[bytes]:
        """Start one pinned yt-dlp upstream producer for the FFmpeg pipe."""

        try:
            process = self._popen(
                self.build_download_command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                shell=False,
                bufsize=0,
            )
        except (FileNotFoundError, OSError) as exc:
            raise MediaSourceError(f"yt-dlp 媒體輸入啟動失敗：{type(exc).__name__}。") from exc
        self._downloader_process = process
        return process

    def _resolve_once(self) -> _ResolvedYouTube:
        if not self._acquire("resolver"):
            raise MediaSourceError("同一 YouTube 來源已有 resolver，拒絕重複解析。")
        self._status = YouTubeSourceStatus.RESOLVING.value
        try:
            if not self.yt_dlp_path:
                self._status = YouTubeSourceStatus.FAILED.value
                raise MediaSourceError("找不到固定版 yt-dlp runtime；請修復或重新安裝 MeowKeeper+。")
            selector = self._format_selector()
            command = [
                self.yt_dlp_path,
                "--no-warnings",
                "--no-playlist",
                "--no-cache-dir",
                "--no-remote-components",
                "--dump-single-json",
                "--skip-download",
                "--format",
                selector,
            ]
            if self.js_runtime_path:
                command.extend(["--js-runtimes", f"node:{self.js_runtime_path}"])
            command.append(self.config.url)
            try:
                result = self._run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=False,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
                self._status = YouTubeSourceStatus.FAILED.value
                raise MediaSourceError(f"yt-dlp 無法執行：{type(exc).__name__}。") from exc
            if result.returncode != 0:
                safe = _safe_error(result.stderr)
                if _unsupported_error(result.stderr or ""):
                    self._status = YouTubeSourceStatus.UNSUPPORTED.value
                    raise YouTubeUnsupportedError("此 YouTube 來源需要私人登入、Cookie、DRM 或年齡驗證，第一版不支援。")
                self._status = YouTubeSourceStatus.FAILED.value
                raise MediaSourceError(f"YouTube yt-dlp 解析失敗：{safe}")
            try:
                payload = json.loads(result.stdout or "")
            except (TypeError, json.JSONDecodeError) as exc:
                self._status = YouTubeSourceStatus.FAILED.value
                raise MediaSourceError("yt-dlp 回傳的媒體資訊不是有效 JSON。") from exc
            resolved = self._parse_resolution(payload)
            self._last_info = resolved.info
            return resolved
        finally:
            self._release("resolver")

    def _parse_resolution(self, payload: Any) -> _ResolvedYouTube:
        if not isinstance(payload, dict):
            raise MediaSourceError("YouTube 媒體資訊格式不正確。")
        requested = payload.get("requested_formats")
        urls: list[str] = []
        formats = requested if isinstance(requested, list) else [payload]
        for item in formats:
            if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].startswith(("http://", "https://")):
                urls.append(item["url"])
        if not urls:
            raise MediaSourceError("yt-dlp 沒有回傳可用的 YouTube 媒體串流。")
        video_format = next((item for item in formats if isinstance(item, dict) and item.get("vcodec") not in (None, "none")), {})
        audio_format = next((item for item in formats if isinstance(item, dict) and item.get("acodec") not in (None, "none")), {})
        height = video_format.get("height") or payload.get("height")
        try:
            selected_height = int(height) if height is not None else None
        except (TypeError, ValueError):
            selected_height = None
        live_status = payload.get("live_status")
        is_live = bool(payload.get("is_live") or live_status == "is_live")
        info = YouTubeMediaInfo(
            video_id=str(payload.get("id")) if payload.get("id") else None,
            title=str(payload.get("title")) if payload.get("title") else None,
            is_live=is_live,
            live_status=str(live_status) if live_status else None,
            selected_height=selected_height,
            video_codec=str(video_format.get("vcodec") or payload.get("vcodec")) if (video_format.get("vcodec") or payload.get("vcodec")) else None,
            audio_codec=str(audio_format.get("acodec") or payload.get("acodec")) if (audio_format.get("acodec") or payload.get("acodec")) else None,
        )
        return _ResolvedYouTube(info=info, input_urls=tuple(urls[:2]))

    def _format_selector(self) -> str:
        height = self.config.max_height
        if self.config.video and self.config.audio:
            return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        if self.config.video:
            return f"bestvideo[height<={height}]/best[height<={height}]"
        return "bestaudio"

    def _start_process(self, resolved: _ResolvedYouTube) -> subprocess.Popen[bytes]:
        downloader = self.start_download_process()
        if downloader.stdout is None:
            self._close_downloader_process()
            raise MediaSourceError("yt-dlp 沒有提供媒體串流輸出。")
        try:
            process = self._popen(
                self.build_ffmpeg_command(resolved, input_from_pipe=True),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=downloader.stdout,
                shell=False,
                bufsize=0,
            )
            downloader.stdout.close()
            return process
        except (FileNotFoundError, OSError) as exc:
            self._close_downloader_process()
            self._status = YouTubeSourceStatus.FAILED.value
            raise MediaSourceError(f"FFmpeg 啟動失敗：{type(exc).__name__}。") from exc

    def _close_downloader_process(self) -> None:
        process = self._downloader_process
        self._downloader_process = None
        if process is not None:
            self._close_process(process)

    def _fail_or_retry(self, message: str, backoff_index: int) -> None:
        self._last_error = message
        if backoff_index >= len(YOUTUBE_RECONNECT_BACKOFF_SECONDS):
            self._status = YouTubeSourceStatus.FAILED.value
            raise MediaSourceError(message)
        self._reconnect_count += 1
        self._status = YouTubeSourceStatus.RECONNECTING.value
        self._wait_before_retry(backoff_index)

    def _wait_before_retry(self, index: int) -> None:
        self._status = YouTubeSourceStatus.WAITING.value
        self._stop.wait(YOUTUBE_RECONNECT_BACKOFF_SECONDS[index])

    def _acquire(self, kind: str) -> bool:
        with self._lease_guard:
            current = self._leases.setdefault(self._source_key, {})
            if current.get(kind):
                return False
            current[kind] = True
            return True

    def _release(self, kind: str) -> None:
        with self._lease_guard:
            current = self._leases.get(self._source_key)
            if not current:
                return
            current.pop(kind, None)
            if not current:
                self._leases.pop(self._source_key, None)

    @staticmethod
    def _close_process(process: subprocess.Popen[bytes]) -> tuple[int | None, str]:
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
            stream = getattr(process, "stderr", None)
            raw = stream.read(8192) if stream is not None else b""
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            return process.returncode, str(raw or "")
        except (OSError, subprocess.TimeoutExpired):
            return getattr(process, "returncode", None), ""


__all__ = [
    "YOUTUBE_MAX_HEIGHTS",
    "YOUTUBE_RECONNECT_BACKOFF_SECONDS",
    "YTDLP_EJS_PINNED_VERSION",
    "YTDLP_PINNED_VERSION",
    "YouTubeMediaInfo",
    "YouTubeRuntimeCheck",
    "YouTubeSourceAdapter",
    "YouTubeSourceConfig",
    "YouTubeSourceStatus",
    "YouTubeUnsupportedError",
    "check_youtube_runtime",
]
