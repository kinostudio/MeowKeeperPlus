"""呼叫官方 Tailscale CLI；預設唯讀，變更命令必須明確確認。"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .models import TailscaleStatus
from .status import parse_status_json, safe_version

CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _default_runner(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        # Tailscale emits JSON as UTF-8.  Windows PowerShell/System
        # processes may use cp950 as their active code page; allowing
        # subprocess to choose that code page can fail its reader thread and
        # leave ``stdout`` as None, which used to surface as ``TypeError`` in
        # ``json.loads``.  Decode the CLI contract explicitly and replace
        # unexpected bytes so status remains a safe diagnostic.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


class TailscaleClient:
    OFFICIAL_INSTALLER = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"

    def __init__(self, *, executable: str | Path | None = None, runner: CommandRunner | None = None, timeout: float = 5.0) -> None:
        self._executable_explicit = executable is not None
        self.executable = str(executable) if executable else self.find_executable()
        self.runner = runner or _default_runner
        self.timeout = timeout
        self._last_run_error: str | None = None

    @staticmethod
    def find_executable() -> str | None:
        candidates = [
            shutil.which("tailscale"),
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        ]
        return next((item for item in candidates if item and Path(item).is_file()), None)

    def _resolve_executable(self) -> str | None:
        """Refresh a previous miss so installing Tailscale does not require a Host restart."""

        if self._executable_explicit:
            return self.executable
        if self.executable and Path(self.executable).is_file():
            return self.executable
        self.executable = self.find_executable()
        return self.executable

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str] | None:
        executable = self._resolve_executable()
        if not executable:
            return None
        self._last_run_error = None
        try:
            return self.runner([executable, *arguments], self.timeout)
        except PermissionError:
            self._last_run_error = "permission_denied"
            return None
        except subprocess.TimeoutExpired:
            self._last_run_error = "timeout"
            return None
        except (OSError, subprocess.SubprocessError, TimeoutError):
            self._last_run_error = "execution_error"
            return None

    def status(self) -> TailscaleStatus:
        if not self._resolve_executable():
            return TailscaleStatus(status="not_installed", message_zh_tw="尚未找到官方 Tailscale CLI。")
        completed = self._run("status", "--json")
        if completed is None:
            if self._last_run_error == "permission_denied":
                return TailscaleStatus(
                    installed=True,
                    status="permission_denied",
                    message_zh_tw="已找到官方 Tailscale CLI，但目前 Windows 執行身分無法執行狀態檢查。請確認 Host 的執行身分／Tailscale 服務權限；MeowKeeper+ 不會自行提升權限。",
                )
            if self._last_run_error == "timeout":
                return TailscaleStatus(
                    installed=True,
                    status="timeout",
                    message_zh_tw="已找到官方 Tailscale CLI，但狀態檢查逾時；請確認 Tailscale 服務回應正常。",
                )
            return TailscaleStatus(installed=True, status="error", message_zh_tw="已找到 Tailscale CLI，但無法執行狀態檢查。")
        if completed.returncode != 0:
            error_text = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
            if "access is denied" in error_text or "permission denied" in error_text:
                return TailscaleStatus(
                    installed=True,
                    status="permission_denied",
                    message_zh_tw="已找到官方 Tailscale CLI，但目前 Windows 執行身分無權讀取 Tailscale 服務狀態。請確認 Host 的執行身分／Tailscale 服務權限；MeowKeeper+ 不會自行提升權限。",
                )
            return TailscaleStatus(installed=True, status="not_logged_in", message_zh_tw="已找到 Tailscale CLI，但尚未登入或服務尚未就緒。")
        return parse_status_json(completed.stdout)

    def version(self) -> str | None:
        completed = self._run("version")
        return safe_version(completed.stdout) if completed and completed.returncode == 0 else None

    def serve_status(self) -> dict[str, Any]:
        if not self._resolve_executable():
            return {"status": "not_installed", "enabled": False}
        completed = self._run("serve", "status", "--json")
        if completed is None or completed.returncode != 0:
            return {"status": "not_checked", "enabled": False, "message_zh_tw": "尚未取得 Serve 狀態。"}
        try:
            payload = json.loads(completed.stdout)
            return payload if isinstance(payload, dict) else {"status": "unknown", "enabled": False}
        except json.JSONDecodeError:
            return {"status": "error", "enabled": False}

    def build_serve_plan(self, *, enable: bool, confirm: bool = False) -> dict[str, Any]:
        current = self.serve_status()
        if not confirm:
            return {
                "status": "confirmation_required",
                "current": current,
                "command": "tailscale serve --bg http://127.0.0.1:8000" if enable else "tailscale serve reset",
                "message_zh_tw": "請先確認只將 Tailnet HTTPS 轉送至 localhost Dashboard。",
            }
        if enable:
            return {
                "status": "ready_to_apply",
                "current": current,
                "command": ["tailscale", "serve", "--bg", "http://127.0.0.1:8000"],
                "funnel": False,
            }
        return {"status": "ready_to_apply", "current": current, "command": ["tailscale", "serve", "reset"], "funnel": False}

    def apply_serve(self, *, enable: bool, confirm: bool = False) -> dict[str, Any]:
        """明確確認後才執行 Serve；不接受 Funnel 或任意命令。"""

        plan = self.build_serve_plan(enable=enable, confirm=confirm)
        if plan["status"] != "ready_to_apply":
            return plan
        arguments = ("serve", "--bg", "http://127.0.0.1:8000") if enable else ("serve", "reset")
        completed = self._run(*arguments)
        if completed is None or completed.returncode != 0:
            return {**plan, "status": "failed", "message_zh_tw": "Serve 變更失敗，本機 Dashboard 不受影響。"}
        return {**plan, "status": "applied"}
