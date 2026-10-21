"""Native camera on/off; verified bool readback, never inferred from EOF."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from threading import Lock
from typing import Any

from meowkeeper.integrations.go2rtc.client import Go2RtcClient


class MiotPowerClient:
    def __init__(self, root: Path, config_path: Path, client: Go2RtcClient | None = None) -> None:
        self.directory = root / "vendor" / "go2rtc" / "miot"
        self.config_path = config_path
        self.client = client
        self._lock = Lock()
        self._cached: tuple[str, float, dict[str, Any]] | None = None

    def request(self, source: str, *, on: bool | None = None, timeout_seconds: float = 90) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"success": False, "error_code": "MIOT_BUSY"}
        try:
            reference = hashlib.sha256(source.encode()).hexdigest()
            if on is None and self._cached and self._cached[0] == reference and time.monotonic() - self._cached[1] < 15:
                return dict(self._cached[2])
            result = self._request(source, on=on, timeout_seconds=timeout_seconds)
            self._cached = (reference, time.monotonic(), dict(result)) if result.get("success") is True else None
            return result
        finally:
            self._lock.release()

    def _request(self, source: str, *, on: bool | None = None, timeout_seconds: float = 90) -> dict[str, Any]:
        binary = self.directory / "xiaomi-miot.exe"
        try:
            manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
            if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest["sha256"]:
                raise ValueError("checksum")
            payload = {"config": str(self.config_path), "source": source}
            if self.client is not None:
                config = self.client.request_json("/api/config")
                if not isinstance(config, str):
                    raise ValueError("config")
                payload["config_yaml"] = config  # stdin only; never saved or logged.
            if on is not None:
                if type(on) is not bool:
                    raise ValueError("on")
                payload["on"] = on
            result = subprocess.run(
                [str(binary)], input=json.dumps(payload), capture_output=True,
                text=True, encoding="utf-8", timeout=timeout_seconds, check=False,
                shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            # Neither raw stdout nor stderr may enter exceptions/logs.
            parsed = json.loads(result.stdout)
            allowed = {"success", "on", "model", "region", "siid", "piid", "stage", "error_code", "property_code"}
            if not isinstance(parsed, dict) or set(parsed) - allowed or result.returncode != 0:
                raise ValueError("response")
            if parsed.get("success") is not True or type(parsed.get("on")) is not bool:
                return {"success": False, "error_code": parsed.get("error_code", "MIOT_FAILED"),
                        "message_zh_tw": "無法讀回攝影機原生開關；未確認隱私待機。"}
            return parsed
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
            return {"success": False, "error_code": "MIOT_UNAVAILABLE",
                    "message_zh_tw": "MIoT 控制工具、登入 Session 或校驗不可用；沒有宣稱攝影機已關閉。"}
