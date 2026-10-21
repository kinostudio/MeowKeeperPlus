"""Two verified per-camera Telegram positions, separate from privacy poses."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class PositionPresetError(ValueError):
    """Safe user-facing failure; never constructed from upstream raw errors."""


class PositionPresetService:
    def __init__(self, adapter: Any, path: Path) -> None:
        self.adapter = adapter
        self.path = path
        self._lock = Lock()

    @staticmethod
    def _position(response: Mapping[str, Any]) -> dict[str, int]:
        if not isinstance(response, Mapping):
            raise PositionPresetError("攝影機沒有回傳位置資料，尚未取得可驗證座標。")
        state = response.get("state")
        pose = state.get("position") if isinstance(state, Mapping) else None
        if (response.get("success") is not True or not isinstance(state, Mapping)
                or state.get("connected") is not True or not isinstance(pose, Mapping)
                or pose.get("ret") != 0):
            reason = {
                "unsupported": "目前遠端中繼未支援座標讀回與指定位置；方向控制可用不代表定位可用。",
                "disabled": "Xiaomi MISS PTZ 尚未啟用；沒有移動攝影機。",
                "not_ready": "目前來源沒有已配對的活動 Xiaomi 串流。",
                "unavailable": "目前無法讀回位置；請確認目前來源與活動串流。",
            }.get(response.get("status"), "目前沒有已確認的位置讀回。")
            raise PositionPresetError(reason)
        result = {key: pose.get(key) for key in ("angle", "elevation")}
        # Native MISS can read elevation=0 at an end-stop, although absolute
        # target commands only permit 1..101. A valid current zero position
        # must not prevent moving to a previously saved legal target.
        if any(type(value) is not int or not 0 <= value <= 101 for value in result.values()):
            raise PositionPresetError("讀回座標不在控制協定有效範圍。")
        return result

    def _context(self) -> tuple[str, str]:
        device = self.adapter.device()
        if not isinstance(device, str) or not device:
            raise PositionPresetError("目前沒有已接入的單一 Xiaomi 攝影機。")
        return device, hashlib.sha256(device.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "cameras": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("cameras"), dict):
            raise PositionPresetError("位置設定檔案無法讀取；未覆寫原紀錄。")
        return data

    def save_next(self) -> dict[str, Any]:
        with self._lock:
            device, reference = self._context()
            pose = self._position(self.adapter.read(device))
            if any(value == 0 for value in pose.values()):
                raise PositionPresetError("已讀到鏡頭位於座標 0 的邊界，但指定位置只接受 1–101；請稍微移離邊界再儲存，未修改舊位置。")
            data = self._load()
            entry = data["cameras"].setdefault(reference, {"next_slot": 1, "slots": {}})
            if not isinstance(entry, dict) or entry.get("next_slot") not in (1, 2) or not isinstance(entry.get("slots"), dict):
                raise PositionPresetError("位置設定格式不正確；沒有變更。")
            slot = entry["next_slot"]
            overwritten = str(slot) in entry["slots"]
            entry["slots"][str(slot)] = pose
            entry["next_slot"] = 2 if slot == 1 else 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return {"success": True, "slot": slot, "overwritten": overwritten,
                    "message_zh_tw": f"已{'覆寫' if overwritten else '儲存'}位置{'一' if slot == 1 else '二'}；已讀回確認。"}

    def diagnostic(self) -> dict[str, Any]:
        """Read-only binding check; never expose IDs, paths or saved coordinates."""
        with self._lock:
            try:
                _device, reference = self._context()
            except PositionPresetError:
                return {"status": "no_active_device", "active_device_present": False,
                        "message_zh_tw": "目前來源未配對至單一 Xiaomi 裝置；未移動鏡頭。"}
            try:
                data = self._load()
            except (OSError, ValueError, TypeError):
                return {"status": "storage_unreadable", "active_device_present": True,
                        "message_zh_tw": "目前 Host 無法讀取位置設定；未修改紀錄。"}
            entry = data["cameras"].get(reference)
            slots = entry.get("slots", {}) if isinstance(entry, Mapping) else {}
            if not isinstance(slots, Mapping):
                slots = {}
            summary = {}
            for slot in (1, 2):
                pose = slots.get(str(slot))
                summary[str(slot)] = {
                    "present": pose is not None,
                    "target_valid": isinstance(pose, Mapping) and set(pose) == {"angle", "elevation"}
                    and all(type(pose[key]) is int and 1 <= pose[key] <= 101 for key in ("angle", "elevation")),
                }
            return {"status": "matched" if isinstance(entry, Mapping) else "different_or_unsaved_device",
                    "active_device_present": True, "settings_file_present": self.path.is_file(),
                    "stored_camera_count": len(data["cameras"]), "current_camera_saved": isinstance(entry, Mapping),
                    "slots": summary, "message_zh_tw": "只核對目前來源與儲存位置，不會移動或覆寫。"}

    def go_to(self, slot: int) -> dict[str, Any]:
        if type(slot) is not int or slot not in (1, 2):
            raise PositionPresetError("只能選擇位置一或位置二。")
        with self._lock:
            device, reference = self._context()
            data = self._load()
            pose = data["cameras"].get(reference, {}).get("slots", {}).get(str(slot))
            if not isinstance(pose, dict) or set(pose) != {"angle", "elevation"}:
                raise PositionPresetError("這台攝影機尚未儲存此位置。")
            if any(type(value) is not int or not 1 <= value <= 101 for value in pose.values()):
                raise PositionPresetError("儲存座標不合法；沒有移動攝影機。")
            self._position(self.adapter.read(device))
            requested_at = datetime.now(timezone.utc)
            response = self.adapter.move(device, **pose)
            if response.get("success") is not True:
                raise PositionPresetError("位置移動未確認；請查看即時畫面。")
            deadline = time.monotonic() + 10
            matches = 0
            last_stamp = None
            while True:
                if self.adapter.device() != device:
                    raise PositionPresetError("監控來源已切換；沒有宣稱定位成功。")
                try:
                    reading = self.adapter.read(device)
                    observed = self._position(reading)
                    stamp = reading["state"]["position"].get("updated_at")
                    captured = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    fresh = (captured.tzinfo is not None and requested_at <= captured <= datetime.now(timezone.utc))
                    if observed == pose and fresh and stamp != last_stamp:
                        matches += 1
                        last_stamp = stamp
                        if matches >= 2:
                            return {"success": True, "slot": slot,
                                    "message_zh_tw": f"已回到位置{'一' if slot == 1 else '二'}；兩次新鮮座標讀回一致。"}
                    else:
                        matches = 0
                except (ValueError, OSError, RuntimeError):
                    pass
                if time.monotonic() >= deadline:
                    raise PositionPresetError("未取得兩次定位後的新鮮一致座標；可能是讀回未更新或移動未完成，請到即時畫面按 ■ 停止。")
                time.sleep(0.5)
