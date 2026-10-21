from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meowkeeper.integrations.go2rtc.config import build_yaml
from meowkeeper.integrations.go2rtc.lan_access import WindowsRtspFirewallManager
from meowkeeper.integrations.go2rtc.models import Go2RtcConfig
from meowkeeper.integrations.go2rtc.process import Go2RtcProcess


class Go2RtcLanAccessTests(unittest.TestCase):
    def test_default_config_remains_loopback_and_api_cannot_be_wildcard(self) -> None:
        config = Go2RtcConfig()
        self.assertEqual(config.api_host, "127.0.0.1")
        self.assertEqual(config.rtsp_host, "127.0.0.1")
        with self.assertRaises(ValueError):
            Go2RtcConfig(api_host="0.0.0.0")

    def test_explicit_lan_config_only_wildcards_rtsp(self) -> None:
        yaml = build_yaml(Go2RtcConfig(rtsp_host="0.0.0.0"))
        self.assertIn("listen: 127.0.0.1:1984", yaml)
        self.assertIn("listen: 0.0.0.0:8554", yaml)

    def test_lan_rtsp_requires_firewall_ready_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-lan-rtsp-") as directory:
            process = Go2RtcProcess(directory, api_port=21984, rtsp_port=28554, rtsp_host="0.0.0.0")
            with patch.object(process, "install") as install:
                install.return_value = Mock(installed=True)
                status = process.start()
            self.assertFalse(status.healthy)
            self.assertIn("防火牆", status.message_zh_tw)

    def test_firewall_command_is_private_tcp_local_subnet_and_program_scoped(self) -> None:
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory(prefix="meowkeeper-lan-firewall-") as directory:
            executable = Path(directory) / "go2rtc.exe"

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                if "show" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"Rule Name: MeowKeeper+ RTSP LAN\nEnabled: Yes\nProfiles: Private\nLocalPort: 8554\nRemoteIP: LocalSubnet\nProgram: {executable.resolve()}\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            manager = WindowsRtspFirewallManager(runner=runner)
            with patch("meowkeeper.integrations.go2rtc.lan_access.os.name", "nt"):
                result = manager.enable(port=8554, executable=executable)
            self.assertTrue(result["enabled"])
            add_command = calls[0]
            self.assertIn("profile=Private", add_command)
            self.assertIn("remoteip=localsubnet", add_command)
            self.assertIn("protocol=TCP", add_command)
            self.assertIn("localport=8554", add_command)
            self.assertFalse(any("1984" in value or "Public" in value or "Any" in value for value in add_command))


if __name__ == "__main__":
    unittest.main()
