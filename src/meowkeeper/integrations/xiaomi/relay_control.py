"""Authenticated, bounded control requests to a paired Tailnet relay.

This never exposes the relay's localhost go2rtc API to a browser or Tailnet.
The pairing nonce is a bearer secret and must be stored with Windows DPAPI.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

from .quality_options import LIVE_PROFILE_OPTIONS

CONTROL_PORT = 28555
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


class RelayControlError(RuntimeError):
    """Safe, non-secret relay control failure."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RelayControlError("遠端控制憑證只能由 Windows 安全保存。")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    source = ctypes.create_string_buffer(data)
    source_blob = _Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    if protect:
        success = crypt.CryptProtectData(ctypes.byref(source_blob), None, None, None, None, 1, ctypes.byref(result))
    else:
        success = crypt.CryptUnprotectData(ctypes.byref(source_blob), None, None, None, None, 1, ctypes.byref(result))
    if not success:
        raise RelayControlError("Windows 無法讀取遠端控制憑證；請重新配對。")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel.LocalFree(result.pbData)


class RelayControlClient:
    def __init__(self, address: str, nonce: str, *, port: int = CONTROL_PORT) -> None:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise RelayControlError("遠端控制位址不合法。") from exc
        if ip not in _TAILNET or not 1024 <= port <= 65535 or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
            raise RelayControlError("遠端控制配對資料不合法。")
        self._url = f"http://{ip}:{port}/v1/control"
        self._nonce = nonce

    def request(self, action: str, *, profile: str | None = None, direction: str | None = None,
                angle: int | None = None, elevation: int | None = None) -> dict[str, Any]:
        if action not in {"status", "profile", "ptz_state", "ptz_refresh", "ptz_position", "ptz_move", "ptz_stop"}:
            raise RelayControlError("不支援的遠端控制動作。")
        if action == "profile" and profile not in LIVE_PROFILE_OPTIONS:
            raise RelayControlError("遠端畫質選項不正確。")
        if action == "ptz_move" and direction not in {"left", "right", "up", "down"}:
            raise RelayControlError("遠端方向選項不正確。")
        payload = {"action": action, "profile": profile, "direction": direction}
        if action == "ptz_position":
            if any(type(v) is not int or not 1 <= v <= 101 for v in (angle, elevation)):
                raise RelayControlError("遠端位置必須介於 1 到 101。")
            payload.update(angle=angle, elevation=elevation)
        body = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self._url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self._nonce}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20 if action == "profile" else 6) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    raise RelayControlError("遠端控制通道回應不正確。")
                data = json.loads(response.read(4097))
        except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
            raise RelayControlError("遠端控制通道未連線或回應逾時；未確認攝影機已執行操作。") from exc
        if not isinstance(data, dict) or len(json.dumps(data)) > 4096:
            raise RelayControlError("遠端控制回應格式不正確。")
        return data


def save_control_nonce(path: Path, nonce: str) -> None:
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise RelayControlError("配對控制碼格式不正確。")
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _dpapi(nonce.encode("ascii"), protect=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(encrypted)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_control_nonce(path: Path) -> str:
    try:
        nonce = _dpapi(path.read_bytes(), protect=False).decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise RelayControlError("找不到遠端控制憑證；請重新配對。") from exc
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise RelayControlError("遠端控制憑證格式錯誤；請重新配對。")
    return nonce
