"""已驗證攝影機設定記憶；不保存 Xiaomi source、DID 或帳號。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .quality_options import LIVE_PROFILE_OPTIONS


class CameraPreferences:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    @staticmethod
    def _key(device_id: str) -> str:
        return hashlib.sha256(device_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        width, height = value.get("width"), value.get("height")
        if type(width) is not int or type(height) is not int or not (1 <= width <= 16384 and 1 <= height <= 16384):
            return None
        result: dict[str, Any] = {"width": width, "height": height}
        fps = value.get("fps")
        if type(fps) in (int, float) and math.isfinite(fps) and 0 < fps <= 240:
            result["fps"] = fps
        for key in ("video_codec", "audio_codec"):
            codec = value.get(key)
            if isinstance(codec, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,32}", codec):
                result[key] = codec
        stamp = value.get("verified_at")
        if isinstance(stamp, str):
            try:
                parsed = datetime.fromisoformat(stamp)
                if parsed.tzinfo is not None:
                    result["verified_at"] = parsed.isoformat()
            except ValueError:
                pass
        return result

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return {}
        result = {}
        items = payload.get("cameras", {})
        if not isinstance(items, dict):
            return {}
        for key, value in list(items.items())[:256]:
            if not re.fullmatch(r"[a-f0-9]{64}", str(key)) or not isinstance(value, dict):
                continue
            profile, stream = value.get("profile"), value.get("stream_name")
            if profile not in LIVE_PROFILE_OPTIONS:
                continue
            if not isinstance(stream, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stream):
                continue
            result[key] = {"profile": profile, "stream_name": stream}
            metadata = self._metadata(value.get("metadata"))
            if metadata:
                result[key]["metadata"] = metadata
        return result

    def get(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read().get(self._key(device_id))

    def remember(self, device_id: str, profile: str, stream_name: str, *, metadata: dict | None = None) -> None:
        # Reuse the strict loader before writing, including all existing rows.
        with self._lock:
            if profile in {"sd", "hd", "auto"}:
                profile = f"subtype={profile}"
            if profile not in LIVE_PROFILE_OPTIONS:
                raise ValueError("不支援的已驗證画質。")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", stream_name):
                raise ValueError("串流識別格式不正確。")
            items = self._read()
            previous = items.get(self._key(device_id), {})
            items[self._key(device_id)] = {"profile": profile, "stream_name": stream_name}
            checked = self._metadata(metadata)
            if checked:
                checked["verified_at"] = datetime.now(timezone.utc).isoformat()
                items[self._key(device_id)]["metadata"] = checked
            elif previous.get("profile") == profile and previous.get("stream_name") == stream_name and previous.get("metadata"):
                items[self._key(device_id)]["metadata"] = previous["metadata"]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".camera-preferences-", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as output:
                    json.dump({"version": 1, "cameras": items}, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
