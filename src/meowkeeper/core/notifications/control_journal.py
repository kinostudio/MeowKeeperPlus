"""Bounded, secret-free audit trail for explicit Xiaomi control operations."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_FINAL_STATUSES = {
    "confirmed_success",
    "command_accepted_unverified",
    "unsupported",
    "timeout",
    "readback_failed",
    "confirmed_no_change",
    "failed",
}
_SAFE_TARGET_KEYS = {"action", "direction", "angle", "elevation", "privacy_mode", "mode"}


def new_operation_id(value: object = None) -> str:
    """Accept a caller correlation id only if it is a canonical UUID."""
    try:
        return str(uuid.UUID(str(value))) if value else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid4())


class CameraControlJournal:
    """Persist only a small rolling window of camera control outcomes.

    Camera identifiers are one-way hashed. The journal intentionally accepts
    a strict field allowlist and never stores source URLs, credentials or raw
    adapter exceptions.
    """

    def __init__(self, path: Path | None = None, *, limit: int = 50) -> None:
        self.path = Path(path) if path is not None else None
        self.limit = max(1, min(int(limit), 200))
        self._lock = threading.RLock()
        self._entries = self._load()
        self._persistence_available = True

    @staticmethod
    def _camera_ref(camera_id: object) -> str:
        value = str(camera_id or "unknown").strip() or "unknown"
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _target(value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        target: dict[str, Any] = {}
        for key in _SAFE_TARGET_KEYS:
            item = value.get(key)
            if key in {"angle", "elevation"}:
                if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 101:
                    target[key] = item
            elif key == "privacy_mode":
                if type(item) is bool:
                    target[key] = item
            elif item is not None:
                text = str(item)
                if len(text) <= 48 and re.fullmatch(r"[A-Za-z0-9_-]+", text):
                    target[key] = text
        return target

    @staticmethod
    def _safe_reason(value: object) -> str | None:
        if value is None:
            return None
        reason = str(value)[:240]
        reason = re.sub(
            r"(?i)(token|password|authorization|source_url|xiaomi://)\s*[:=]?\s*\S+",
            r"\1=[已遮罩]",
            reason,
        )
        return reason

    def _load(self) -> list[dict[str, Any]]:
        if self.path is None:
            return []
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, list):
                return [item for item in value[-self.limit:] if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        return []

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(self._entries[-self.limit:], ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
            self._persistence_available = True
        except OSError:
            self._persistence_available = False

    def append(
        self,
        *,
        operation_id: str | None = None,
        operation_type: str,
        camera_id: object,
        requested_target: object = None,
        command_sent: bool | None = None,
        transport_result: str = "not_attempted",
        readback_attempted: bool = False,
        readback_result: object = "not_attempted",
        observed_change: bool | None = None,
        final_status: str = "failed",
        failure_reason: object = None,
        before_position: object = None,
    ) -> dict[str, Any]:
        status = final_status if final_status in _FINAL_STATUSES else "failed"
        entry = {
            "operation_id": new_operation_id(operation_id),
            "operation_type": str(operation_type)[:40],
            "camera_id": self._camera_ref(camera_id),
            "request_time": datetime.now(timezone.utc).isoformat(),
            "requested_target": self._target(requested_target),
            "command_sent": command_sent if type(command_sent) is bool else None,
            "transport_result": str(transport_result)[:48],
            "readback_attempted": bool(readback_attempted),
            "readback_result": readback_result if isinstance(readback_result, (str, int, float, bool, type(None), dict)) else "unavailable",
            "observed_change": observed_change if type(observed_change) is bool else None,
            "final_status": status,
            "failure_reason": self._safe_reason(failure_reason),
        }
        if isinstance(before_position, Mapping):
            before = self._target(before_position)
            if "angle" in before and "elevation" in before:
                entry["before_position"] = {"angle": before["angle"], "elevation": before["elevation"]}
        with self._lock:
            self._entries.append(entry)
            self._entries = self._entries[-self.limit:]
            self._save()
        return dict(entry)

    def record_readback(self, operation_id: str, camera_id: object, *, verified: bool,
                        position: Mapping[str, Any] | None) -> None:
        camera_ref = self._camera_ref(camera_id)
        with self._lock:
            entry = next((item for item in reversed(self._entries)
                          if item.get("operation_id") == operation_id
                          and item.get("camera_id") == camera_ref
                          and item.get("operation_type", "").startswith("ptz_")), None)
            if entry is None:
                return
            entry["readback_attempted"] = True
            if not verified or not isinstance(position, Mapping):
                entry["readback_result"] = "failed"
                entry["final_status"] = "readback_failed"
                if not entry.get("failure_reason"):
                    entry["failure_reason"] = "攝影機沒有回傳可驗證的位置。"
            else:
                angle, elevation = position.get("angle"), position.get("elevation")
                if (type(angle) is not int or type(elevation) is not int
                        or not 0 <= angle <= 101 or not 0 <= elevation <= 101):
                    entry["readback_result"] = "failed"
                    entry["final_status"] = "readback_failed"
                    entry["failure_reason"] = "攝影機位置回讀格式不正確。"
                else:
                    actual = {"angle": angle, "elevation": elevation}
                    entry["readback_result"] = {"verified": True, **actual}
                    before = entry.get("before_position")
                    if isinstance(before, Mapping):
                        entry["observed_change"] = before != actual
                    target = entry.get("requested_target", {})
                    if target.get("action") == "position":
                        reached = (abs(angle - int(target.get("angle", -999))) <= 2
                                   and abs(elevation - int(target.get("elevation", -999))) <= 2)
                        entry["final_status"] = "confirmed_success" if reached else "command_accepted_unverified"
                        if not reached:
                            entry["failure_reason"] = "已讀回目前位置，但尚未到達選取目標。"
                        else:
                            entry["failure_reason"] = None
                    elif target.get("action") == "move" and entry.get("observed_change") is not None:
                        entry["final_status"] = ("confirmed_success" if entry["observed_change"]
                                                  else "confirmed_no_change")
                        entry["failure_reason"] = None if entry["observed_change"] else "位置讀回未改變。"
            self._save()

    def recent(self, operation_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            items = list(reversed(self._entries[-self.limit:]))
            if operation_id is not None:
                try:
                    requested_id = str(uuid.UUID(operation_id))
                except (ValueError, TypeError, AttributeError):
                    requested_id = ""
                items = [item for item in items if item.get("operation_id") == requested_id]
            return {
                "items": [dict(item) for item in items],
                "retained_count": len(items) if operation_id is not None else min(len(self._entries), self.limit),
                "limit": self.limit,
                "persistent": self.path is not None and self._persistence_available,
                "message_zh_tw": "只保留最近 50 筆控制紀錄；攝影機識別碼已雜湊，未保存帳密或來源網址。",
            }


__all__ = ["CameraControlJournal", "new_operation_id"]
