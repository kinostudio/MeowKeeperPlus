"""Explicit, local-only UI entry to the bundled CPU sensor setup wizard."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path


class CpuTemperatureSetup:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._state = "idle"

    @staticmethod
    def _interactive_windows() -> bool:
        if os.name != "nt":
            return False
        session = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)) and session.value)

    def _verified_script(self) -> Path:
        try:
            manifest = json.loads((self.root / "build_manifest.json").read_text(encoding="utf-8-sig"))
            hashes = {item["path"]: str(item["sha256"]).lower() for item in manifest["files"]}
            for name in ("scripts/Manage-CpuTemperature.ps1", "scripts/Manage-CpuSensorTask.ps1"):
                path = self.root / name
                if path.resolve() != path.absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
                    raise ValueError
            return self.root / "scripts/Manage-CpuTemperature.ps1"
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("測溫啟用工具缺少或完整性未通過；請使用完整正式發布包，未開啟安裝。") from None

    def status(self) -> dict:
        available = False
        reason = ""
        try:
            self._verified_script()
            available = self._interactive_windows()
            if not available:
                reason = "目前 Host 不是 Windows 互動登入工作階段；請在監控主機上雙擊 Enable-CPU-Temperature.cmd。"
        except ValueError as error:
            reason = str(error)
        with self._lock:
            state = self._state
        return {"state": state, "launcher_available": available, "reason_zh_tw": reason}

    def launch(self) -> dict:
        script = self._verified_script()
        if not self._interactive_windows():
            raise ValueError("無法在背景服務中顯示授權視窗；請在監控主機上雙擊 Enable-CPU-Temperature.cmd。")
        with self._lock:
            if self._state in {"running", "timeout"}:
                return {"state": "running", "message_zh_tw": "啟用視窗已開啟，請在監控主機完成操作，勿重複點擊。"}
            self._state = "running"
        threading.Thread(target=self._run, args=(script,), daemon=True, name="cpu-setup-wizard").start()
        return {"state": "running", "message_zh_tw": "請在監控主機確認啟用提示與 Windows 管理員授權；尚未確認測溫成功。"}

    def _run(self, script: Path) -> None:
        state = "failed"
        try:
            powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                 "-File", str(script), "-Mode", "Install"],
                cwd=self.root, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=420, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
            state = {0: "wizard_finished", 1223: "cancelled", 3010: "restart_required"}.get(result.returncode, "failed")
        except subprocess.TimeoutExpired:
            state = "timeout"
        except (OSError, KeyError):
            pass
        with self._lock:
            self._state = state
