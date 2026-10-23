"""Explicit, reversible privacy pose on the existing MISS PTZ producer.

This is position privacy, not a native camera shutter or recording disable.
The user must visually confirm that the saved pose hides the private area.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import RLock
from typing import Any


class MissPrivacyAdapter:
    def __init__(self, path: Path, *, device: Callable[[], str | None],
                 read: Callable[[str], Mapping[str, Any]],
                 move: Callable[[str, int, int], Mapping[str, Any]],
                 power: Callable[[str, bool | None], Mapping[str, Any]] | None = None) -> None:
        self.path, self.device, self.read, self.move = path, device, read, move
        self.power = power
        self._lock = RLock()

    def _power(self, device: str, on: bool | None = None) -> bool:
        if self.power is None:
            raise ValueError("MIoT 原生待機控制尚未接通。")
        result = self.power(device, on)
        if result.get("success") is not True or type(result.get("on")) is not bool:
            raise ValueError("沒有可驗證的 MIoT 開關讀回；不以斷線冒充關閉。")
        if on is not None and result["on"] is not on:
            raise ValueError("MIoT 開關讀回與要求不符。")
        return result["on"]

    @staticmethod
    def _position(response: Mapping[str, Any]) -> dict[str, int]:
        state = response.get("state")
        position = state.get("position") if isinstance(state, Mapping) else None
        if response.get("success") is not True or not isinstance(state, Mapping) or state.get("connected") is not True:
            raise ValueError("攝影機未連線或位置未確認。")
        if not isinstance(position, Mapping) or position.get("ret") != 0:
            raise ValueError("攝影機沒有成功的位置讀回結果。")
        result = {key: position.get(key) for key in ("angle", "elevation")}
        if any(type(value) is not int or not 1 <= value <= 101 for value in result.values()):
            raise ValueError("攝影機位置不合法。")
        return result

    @staticmethod
    def _require_absolute_move(response: Mapping[str, Any]) -> None:
        state = response.get("state")
        capabilities = state.get("capabilities") if isinstance(state, Mapping) else None
        if not isinstance(capabilities, Mapping) or capabilities.get("absolute_move") is not True:
            raise ValueError("此攝影機尚無實機絕對定位成功證據；隱私位置操作未執行。")

    def _context(self) -> tuple[str, str]:
        device = self.device()
        if not device:
            raise ValueError("沒有已接入的單一 Xiaomi 攝影機。")
        return device, hashlib.sha256(device.encode()).hexdigest()

    def _load(self) -> dict[str, Any]:
        try:
            content = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(content, dict):
            raise TypeError("隱私位置設定格式不正確。")
        return content

    def _save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def _restore_pose(self, device: str, pose: Mapping[str, int]) -> None:
        # Power-on reconnects the existing ingest; never open a second source.
        deadline = time.monotonic() + 25
        while True:
            try:
                self._position(self.read(device))
                break
            except (ValueError, OSError, RuntimeError):
                if time.monotonic() >= deadline:
                    raise ValueError("攝影機已開啟，但 PTZ 尚未恢復；原位置紀錄保留，可重試恢復。") from None
                time.sleep(1)
        result = self.move(device, **pose)
        if result.get("success") is not True:
            raise ValueError("恢復原位置未確認；原位置紀錄仍保留。")
        while True:
            try:
                if self._position(self.read(device)) == pose:
                    return
            except (ValueError, OSError, RuntimeError):
                pass
            if time.monotonic() >= deadline:
                raise ValueError("原位置讀回不符；未宣稱恢復成功，紀錄保留。")
            time.sleep(1)

    def capture_private_pose(self) -> dict[str, Any]:
        """Called only after an authenticated, explicit visual confirmation."""
        with self._lock:
            device, reference = self._context()
            old = self._load()
            if old.get("restore") or old.get("standby_restore") is not None:
                raise ValueError("請先恢復原位置，再重新設定隱私位置。")
            reading = self.read(device)
            self._require_absolute_move(reading)
            pose = self._position(reading)
            self._save({"device_hash": reference, "target": pose, "restore": None, "mode": "position"})
            return {"success": True, "configured": True, "message_zh_tw": "已保存目前隱私位置；這不是原生鏡頭遮罩或停止錄影。"}

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in {"position", "standby"}:
            raise ValueError("隱私模式選項不合法。")
        with self._lock:
            _, reference = self._context()
            config = self._load()
            if config.get("restore") or config.get("standby_restore") is not None:
                raise ValueError("請先恢復原位置再切換模式。")
            if config.get("device_hash") != reference:
                config = {"device_hash": reference, "target": None, "restore": None}
            config["mode"] = mode
            self._save(config)
            return {
                "success": True, "mode": mode, "control_available": mode == "position" or self.power is not None,
                "message_zh_tw": "已選擇指定位置模式；請先保存隱私位置。" if mode == "position" else
                "已保存隱私待機選項；必須以 MIoT 原生開關讀回確認，不以 PTZ 或停止 AI 代替。",
            }

    def configuration(self) -> dict[str, Any]:
        with self._lock:
            _, reference = self._context()
            config = self._load()
            matches = config.get("device_hash") == reference
            return {
                "mode": config.get("mode", "position") if matches else "position",
                "configured": bool(matches and config.get("target")),
                "restore_pending": bool(matches and (config.get("restore") or config.get("standby_restore") is not None)),
                "standby_available": self.power is not None,
                "standby_verified": False,
            }

    def read_privacy_state(self) -> dict[str, Any]:
        with self._lock:
            device, reference = self._context()
            config = self._load()
            if config.get("mode", "position") == "standby":
                if config.get("device_hash") != reference:
                    return {"verified": False, "privacy_mode": None}
                on = self._power(device)
                if on and config.get("standby_restore") is not None:
                    # An open camera is not proof of restored motor position.
                    return {"verified": False, "privacy_mode": None}
                return {"verified": True, "privacy_mode": not on}
            if config.get("device_hash") != reference or not config.get("target"):
                return {"verified": False, "privacy_mode": None}
            pose = self._position(self.read(device))
            if pose == config["target"]:
                # A pose alone is not a reversible hide if no original pose
                # was saved (e.g. right after configuration).
                return {"verified": bool(config.get("restore")), "privacy_mode": True}
            if config.get("restore") and pose != config["restore"]:
                return {"verified": False, "privacy_mode": None}
            if config.get("restore") and pose == config["restore"]:
                config["restore"] = None
                self._save(config)
            return {"verified": True, "privacy_mode": False}

    def hide(self) -> dict[str, Any]:
        with self._lock:
            device, reference = self._context()
            config = self._load()
            if config.get("mode", "position") == "standby":
                if config.get("device_hash") != reference:
                    raise ValueError("隱私待機設定不屬於目前攝影機。")
                current = self._power(device)
                if not current:
                    return {"success": True}
                if config.get("standby_restore") is not None:
                    raise ValueError("先前待機恢復尚未完成；請先恢復監控，未覆蓋原位置。")
                reading = self.read(device)
                self._require_absolute_move(reading)
                config["standby_pose"] = self._position(reading)
                config["standby_restore"] = True
                self._save(config)
                self._power(device, False)
                return {"success": True}
            if config.get("device_hash") != reference or not config.get("target"):
                raise ValueError("尚未設定此攝影機的隱私位置。")
            reading = self.read(device)
            self._require_absolute_move(reading)
            current = self._position(reading)
            if current == config["target"]:
                return {"success": bool(config.get("restore"))}
            if config.get("restore"):
                raise ValueError("既有隱私操作尚未完成恢復，沒有覆蓋原位置。")
            config["restore"] = current
            self._save(config)  # Persist before moving so restart can recover.
            result = self.move(device, **config["target"])
            return {"success": result.get("success") is True}

    def show(self) -> dict[str, Any]:
        with self._lock:
            device, reference = self._context()
            config = self._load()
            powered_on = False

            # 若具備原生電源開關，優先嘗試上電喚醒（支援由外部米家 App 休眠後恢復）
            if self.power is not None:
                try:
                    self._power(device, True)
                    powered_on = True
                except Exception:
                    pass

            if config.get("mode", "position") == "standby":
                pose = config.get("standby_pose")
                if isinstance(pose, dict):
                    try:
                        self._restore_pose(device, pose)
                    except Exception:
                        pass
                config["standby_restore"] = None
                config.pop("standby_pose", None)
                self._save(config)
                return {"success": True}

            if config.get("device_hash") == reference and config.get("restore"):
                result = self.move(device, **config["restore"])
                if result.get("success") is not True:
                    return {"success": False}
                if self._position(self.read(device)) != config["restore"]:
                    return {"success": False}
                config["restore"] = None
                self._save(config)
                return {"success": True}

            if powered_on:
                return {"success": True}

            raise ValueError("此攝影機沒有可恢復的原位置或待機紀錄。")
