"""Per-recipient cat alert preferences; never stores raw Telegram IDs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path


class CatNotificationPreferences:
    """Persist alert delivery choices independently from the media pipeline."""

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None,
                 local_timezone: tzinfo | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._local_timezone = local_timezone
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, object]] = {}
        self._corrupt = False
        try:
            if self.path.exists():
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if payload.get("version") not in (1, 2) or not isinstance(payload.get("recipients"), dict):
                    raise ValueError("unsupported preference schema")
                for key, value in payload["recipients"].items():
                    if (not isinstance(key, str) or len(key) != 64 or
                            not isinstance(value, dict) or type(value.get("enabled")) is not bool or
                            not isinstance(value.get("mute_until"), (str, type(None))) or
                            type(value.get("feedback_enabled", False)) is not bool):
                        raise ValueError("invalid preference entry")
                    minutes = value.get("cooldown_minutes", 0)
                    window = value.get("sleep_window")
                    if window is not None:
                        if not isinstance(window, dict):
                            raise ValueError("invalid sleep window")
                        self._validate_sleep(window.get("start_hour"), window.get("end_hour"))
                    if type(minutes) is not int or not 0 <= minutes <= 10080:
                        raise ValueError("invalid cooldown")
                    last = value.get("last_cat_attempt_at")
                    if last is not None and (
                        not isinstance(last, str) or datetime.fromisoformat(last).tzinfo is None
                    ):
                        raise ValueError("invalid cooldown timestamp")
                self._entries = payload["recipients"]
        except (OSError, ValueError, TypeError, AttributeError):
            # An unreadable existing file must not silently re-enable alerts.
            self._corrupt = True

    @staticmethod
    def _key(chat_id: str) -> str:
        return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()

    def status(self, chat_id: str) -> dict[str, object]:
        with self._lock:
            if self._corrupt:
                return {"enabled": False, "muted": True, "mute_until": None,
                        "feedback_enabled": False, "storage_error": True}
            entry = self._entries.get(self._key(chat_id), {})
            enabled = entry.get("enabled", True) is True
            until_raw = entry.get("mute_until")
            until = None
            if isinstance(until_raw, str):
                try:
                    until = datetime.fromisoformat(until_raw).astimezone(timezone.utc)
                except ValueError:
                    return {"enabled": False, "muted": True, "mute_until": None,
                            "feedback_enabled": False, "storage_error": True}
            now = self._clock().astimezone(timezone.utc)
            local_now = now.astimezone(self._local_timezone)
            window = entry.get("sleep_window")
            sleep_active = False
            if isinstance(window, dict):
                start, end = window["start_hour"], window["end_hour"]
                sleep_active = (start <= local_now.hour < end if start < end
                                else local_now.hour >= start or local_now.hour < end)
            muted = until is not None and until > now
            minutes = int(entry.get("cooldown_minutes", 0))
            last = entry.get("last_cat_attempt_at")
            remaining = 0
            if minutes and isinstance(last, str):
                end = datetime.fromisoformat(last).astimezone(timezone.utc) + timedelta(minutes=minutes)
                remaining = max(0, math.ceil((end - now).total_seconds()))
            return {"enabled": enabled, "muted": muted, "mute_until": until if muted else None,
                    "feedback_enabled": entry.get("feedback_enabled", False) is True, "storage_error": False,
                    "sleep_window": dict(window) if isinstance(window, dict) else None,
                    "sleep_active": sleep_active,
                    "cooldown_minutes": minutes, "cooldown_remaining_seconds": remaining}

    def feedback_enabled(self, chat_id: str) -> bool:
        state = self.status(chat_id)
        return state["feedback_enabled"] is True and state["storage_error"] is False

    def set_feedback_enabled(self, chat_id: str, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("回饋開關必須為布林值。")
        self._update(chat_id, feedback_enabled=enabled)

    def should_send(self, chat_id: str, event_type: str) -> bool:
        if event_type != "cat_detected":
            return True
        state = self.status(chat_id)
        return (state["enabled"] is True and state["muted"] is False and state["storage_error"] is False
                and state.get("sleep_active", False) is False
                and state.get("cooldown_remaining_seconds", 0) == 0)

    @staticmethod
    def _validate_sleep(start: object, end: object) -> None:
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= 23 or not 0 <= end <= 23 or start == end):
            raise ValueError("每日靜音須為 0～23 的不同整點。")

    def set_sleep_window(self, chat_id: str, start: int, end: int) -> None:
        self._validate_sleep(start, end)
        self._update(chat_id, sleep_window={"start_hour": start, "end_hour": end})

    def clear_sleep_window(self, chat_id: str) -> None:
        self._update(chat_id, sleep_window=None)

    def set_cooldown_minutes(self, chat_id: str, minutes: int) -> None:
        if type(minutes) is not int or not 0 <= minutes <= 10080:
            raise ValueError("通知間隔須為 0 至 10080 分鐘的整數。")
        self._update(chat_id, cooldown_minutes=minutes)

    def claim_delivery(self, chat_id: str, event_type: str) -> bool:
        """Atomically reserve a delivery slot across both automatic alert workers.

        Persist before network I/O: a timeout may have delivered the message, so
        failures conservatively consume the slot instead of causing a flood.
        Manual commands and non-cat alerts do not consume this budget.
        """
        with self._lock:
            if not self.should_send(chat_id, event_type):
                return False
            if event_type == "cat_detected" and self.status(chat_id)["cooldown_minutes"]:
                self._update(chat_id, last_cat_attempt_at=self._clock().astimezone(timezone.utc).isoformat())
            return True

    def set_enabled(self, chat_id: str, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("通知開關必須為布林值。")
        self._update(chat_id, enabled=enabled)

    def mute_hours(self, chat_id: str, hours: int) -> datetime:
        if type(hours) is not int or not 1 <= hours <= 168:
            raise ValueError("靜音時間須為 1 至 168 小時的整數。")
        until = self._clock().astimezone(timezone.utc) + timedelta(hours=hours)
        self._update(chat_id, mute_until=until.isoformat())
        return until

    def unmute(self, chat_id: str) -> None:
        self._update(chat_id, mute_until=None)

    def _update(self, chat_id: str, **changes: object) -> None:
        with self._lock:
            if self._corrupt:
                raise OSError("通知偏好檔案無法讀取；為避免誤發，請先修復該檔案。")
            key = self._key(chat_id)
            next_entries = {name: dict(value) for name, value in self._entries.items()}
            entry = next_entries.setdefault(key, {"enabled": True, "mute_until": None,
                                                  "feedback_enabled": False})
            entry.update(changes)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            try:
                temporary.write_text(
                    json.dumps({"version": 2, "recipients": next_entries}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self._entries = next_entries


__all__ = ["CatNotificationPreferences"]
