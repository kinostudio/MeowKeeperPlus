"""Xiaomi UDP firewall diagnostics must stay read-only inside the Host."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService

ROOT = Path(__file__).resolve().parents[1]


class XiaomiFirewallDiagnosticTests(unittest.TestCase):
    def test_dashboard_displays_read_only_diagnostic_instead_of_discarding_payload(self) -> None:
        template = (ROOT / "src" / "meowkeeper" / "app" / "web" / "templates" / "xiaomi_setup.html").read_text(encoding="utf-8")
        script = (ROOT / "src" / "meowkeeper" / "app" / "web" / "static" / "js" / "xiaomi-wizard.js").read_text(encoding="utf-8")
        self.assertIn('id="xiaomi-firewall-result-panel"', template)
        self.assertIn('id="xiaomi-firewall-result-summary"', template)
        self.assertIn('id="xiaomi-firewall-result"', template)
        self.assertIn("name==='firewall-diagnostic'", script)
        self.assertIn("panel.hidden=false;panel.open=true", script)
        self.assertIn("parent.open=true", script)
        self.assertIn("textContent=JSON.stringify(data,null,2)", script)
        self.assertNotIn("xiaomi-firewall-result').innerHTML", script)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell rule policy")
    def test_public_block_does_not_confuse_private_allow_or_tcp(self) -> None:
        path = ROOT / "scripts" / "Manage-XiaomiUdpFirewall.ps1"
        command = f"""
        $ast=[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$null)
        $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-UdpBlockForProfile'}},$true)
        . ([scriptblock]::Create($fn.Extent.Text))
        $rule=[pscustomobject]@{{Direction='Inbound';Enabled='True';Action='Block';Profile='Public'}}
        if(-not (Test-UdpBlockForProfile $rule 'Public' 'UDP')){{exit 1}}
        if(Test-UdpBlockForProfile $rule 'Private' 'UDP'){{exit 2}}
        if(Test-UdpBlockForProfile $rule 'Public' 'TCP'){{exit 3}}
        $rule.Action='Allow'
        if(Test-UdpBlockForProfile $rule 'Public' 'UDP'){{exit 4}}
        $rule.Action='Block';$rule.Profile='Any'
        if(-not (Test-UdpBlockForProfile $rule 'Private' 'UDP')){{exit 5}}
        $rule.Enabled='False'
        if(Test-UdpBlockForProfile $rule 'Public' 'UDP'){{exit 6}}
        """
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                shell=False, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell parser")
    def test_powershell_scripts_parse_and_use_utf8_bom(self) -> None:
        for name in ("Manage-XiaomiUdpFirewall.ps1", "Xiaomi-Firewall-Wizard.ps1"):
            path = ROOT / "scripts" / name
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            command = (
                "$tokens=$null;$errors=$null;"
                f"[System.Management.Automation.Language.Parser]::ParseFile('{path}',"
                "[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count){exit 1}"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                shell=False, capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(result.returncode, 0, name)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell rule filter")
    def test_existing_rule_requires_one_exact_owned_object(self) -> None:
        path = ROOT / "scripts" / "Manage-XiaomiUdpFirewall.ps1"
        command = f"""
        $ast=[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$null)
        $fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-ExactRule'}},$true)
        . ([scriptblock]::Create($fn.Extent.Text))
        function Get-NetFirewallApplicationFilter {{ param($AssociatedNetFirewallRule) [pscustomobject]@{{Program='C:\\safe\\go2rtc.exe'}} }}
        function Get-NetFirewallAddressFilter {{ param($AssociatedNetFirewallRule) [pscustomobject]@{{LocalAddress='192.168.18.3';RemoteAddress='192.168.18.62'}} }}
        function Get-NetFirewallPortFilter {{ param($AssociatedNetFirewallRule) [pscustomobject]@{{Protocol='UDP';LocalPort='Any';RemotePort='Any'}} }}
        $marker='MeowKeeperPlus.XiaomiUdp.v1';$script:networkProfile='Public';$hash='A'*64
        $rule=[pscustomobject]@{{Group=$marker;Description="$marker;camera=192.168.18.62;binary_sha256=$hash;local=192.168.18.3;profile=Public";Direction='Inbound';Action='Allow';Enabled='True';Profile='Public'}}
        if(-not (Get-ExactRule @($rule) 'C:\\safe\\go2rtc.exe' '192.168.18.3' '192.168.18.62' $hash)){{exit 1}}
        if(Get-ExactRule @($rule,$rule) 'C:\\safe\\go2rtc.exe' '192.168.18.3' '192.168.18.62' $hash){{exit 2}}
        $rule.Profile='Any'
        if(Get-ExactRule @($rule) 'C:\\safe\\go2rtc.exe' '192.168.18.3' '192.168.18.62' $hash){{exit 3}}
        """
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            shell=False, capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_device_never_runs_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = XiaomiSetupService(
                temporary, go2rtc=SimpleNamespace(api_port=1984, rtsp_port=8554)
            )
            with patch("meowkeeper.integrations.xiaomi.setup_service.subprocess.run") as run:
                result = service.firewall_diagnostic("missing")
            self.assertEqual(result["status"], "not_found")
            run.assert_not_called()

    def test_native_power_read_is_bounded_read_only_and_failure_stays_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = XiaomiSetupService(temporary, go2rtc=SimpleNamespace(api_port=1984, rtsp_port=8554))
            reader = Mock(return_value={"success": True, "on": True})
            service.miot_power = SimpleNamespace(request=reader)
            self.assertEqual(service._camera_power_diagnostic("private-source"),
                             {"status": "verified", "on": True, "state": "on"})
            reader.assert_called_once_with("private-source", timeout_seconds=20)
            for response in ({"success": False, "on": False}, {"success": True, "on": 0}, {}):
                reader.return_value = response
                self.assertEqual(service._camera_power_diagnostic("private-source"),
                                 {"status": "unverified", "on": None, "state": "unknown"})
            reader.side_effect = TimeoutError("private-source must not leak")
            self.assertEqual(service._camera_power_diagnostic("private-source")["status"], "unverified")

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell diagnostic")
    def test_only_camera_ip_and_stream_alias_reach_read_only_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            (root / "scripts" / "Manage-XiaomiUdpFirewall.ps1").write_text("# test only", encoding="utf-8")
            service = XiaomiSetupService(
                root, go2rtc=SimpleNamespace(api_port=21984, rtsp_port=28554)
            )
            service._device_records["camera_a"] = {
                "source_url": "xiaomi://secret-account:sg@192.168.18.62?did=secret-device",
                "stream_name": "camera_a",
            }
            power_reader = Mock(return_value={"success": True, "on": False})
            service.miot_power = SimpleNamespace(request=power_reader)
            completed = SimpleNamespace(
                stdout=json.dumps({"status": "passed", "route": "physical_lan", "rule_status": "absent"}),
                returncode=0,
            )
            with patch("meowkeeper.integrations.xiaomi.setup_service.subprocess.run", return_value=completed) as run:
                result = service.firewall_diagnostic("camera_a")
            command = run.call_args.args[0]
            self.assertEqual(command[command.index("-Mode") + 1], "Diagnose")
            self.assertEqual(command[command.index("-CameraIp") + 1], "192.168.18.62")
            self.assertEqual(command[command.index("-StreamName") + 1], "camera_a")
            self.assertNotIn("secret-account", " ".join(command))
            self.assertNotIn("secret-device", " ".join(command))
            self.assertEqual(result["rule_status"], "absent")
            power_reader.assert_called_once_with(
                "xiaomi://secret-account:sg@192.168.18.62?did=secret-device", timeout_seconds=20
            )
            self.assertEqual(result["camera_power"], {"status": "verified", "on": False, "state": "standby"})
            self.assertEqual(result["status"], "attention_required")
            self.assertEqual(result["next_action"], "confirm_camera_power_on")
            self.assertIn("隱私待機", result["message_zh_tw"])
            self.assertNotIn("secret-account", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
