"""Tailscale Serve 狀態保存與恢復的純資料契約。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServeSnapshot:
    payload: dict[str, Any]

    @classmethod
    def capture(cls, payload: Mapping[str, Any]) -> ServeSnapshot:
        return cls(dict(payload))

    def restore_plan(self) -> dict[str, Any]:
        return {"status": "restore_ready", "previous": dict(self.payload), "requires_confirmation": True}


def safe_serve_url(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("url") or payload.get("URL")
    if not isinstance(value, str) or not value.startswith("https://"):
        return None
    return value if "?" not in value else value.split("?", 1)[0]
