"""Stable, secret-free go2rtc integration contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Go2RtcReleaseManifest:
    product: str
    repository: str
    release_tag: str
    asset_name: str
    download_url: str
    downloaded_at: str
    zip_size: int
    executable_size: int
    zip_sha256: str
    executable_sha256: str
    license: str
    minimum_windows: str
    verified_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "repository": self.repository,
            "release_tag": self.release_tag,
            "asset_name": self.asset_name,
            "download_url": self.download_url,
            "downloaded_at": self.downloaded_at,
            "zip_size": self.zip_size,
            "executable_size": self.executable_size,
            "zip_sha256": self.zip_sha256,
            "executable_sha256": self.executable_sha256,
            "license": self.license,
            "minimum_windows": self.minimum_windows,
            "verified_by": self.verified_by,
        }


@dataclass(frozen=True)
class Go2RtcConfig:
    api_host: str = "127.0.0.1"
    api_port: int = 1984
    rtsp_host: str = "127.0.0.1"
    rtsp_port: int = 8554
    streams: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.api_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("go2rtc API 只允許 localhost。")
        if self.rtsp_host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
            raise ValueError("go2rtc RTSP 只允許 localhost 或明確的區網 wildcard bind。")
        for port in (self.api_port, self.rtsp_port):
            if not 1 <= port <= 65535:
                raise ValueError("go2rtc port 必須介於 1 到 65535。")


@dataclass(frozen=True)
class Go2RtcStatus:
    state: str
    installed: bool
    healthy: bool
    pid: int | None = None
    version: str | None = None
    api_url: str = "http://127.0.0.1:1984"
    rtsp_url: str = "rtsp://127.0.0.1:8554"
    message_zh_tw: str = ""
    executable: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "installed": self.installed,
            "healthy": self.healthy,
            "pid": self.pid,
            "version": self.version,
            "api_url": self.api_url,
            "rtsp_url": self.rtsp_url,
            "message_zh_tw": self.message_zh_tw,
            "executable": str(self.executable) if self.executable else None,
        }
