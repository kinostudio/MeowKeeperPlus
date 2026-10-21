from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CpuTemperatureInstallTests(unittest.TestCase):
    def test_installer_consent_survives_elevation_and_failure_is_optional(self):
        source = (ROOT / "scripts/Install-MeowKeeperPlus.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[switch]$EnableCpuTemperature", source)
        self.assertIn("$arguments += '-EnableCpuTemperature'", source)
        self.assertIn("$arguments += '-SkipCpuTemperature'", source)
        self.assertIn("-Mode Install -Consent", source)
        self.assertIn("仍繼續安裝監控", source)
        tool = (ROOT / "scripts/Manage-CpuTemperature.ps1").read_text(encoding="utf-8-sig")
        self.assertLess(tool.index("if ($Mode -eq 'Diagnose')"), tool.index("if (-not $Consent)"))
        self.assertLess(tool.index("Get-FileHash"), tool.index("Start-Process -FilePath $installer"))
        self.assertLess(tool.index("Get-AuthenticodeSignature"), tool.index("Start-Process -FilePath $installer"))
        self.assertIn("WaitForExit(300000)", tool)
        self.assertIn("@(0, 3010)", tool)
        self.assertNotIn("-ArgumentList '-install -silent'", tool)
        self.assertNotIn("Set-Acl", tool)
        self.assertNotIn("Restart-Computer", tool)

    @unittest.skipUnless(os.name == "nt", "需要 Windows PowerShell")
    def test_real_windows_read_only_diagnose_and_powershell_syntax(self):
        executable = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        tool = ROOT / "scripts/Manage-CpuTemperature.ps1"
        environment = os.environ.copy()
        environment.pop("PSModulePath", None)
        command = "$errors=$null; $tokens=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('" + str(tool).replace("'", "''") + "',[ref]$tokens,[ref]$errors); if($errors.Count){exit 1}"
        parsed = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, timeout=30, shell=False, env=environment, check=False)
        self.assertEqual(parsed.returncode, 0)
        result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(tool), "-Mode", "Diagnose"],
                                capture_output=True, timeout=30, shell=False, env=environment, check=False)
        self.assertEqual(result.returncode, 0, "CPU 驅動唯讀診斷未成功")
        self.assertFalse(json.loads(result.stdout.decode("utf-8-sig"))["temperature_verified"])

    def test_package_contains_explicit_portable_entry_and_missing_driver_reason(self):
        source = (ROOT / "tools/build_portable.py").read_text(encoding="utf-8")
        self.assertIn('staging / "Enable-CPU-Temperature.cmd"', source)
        self.assertIn('staging / "scripts" / "Manage-CpuTemperature.ps1"', source)
        script = (ROOT / "src/meowkeeper/app/web/static/js/dashboard-live-metrics.js").read_text(encoding="utf-8")
        self.assertIn("driver_missing:", script)
        self.assertIn("缺少測溫驅動", script)
