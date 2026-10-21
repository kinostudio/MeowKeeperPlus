"""Explicit Windows elevation for a program/peer/local-address restricted rule."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path

from .relay_provider import tail_ip


class RelayFirewall:
    def __init__(self, root: Path, network):
        self.script = Path(root) / "scripts/Manage-XiaomiRelayFirewall.ps1"
        self.network = network

    def arguments(self, peer: str, mode: str) -> list[str]:
        address = tail_ip(self.network().get("tailscale_ip"))
        return ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.script),
                "-Mode", mode, "-Program", str(Path(sys.executable).resolve()), "-Peer", tail_ip(peer), "-LocalAddress", address]

    def verified(self, peer: str) -> bool:
        if os.name != "nt" or not self.script.is_file():
            return False
        try:
            result = subprocess.run(["powershell.exe", *self.arguments(peer, "Inspect")], capture_output=True,
                                    timeout=15, shell=False, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return result.returncode == 0 and json.loads(result.stdout).get("verified") is True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def authorize(self, peer: str) -> dict:
        args = self.arguments(peer, "Install")
        if self.verified(peer):
            return {"success": True, "message_zh_tw": "指定程式、Tailscale 位址與對端的規則已確認。"}
        if os.name != "nt" or not self.script.is_file():
            raise ValueError("此環境無法建立 Windows 防火牆規則。")
        session_id = ctypes.c_uint()
        interactive = bool(ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id))) and session_id.value != 0
        if not interactive or os.getenv("USERNAME", "").lower() == "system":
            return {"success": False, "administrator_command": "powershell.exe " + subprocess.list2cmdline(args),
                    "message_zh_tw": "服務工作階段無法顯示 UAC；請在此電腦的管理員 PowerShell 執行下方命令後再啟用中繼。"}
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", subprocess.list2cmdline(args), None, 0)
        return {"success": False, "authorization_pending": result > 32,
                "message_zh_tw": "請核准 Windows UAC，完成後再啟用中繼。" if result > 32 else "Windows 未接受授權；規則尚未確認。"}
