"""Tailscale CLI 輸出解析，所有欄位都以未知值容錯。"""

from __future__ import annotations

import json

from .models import TailscaleStatus


def parse_status_json(raw: str | bytes | bytearray | None) -> TailscaleStatus:
    if raw is None:
        return TailscaleStatus(status="error", message_zh_tw="Tailscale status 沒有回傳內容。")
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return TailscaleStatus(status="error", message_zh_tw="Tailscale status 回傳格式不正確。")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return TailscaleStatus(status="error", message_zh_tw="Tailscale status JSON 無法解析。")
    if not isinstance(payload, dict):
        return TailscaleStatus(status="error", message_zh_tw="Tailscale status 回傳格式不正確。")
    return TailscaleStatus.from_cli_json(payload)


def safe_version(raw: str) -> str | None:
    value = " ".join(raw.split())
    if not value or len(value) > 200 or any(secret in value.lower() for secret in ("key=", "token=", "authkey")):
        return None
    return value
