"""Explicit, narrow Windows firewall boundary for LAN RTSP access.

This module never opens the go2rtc API port and never creates a Public/WAN
rule.  The rule is limited to the installed go2rtc executable, TCP 8554,
Windows Private profile, and the local-subnet address set.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

RTSP_LAN_FIREWALL_RULE = "MeowKeeper+ RTSP LAN"
PRIVATE_PROFILE = "Private"
LOCAL_SUBNET = "localsubnet"


class WindowsRtspFirewallManager:
    """Manage only the approved Private/local-subnet RTSP rule."""

    def __init__(self, *, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self._runner = runner or subprocess.run

    @staticmethod
    def _validate(port: int, executable: str | Path) -> tuple[int, str]:
        value = int(port)
        if not 1 <= value <= 65535:
            raise ValueError("RTSP 防火牆 Port 必須介於 1 到 65535。")
        path = str(Path(executable).resolve())
        if not path.lower().endswith("go2rtc.exe"):
            raise ValueError("RTSP 防火牆只能綁定已驗證的 go2rtc.exe。")
        return value, path

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(
            ["netsh", "advfirewall", "firewall", *arguments],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

    @staticmethod
    def _permission_denied(output: str) -> bool:
        lowered = output.lower()
        return "access is denied" in lowered or "拒絕存取" in lowered or "需要提升" in lowered

    def status(self, *, port: int = 8554, executable: str | Path) -> dict[str, Any]:
        value, path = self._validate(port, executable)
        base = {
            "rule_name": RTSP_LAN_FIREWALL_RULE,
            "profile": PRIVATE_PROFILE,
            "remote_scope": LOCAL_SUBNET,
            "port": value,
            "program": path,
        }
        if os.name != "nt":
            return {**base, "status": "not_supported", "enabled": False, "message_zh_tw": "目前不是 Windows，未執行 RTSP 防火牆查詢。"}
        try:
            completed = self._run(["show", "rule", f"name={RTSP_LAN_FIREWALL_RULE}"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**base, "status": "not_checked", "enabled": False, "message_zh_tw": f"RTSP 防火牆狀態尚未確認：{type(exc).__name__}"}
        output = (completed.stdout or "") + (completed.stderr or "")
        if completed.returncode != 0:
            return {
                **base,
                "status": "permission_denied" if self._permission_denied(output) else "not_configured",
                "enabled": False,
                "message_zh_tw": "尚未建立僅限 Private／區網的 RTSP 防火牆規則。",
            }
        lowered = output.lower()
        safe = (
            "profiles: private" in lowered
            and "localport: " + str(value) in lowered
            and "remoteip: localsubnet" in lowered
            and path.lower() in lowered
            and "enabled: yes" in lowered
        )
        return {
            **base,
            "status": "enabled" if safe else "not_verified",
            "enabled": safe,
            "message_zh_tw": "已確認僅限 Private／區網的 RTSP 防火牆規則。" if safe else "找到同名規則，但未確認為安全的 Private／區網 RTSP 規則。",
        }

    def enable(self, *, port: int = 8554, executable: str | Path) -> dict[str, Any]:
        value, path = self._validate(port, executable)
        if os.name != "nt":
            return self.status(port=value, executable=path)
        try:
            completed = self._run([
                "add", "rule", f"name={RTSP_LAN_FIREWALL_RULE}",
                "dir=in", "action=allow", "protocol=TCP", f"localport={value}",
                f"profile={PRIVATE_PROFILE}", f"remoteip={LOCAL_SUBNET}",
                f"program={path}", "enable=yes",
            ])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {**self.status(port=value, executable=path), "status": "not_checked", "enabled": False, "message_zh_tw": f"RTSP 防火牆規則未變更：{type(exc).__name__}"}
        if completed.returncode != 0:
            output = (completed.stdout or "") + (completed.stderr or "")
            return {
                **self.status(port=value, executable=path),
                "status": "permission_denied" if self._permission_denied(output) else "error",
                "enabled": False,
                "message_zh_tw": "無法建立 RTSP 防火牆規則；請以系統管理員權限操作。",
            }
        return self.status(port=value, executable=path)


__all__ = ["LOCAL_SUBNET", "PRIVATE_PROFILE", "RTSP_LAN_FIREWALL_RULE", "WindowsRtspFirewallManager"]
