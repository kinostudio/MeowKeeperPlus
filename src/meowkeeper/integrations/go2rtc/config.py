"""Safe go2rtc config generation.

The API remains loopback-only. RTSP may use the explicit LAN opt-in
``0.0.0.0`` bind; the firewall boundary is handled separately.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Go2RtcConfig


def sanitize_stream_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    if not cleaned:
        raise ValueError("串流名稱不能為空。")
    return cleaned[:64]


def build_yaml(config: Go2RtcConfig) -> str:
    lines = [
        "api:",
        f"  listen: {config.api_host}:{config.api_port}",
        "rtsp:",
        f"  listen: {config.rtsp_host}:{config.rtsp_port}",
    ]
    if config.streams:
        lines.append("streams:")
        for name, source in sorted(config.streams.items()):
            safe_name = sanitize_stream_name(name)
            if "\n" in source or "\r" in source:
                raise ValueError("go2rtc source 不可含換行。")
            lines.append(f"  {safe_name}: {source}")
    return "\n".join(lines) + "\n"


def write_config(path: str | Path, config: Go2RtcConfig) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_yaml(config), encoding="utf-8")
    return destination
