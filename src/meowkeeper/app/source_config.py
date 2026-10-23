"""Dashboard 可用的媒體來源與 Telegram 啟用設定。

這個模組只保存非秘密設定。YouTube 只保存使用者提供的頁面網址；RTSP
若需要帳號密碼，必須由啟動環境變數提供，Web 設定不接受 URL userinfo。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from meowkeeper.core.media.sources import is_youtube_url

SOURCE_TYPES = {"youtube", "rtsp", "video", "mock"}
YOUTUBE_HEIGHTS = {360, 480, 720, 1080}

# These are the non-secret values exposed by the Dashboard AI/behaviour page.
# Keep them in one small contract so a restart loads exactly what the page
# saved, while unknown config sections remain untouched.
RUNTIME_SETTING_DEFAULTS: dict[str, Any] = {
    "analyzer_mode": "yolo",
    "inference_device": "auto",
    "confidence_threshold": 0.45,
    "identity_accepted_threshold": 0.62,
    "identity_minimum_margin": 0.05,
    "analysis_fps": 5,
    "analysis_width": 640,
    "activity_fps": 5,
    "identity_frame_width": 1280,
    "preview_frame_width": 1920,
    "cooldown_seconds": 180.0,
    "event_pre_seconds": 5.0,
    "event_post_seconds": 10.0,
    "retention_seconds": 60.0,
    "ring_maximum_size_mb": 512.0,
    "dashboard_auto_open": False,
    "telegram_startup_notice": False,
}

_RUNTIME_SETTING_RANGES: dict[str, tuple[float, float]] = {
    "confidence_threshold": (0.0, 1.0),
    "identity_accepted_threshold": (0.0, 1.0),
    "identity_minimum_margin": (0.0, 1.0),
    "analysis_fps": (1.0, 5.0),
    "analysis_width": (640.0, 1920.0),
    "activity_fps": (1.0, 5.0),
    "identity_frame_width": (640.0, 1280.0),
    "preview_frame_width": (640.0, 1920.0),
    "cooldown_seconds": (0.0, 86400.0),
    "event_pre_seconds": (0.0, 120.0),
    "event_post_seconds": (0.0, 180.0),
    "retention_seconds": (10.0, 120.0),
    "ring_maximum_size_mb": (16.0, 4096.0),
}


@dataclass(frozen=True)
class SourceConfig:
    """可持久化的來源頁面設定；不含 signed media URL 或秘密。"""

    source_type: str = "mock"
    url: str = ""
    max_height: int = 720
    video: bool = True
    audio: bool = False

    def validate(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError("來源類型只支援 YouTube、RTSP、本地影片或 Mock。")
        if self.max_height not in YOUTUBE_HEIGHTS:
            raise ValueError("YouTube 最高畫質只支援 360、480、720 或 1080。")
        url = self.url.strip()
        if self.source_type == "mock":
            if url:
                raise ValueError("Mock 來源不需要網址。")
            return
        if not url or len(url) > 2048:
            raise ValueError("來源網址不可為空白或過長。")
        parsed = urlsplit(url)
        if self.source_type == "youtube":
            if not is_youtube_url(url):
                raise ValueError("YouTube 來源必須是 youtube.com 或 youtu.be 頁面網址。")
        elif self.source_type == "rtsp":
            if parsed.scheme.lower() not in {"rtsp", "rtsps"}:
                raise ValueError("RTSP 來源必須使用 rtsp:// 或 rtsps://。")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Web 設定不可保存 RTSP 帳密；請使用來源環境變數。")
        elif self.source_type == "video" and parsed.scheme:
            is_windows_path = len(parsed.scheme) == 1 and len(url) > 2 and url[1:3] in {":\\", ":/"}
            if not is_windows_path:
                raise ValueError("本地影片來源必須是本機檔案路徑，不可使用網路網址。")

    @property
    def configured(self) -> bool:
        return self.source_type != "mock" and bool(self.url.strip())

    def to_public_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_type": self.source_type,
            "url": self.url,
            "configured": self.configured,
            "max_height": self.max_height,
            "video": self.video,
            "audio": self.audio,
            "message_zh_tw": (
                "來源已設定；簽名媒體網址只在記憶體中使用。"
                if self.configured
                else "尚未設定來源。"
            ),
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("來源設定檔無法讀取或不是有效 JSON。") from exc
    return payload if isinstance(payload, dict) else {}


def load_source_config(path: str | Path) -> SourceConfig:
    payload = _read_json(Path(path))
    media = payload.get("media") if isinstance(payload.get("media"), Mapping) else {}
    source_type = str(media.get("source_type") or "mock").strip().lower()
    youtube = media.get("youtube") if isinstance(media.get("youtube"), Mapping) else {}
    rtsp = media.get("rtsp") if isinstance(media.get("rtsp"), Mapping) else {}
    if source_type == "youtube":
        url = str(youtube.get("url") or media.get("url") or "").strip()
    elif source_type == "rtsp":
        url = str(rtsp.get("url") or media.get("url") or "").strip()
    elif source_type == "video":
        video_source = media.get("video") if isinstance(media.get("video"), Mapping) else {}
        url = str(video_source.get("url") or media.get("url") or "").strip()
    else:
        url = str(media.get("url") or "").strip()
    try:
        max_height = int(youtube.get("max_height", 720)) if source_type == "youtube" else 720
    except (TypeError, ValueError):
        max_height = 720
    config = SourceConfig(
        source_type=source_type,
        url=url,
        max_height=max_height,
        video=bool(youtube.get("video", True)) if source_type == "youtube" else True,
        audio=bool(youtube.get("audio", False)) if source_type == "youtube" else False,
    )
    config.validate()
    return config


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_source_config(path: str | Path, config: SourceConfig) -> None:
    config.validate()
    target = Path(path)
    payload = _read_json(target)
    media = dict(payload.get("media") or {}) if isinstance(payload.get("media"), Mapping) else {}
    media["source_type"] = config.source_type
    # Switching the active type must not erase the previous type's settings.
    # This retains only validated original URLs, never resolved media URLs.
    if config.source_type == "youtube":
        section = dict(media["youtube"]) if isinstance(media.get("youtube"), Mapping) else {}
        section.update(url=config.url, max_height=config.max_height, video=config.video, audio=config.audio)
        media["youtube"] = section
    elif config.source_type in {"rtsp", "video"}:
        section = dict(media[config.source_type]) if isinstance(media.get(config.source_type), Mapping) else {}
        section["url"] = config.url
        media[config.source_type] = section
    # Legacy readers still use media.url for local files; keep active value
    # consistent so clearing/switching a source never resurrects an old URL.
    media["url"] = config.url
    payload["media"] = media
    payload["schema_version"] = max(int(payload.get("schema_version", 3)), 3)
    _atomic_write(target, payload)


def load_telegram_enabled(path: str | Path) -> bool:
    payload = _read_json(Path(path))
    section = payload.get("telegram")
    return bool(section.get("enabled", False)) if isinstance(section, Mapping) else False


def save_telegram_enabled(path: str | Path, enabled: bool) -> None:
    target = Path(path)
    payload = _read_json(target)
    section = dict(payload.get("telegram") or {}) if isinstance(payload.get("telegram"), Mapping) else {}
    section["enabled"] = bool(enabled)
    payload["telegram"] = section
    payload["schema_version"] = max(int(payload.get("schema_version", 3)), 3)
    _atomic_write(target, payload)


def load_go2rtc_rtsp_lan_access(path: str | Path) -> bool:
    """Read the explicit, non-secret RTSP LAN opt-in (default: disabled)."""

    payload = _read_json(Path(path))
    section = payload.get("go2rtc")
    return bool(section.get("rtsp_lan_access_enabled", False)) if isinstance(section, Mapping) else False


def save_go2rtc_rtsp_lan_access(path: str | Path, enabled: bool) -> None:
    """Persist only the RTSP LAN opt-in while preserving all other settings."""

    target = Path(path)
    payload = _read_json(target)
    section = dict(payload.get("go2rtc") or {}) if isinstance(payload.get("go2rtc"), Mapping) else {}
    section["rtsp_lan_access_enabled"] = bool(enabled)
    payload["go2rtc"] = section
    payload["schema_version"] = max(int(payload.get("schema_version", 3)), 3)
    _atomic_write(target, payload)


def _coerce_runtime_setting(name: str, value: Any) -> Any:
    """Return a safe value or the default for a malformed persisted value."""

    default = RUNTIME_SETTING_DEFAULTS[name]
    if name == "inference_device":
        return value if isinstance(value, str) and value in {"auto", "cpu", "intel:gpu", "onnx:cuda"} else default
    if name == "analyzer_mode":
        return value if isinstance(value, str) and value in {"yolo", "v02"} else default
    if name in _RUNTIME_SETTING_RANGES:
        if isinstance(value, bool):
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        low, high = _RUNTIME_SETTING_RANGES[name]
        if not low <= number <= high:
            return default
        if name in {"identity_frame_width", "preview_frame_width", "analysis_width"} and not number.is_integer():
            return default
        if name == "analysis_width" and int(number) not in {640, 960, 1280, 1920}:
            return default
        return int(number) if name.endswith("_fps") or name in {"identity_frame_width", "preview_frame_width", "analysis_width"} else number
    return value if isinstance(value, bool) else default


def load_runtime_settings(path: str | Path) -> dict[str, Any]:
    """Load the Dashboard's non-secret AI/media/behaviour settings.

    ``dashboard_settings`` is deliberately a flat, private-to-this-UI section;
    source URL, Telegram secrets and unknown configuration sections are not
    read or rewritten here.  Older example configs are accepted through the
    canonical nested media/inference/behaviour fields as a fallback.
    """

    payload = _read_json(Path(path))
    values = dict(RUNTIME_SETTING_DEFAULTS)
    section = payload.get("dashboard_settings")
    if isinstance(section, Mapping):
        for name in values:
            if name in section:
                values[name] = _coerce_runtime_setting(name, section[name])

    inference = payload.get("inference") if isinstance(payload.get("inference"), Mapping) else {}
    behavior = payload.get("behavior") if isinstance(payload.get("behavior"), Mapping) else {}
    media = payload.get("media") if isinstance(payload.get("media"), Mapping) else {}
    ring = media.get("ring_buffer") if isinstance(media.get("ring_buffer"), Mapping) else {}
    events = media.get("events") if isinstance(media.get("events"), Mapping) else {}
    ai = payload.get("ai") if isinstance(payload.get("ai"), Mapping) else {}
    fallbacks = {
        "confidence_threshold": ai.get("confidence_threshold"),
        "identity_accepted_threshold": ai.get("identity_accepted_threshold"),
        "identity_minimum_margin": ai.get("identity_minimum_margin"),
        "analysis_fps": inference.get("active_yolo_fps"),
        "activity_fps": behavior.get("behavior_sampling_hz"),
        "cooldown_seconds": behavior.get("cooldown_seconds"),
        "event_pre_seconds": events.get("pre_seconds"),
        "event_post_seconds": events.get("post_seconds"),
        "retention_seconds": ring.get("retention_seconds"),
        "ring_maximum_size_mb": ring.get("maximum_size_mb"),
    }
    section_keys = set(section) if isinstance(section, Mapping) else set()
    for name, candidate in fallbacks.items():
        if name not in section_keys and candidate is not None:
            values[name] = _coerce_runtime_setting(name, candidate)
    return values


def save_runtime_settings(path: str | Path, values: Mapping[str, Any]) -> None:
    """Atomically persist only validated, non-secret Dashboard settings."""

    target = Path(path)
    current = load_runtime_settings(target)
    for name, value in values.items():
        if name not in RUNTIME_SETTING_DEFAULTS:
            raise ValueError(f"不支援的執行設定：{name}。")
        if name == "analyzer_mode" and (not isinstance(value, str) or value not in {"yolo", "v02"}):
            raise ValueError("分析模式只能是基本找貓或 V0.2 實驗性行為。")
        coerced = _coerce_runtime_setting(name, value)
        if name == "inference_device" and coerced != value:
            raise ValueError("運算裝置選項不正確。")
        if name in _RUNTIME_SETTING_RANGES and coerced == RUNTIME_SETTING_DEFAULTS[name] and value != coerced:
            # A malformed value must not silently become a valid-looking saved
            # default; the Web contract normally catches this first.
            try:
                if float(value) != float(coerced):
                    raise ValueError(f"執行設定 {name} 超出允許範圍。")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"執行設定 {name} 格式不正確。") from exc
        current[name] = coerced
    payload = _read_json(target)
    payload["dashboard_settings"] = current
    payload["schema_version"] = max(int(payload.get("schema_version", 3)), 3)
    _atomic_write(target, payload)


class SourceConfigService:
    """受保護 Web API 使用的來源設定邊界。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def config(self) -> SourceConfig:
        return load_source_config(self.path)

    def snapshot(self) -> dict[str, Any]:
        result = self.config().to_public_dict()
        payload = _read_json(self.path)
        media = payload.get("media") if isinstance(payload.get("media"), Mapping) else {}
        remembered: dict[str, Any] = {}
        youtube = media.get("youtube") if isinstance(media.get("youtube"), Mapping) else {}
        for kind in ("youtube", "rtsp", "video"):
            section = media.get(kind)
            if not isinstance(section, Mapping) or not section.get("url"):
                continue
            try:
                candidate = SourceConfig(
                    source_type=kind, url=str(section["url"]),
                    max_height=int(youtube.get("max_height", 720)) if kind == "youtube" else 720,
                    video=bool(youtube.get("video", True)) if kind == "youtube" else True,
                    audio=bool(youtube.get("audio", False)) if kind == "youtube" else False,
                )
                remembered[kind] = candidate.to_public_dict()
            except (TypeError, ValueError):
                # Never echo a malformed legacy URL containing credentials.
                continue
        result["remembered_sources"] = remembered
        return result

    def validate_update(self, values: Mapping[str, Any]) -> SourceConfig:
        if not isinstance(values, Mapping):
            raise TypeError("來源設定必須是 JSON object。")
        source_type = str(values.get("source_type", "mock")).strip().lower()
        config = SourceConfig(
            source_type=source_type,
            url=str(values.get("url", "")).strip(),
            max_height=int(values.get("max_height", 720)),
            video=bool(values.get("video", True)),
            audio=bool(values.get("audio", False)),
        )
        config.validate()
        return config

    def save(self, config: SourceConfig) -> dict[str, Any]:
        save_source_config(self.path, config)
        return config.to_public_dict()


__all__ = [
    "RUNTIME_SETTING_DEFAULTS",
    "SOURCE_TYPES",
    "YOUTUBE_HEIGHTS",
    "SourceConfig",
    "SourceConfigService",
    "load_go2rtc_rtsp_lan_access",
    "load_runtime_settings",
    "load_source_config",
    "load_telegram_enabled",
    "save_go2rtc_rtsp_lan_access",
    "save_runtime_settings",
    "save_source_config",
    "save_telegram_enabled",
]
