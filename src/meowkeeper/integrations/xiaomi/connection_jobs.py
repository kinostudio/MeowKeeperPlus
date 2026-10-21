"""One bounded camera connection workflow; public snapshots never contain sources."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any


class ConnectionJobs:
    def __init__(self, service: Any, *, timeout: float = 90) -> None:
        self.service, self.timeout = service, timeout
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.active: str | None = None
        self.closed = False

    def start(self, device: str, mode: str) -> dict:
        if not isinstance(mode, str) or mode not in {"local", "relay"} or self.service._resolve_device(device) is None:
            raise ValueError("請選擇攝影機與本機／中繼連線方式。")
        with self.lock:
            if self.closed:
                raise ValueError("服務正在關閉，請稍後再試。")
            if self.active:
                current = self.jobs[self.active]
                if current["device_id"] != device or current["connection_mode"] != mode:
                    raise ValueError("另一個接入工作正在進行；請等待完成後再切換攝影機或連線方式。")
                return self.snapshot(self.active)
            key = uuid.uuid4().hex
            self.jobs[key] = {"job_id": key, "device_id": device, "connection_mode": mode,
                              "stage": "checking", "status": "running", "success": False,
                              "message_zh_tw": "正在確認連線…", "started": time.monotonic()}
            self.active = key
            while len(self.jobs) > 32:
                self.jobs.pop(next(iter(self.jobs)))
            threading.Thread(target=self._run, args=(key,), daemon=True, name="xiaomi-connect").start()
            return self.snapshot(key)

    def snapshot(self, key: str) -> dict:
        with self.lock:
            job = self.jobs.get(key)
            if job is None:
                raise ValueError("找不到這次接入工作，請更新裝置狀態。")
            return {k: v for k, v in job.items() if k != "started"}

    def _stage(self, key: str, stage: str, message: str) -> None:
        with self.lock:
            if self.closed or time.monotonic() - self.jobs[key]["started"] > self.timeout:
                raise TimeoutError
            self.jobs[key].update(stage=stage, message_zh_tw=message)

    def _run(self, key: str) -> None:
        job = self.snapshot(key)
        service = self.service
        acquired = service._camera_control_lock.acquire(blocking=False)
        try:
            if not acquired:
                raise ValueError("攝影機正在進行其他操作，請等待完成。")
            device, mode = job["device_id"], job["connection_mode"]
            provider = service.relay_provider
            if provider.running and (provider.config.get("device_id") != device or mode != "local"):
                raise ValueError("此電腦正在分享攝影機；切換其他來源前請先停止中繼。")
            self._stage(key, "verifying", "正在驗證攝影機畫面…")
            if mode == "local":
                resolved = service._resolve_device(device)
                if resolved is None:
                    raise ValueError("裝置清單已變更，請重新選擇。")
                internal, record = resolved
                from .quality_probe import choose_highest_stable_profile
                if not service._remembered_preference(internal, record) and choose_highest_stable_profile(service._profiles.get(internal, [])) is None:
                    probe = service._probe_device(device, profile_names=("subtype=1",))
                    if probe.get("action_status") != "ready":
                        raise ValueError("未取得可用畫面；請檢查連線／防火牆或選擇已配對的中繼。")
                self._stage(key, "activating", "畫面已驗證，正在啟動監控…")
                result = service._activate(device)
            else:
                self._stage(key, "activating", "正在驗證中繼並啟動監控…")
                result = service._activate_remote(device)
            if result.get("action_status") not in {"connected", "starting"} and result.get("success") is not True:
                raise ValueError(result.get("message_zh_tw") or "接入未完成，請查看連線診斷。")
            # Source-switch acceptance is not proof of sustained frames.
            self._stage(key, "waiting_frame", "正在等待監控管線的新影格…")
            while not service._active_frame_ready():
                self._stage(key, "waiting_frame", "正在等待監控管線的新影格…")
                time.sleep(.25)
            modes = service._state.get("connection_modes", {})
            modes[device] = mode
            service._save(connection_modes=modes)
            with self.lock:
                self.jobs[key].update(status="completed", stage="connected", success=True,
                                      message_zh_tw="已接入監控，正在接收新的攝影機畫面。")
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "接入逾時或連線失敗；請檢查來源與診斷。"
            with self.lock:
                self.jobs[key].update(status="failed", stage="failed", message_zh_tw=message)
        finally:
            if acquired:
                service._camera_control_lock.release()
            with self.lock:
                self.active = None

    def close(self) -> None:
        with self.lock:
            self.closed = True
