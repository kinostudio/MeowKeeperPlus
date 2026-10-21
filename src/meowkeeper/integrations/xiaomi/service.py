"""Xiaomi bridge facade；沒有帳號或型號資料時只回報 not_checked。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .device_discovery import list_confirmed_devices
from .go2rtc_process import Go2rtcProcess
from .models import XiaomiDeviceIdentity, XiaomiSourceProfile
from .security import public_device_payload, reject_secret_fields


class XiaomiBridgeService:
    def __init__(self, *, process: Go2rtcProcess | None = None, discovery: Callable[[], list[Mapping[str, Any]]] | None = None) -> None:
        self.process = process or Go2rtcProcess()
        self.discovery = discovery

    def status(self) -> dict[str, Any]:
        return {"provider": "xiaomi_go2rtc", **self.process.status(), "auth": "user_browser_flow_only"}

    def discover(self) -> list[dict[str, Any]]:
        if self.discovery is None:
            return []
        return list_confirmed_devices(self.discovery())

    def create_source_profile(self, identity: XiaomiDeviceIdentity, local_url: str, *, quality: str | None = None) -> XiaomiSourceProfile:
        return XiaomiSourceProfile(identity, local_url, selected_quality=quality, status="experimental_not_verified")

    def validate_external_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        reject_secret_fields(payload)
        return public_device_payload(payload)
