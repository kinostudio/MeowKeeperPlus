"""小米裝置與畫質資料；不得根據產品名稱假設型號支援。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class XiaomiDeviceIdentity:
    model: str
    product_code: str | None = None
    device_code: str | None = None
    region: str | None = None
    firmware_version: str | None = None
    confirmed_by_user: bool = False

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("小米機型必須由使用者提供，不能留白。")
        if not self.confirmed_by_user:
            # Identity is still useful for a plan, but may not be treated as supported.
            return

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class XiaomiQualityProfile:
    name: str
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    stable: bool
    duration_seconds: float | None = None
    error: str | None = None

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)


@dataclass(frozen=True)
class XiaomiSourceProfile:
    device: XiaomiDeviceIdentity
    local_url: str
    bind_host: str = "127.0.0.1"
    selected_quality: str | None = None
    status: str = "not_checked"

    def __post_init__(self) -> None:
        if self.bind_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("小米 bridge 只允許綁定 localhost。")
        if not self.local_url.startswith(("rtsp://127.0.0.1", "rtsp://localhost", "http://127.0.0.1", "http://localhost")):
            raise ValueError("小米 bridge URL 必須是本機 restream。")

    def to_dict(self) -> dict[str, Any]:
        return {"device": self.device.to_dict(), "local_url": self.local_url, "bind_host": self.bind_host, "selected_quality": self.selected_quality, "status": self.status}
