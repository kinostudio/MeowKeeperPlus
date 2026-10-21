"""Dashboard 設定及 API 輸入的無副作用驗證。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class SettingsValidationError(ValueError):
    """設定不符合安全範圍。"""


_SECRET_WORDS = re.compile(r"(?:token|password|passwd|secret|stream[_-]?key|auth[_-]?key|pin)", re.IGNORECASE)
_FORBIDDEN_COMMAND_WORDS = re.compile(r"(?:ffmpeg|shell|command|subprocess|exec|pipe|filtergraph)", re.IGNORECASE)


@dataclass(frozen=True)
class SettingsPatch:
    """可由 Dashboard 修改的非秘密設定白名單。"""

    values: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", validate_settings_patch(self.values))


def validate_settings_patch(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise SettingsValidationError("設定內容必須是 JSON object。")
    cleaned: dict[str, Any] = {}
    ranges: dict[str, tuple[float, float]] = {
        "confidence_threshold": (0.0, 1.0),
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
        "dashboard_port": (1024.0, 65535.0),
    }
    allowed = set(ranges) | {
        "analyzer_mode",
        "inference_device",
        "source_profile",
        "dashboard_auto_open",
        "telegram_startup_notice",
        "lan_access_enabled",
    }
    for key, value in values.items():
        name = str(key)
        if _SECRET_WORDS.search(name) or _FORBIDDEN_COMMAND_WORDS.search(name):
            raise SettingsValidationError(f"設定欄位 {name} 不允許由 Web API 直接修改。")
        if name not in allowed:
            raise SettingsValidationError(f"不支援的設定欄位：{name}。")
        if name in ranges:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SettingsValidationError(f"設定欄位 {name} 必須是數字。")
            low, high = ranges[name]
            if not low <= float(value) <= high:
                raise SettingsValidationError(f"設定欄位 {name} 必須介於 {low:g} 與 {high:g}。")
            if name in {"identity_frame_width", "preview_frame_width", "analysis_width"} and not float(value).is_integer():
                raise SettingsValidationError("影格寬度與分析尺寸必須是整數。")
            if name == "analysis_width" and int(value) not in {640, 960, 1280, 1920}:
                raise SettingsValidationError("AI 推論尺寸只支援 640、960、1280 或 1920。")
            if name.endswith("_fps") or name in {"dashboard_port", "identity_frame_width", "preview_frame_width", "analysis_width"}:
                cleaned[name] = int(value)
            else:
                cleaned[name] = float(value)
        elif name == "inference_device":
            if not isinstance(value, str) or value not in {"auto", "cpu", "intel:gpu", "onnx:cuda"}:
                raise SettingsValidationError("運算裝置只支援自動、CPU、Intel 內顯或 NVIDIA 外顯。")
            cleaned[name] = value
        elif name == "analyzer_mode":
            if not isinstance(value, str) or value not in {"yolo", "v02"}:
                raise SettingsValidationError("分析模式只能是 yolo 或 v02。")
            cleaned[name] = value
        elif name == "source_profile":
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise SettingsValidationError("來源 profile 名稱格式不正確。")
            cleaned[name] = value.strip()
        else:
            if not isinstance(value, bool):
                raise SettingsValidationError(f"設定欄位 {name} 必須是布林值。")
            cleaned[name] = value
    return cleaned


def validate_settings_with_pydantic(values: Mapping[str, Any]) -> dict[str, Any]:
    """發布 runtime 有 Pydantic 時先走 model validation，缺少時安全降級純範圍檢查。"""

    try:
        from pydantic import BaseModel, ConfigDict, Field, ValidationError
    except ImportError:
        return validate_settings_patch(values)

    class SettingsModel(BaseModel):
        model_config = ConfigDict(extra="forbid")
        confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
        analysis_fps: int | None = Field(default=None, ge=1, le=5)
        analysis_width: int | None = Field(default=None, ge=640, le=1920)
        activity_fps: int | None = Field(default=None, ge=1, le=5)
        identity_frame_width: int | None = Field(default=None, ge=640, le=1280)
        preview_frame_width: int | None = Field(default=None, ge=640, le=1920)
        cooldown_seconds: float | None = Field(default=None, ge=0.0, le=86400.0)
        event_pre_seconds: float | None = Field(default=None, ge=0.0, le=120.0)
        event_post_seconds: float | None = Field(default=None, ge=0.0, le=180.0)
        retention_seconds: float | None = Field(default=None, ge=10.0, le=120.0)
        ring_maximum_size_mb: float | None = Field(default=None, ge=16.0, le=4096.0)
        dashboard_port: int | None = Field(default=None, ge=1024, le=65535)
        source_profile: str | None = Field(default=None, min_length=1, max_length=120)
        analyzer_mode: str | None = None
        inference_device: str | None = None
        dashboard_auto_open: bool | None = None
        telegram_startup_notice: bool | None = None
        lan_access_enabled: bool | None = None

    try:
        model = SettingsModel.model_validate(dict(values))
    except ValidationError as exc:
        raise SettingsValidationError("設定未通過 Pydantic 範圍或欄位驗證。") from exc
    return validate_settings_patch(model.model_dump(exclude_none=True))


def public_settings(values: Mapping[str, Any]) -> dict[str, Any]:
    """只返回已知安全設定，避免前端拿到 token、密碼或完整來源 URL。"""

    result: dict[str, Any] = {}
    for key, value in values.items():
        if _SECRET_WORDS.search(str(key)):
            continue
        if isinstance(value, str) and ("@" in value or "://" in value) and str(key).lower().endswith("url"):
            result[key] = "[已設定；來源網址不在 API 回應中顯示]"
        else:
            result[key] = value
    return result
