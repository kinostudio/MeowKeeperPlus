"""Installed builds expose a safe, clickable restart entry after full close."""
from __future__ import annotations

import base64
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstalledGuiLauncherTests(unittest.TestCase):
    def test_migration_journal_survives_until_outer_rollback_and_failure_receipt_accepts_phase(self):
        installer = (ROOT / "scripts/Install-MeowKeeperPlus.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("Remove-Item -LiteralPath $tempRoot -Recurse -Force", installer)
        self.assertGreater(installer.index("Remove-Item -LiteralPath $resolvedTemp"), installer.index("--mode rollback"))
        self.assertIn("$installationPhase -eq 'complete'", installer)
        self.assertIn("'archive', 'data_migration', 'copy'", installer)

    def test_release_has_clickable_safe_relaunch_entry(self) -> None:
        installer = (ROOT / "scripts" / "Install-MeowKeeperPlus.ps1").read_text(encoding="utf-8-sig")
        launcher_path = ROOT / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1"
        launcher = launcher_path.read_text(encoding="utf-8-sig")
        uninstaller = (ROOT / "scripts" / "Uninstall-MeowKeeperPlus.ps1").read_text(encoding="utf-8-sig")
        package_builder = (ROOT / "tools" / "build_portable.py").read_text(encoding="utf-8")

        self.assertIn('[Environment]::GetFolderPath("Programs")', installer)
        self.assertIn('[Environment]::GetFolderPath("CommonPrograms")', installer)
        self.assertIn('New-StartMenuLauncherShortcut $startMenuShortcut $appRoot', installer)
        self.assertIn('start_menu_shortcut = $startMenuShortcut', installer)
        self.assertIn("Launch-Installed-MeowKeeperPlus.ps1", installer)
        self.assertIn("migrate_legacy_app_data.py", installer)
        self.assertIn('"--mode", "move"', installer)
        self.assertIn("--mode rollback", installer)
        self.assertIn("Assert-OwnedStartMenuShortcut $startMenuShortcut $appRoot", uninstaller)
        self.assertIn("Remove-Item -LiteralPath $startMenuShortcut -Force", uninstaller)

        self.assertIn("Assert-SystemTaskTarget 'MeowKeeperPlus Core' 'core'", launcher)
        self.assertIn("Assert-SystemTaskTarget 'MeowKeeperPlus Tray' 'tray'", launcher)
        self.assertIn("Get-SystemTaskDataRoot $appRoot $installRoot", launcher)
        self.assertIn("--event-directory\\s+", launcher)
        self.assertIn("Join-Path $AppRoot 'data\\events'", launcher)
        self.assertIn("MeowKeeperPlus User Core.cmd", launcher)
        self.assertIn("MeowKeeperPlus User Tray.cmd", launcher)
        self.assertIn("/healthz", launcher)
        self.assertIn("Start-Process $baseUrl", launcher)
        self.assertNotIn("-Verb RunAs", launcher)
        self.assertNotIn("/Create", launcher)

        self.assertIn('ROOT / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1"', package_builder)
        self.assertIn('staging / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1"', package_builder)

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell 語法解析器")
    def test_installer_launcher_and_uninstaller_parse_in_windows_powershell(self) -> None:
        paths = [
            ROOT / "scripts" / "Install-MeowKeeperPlus.ps1",
            ROOT / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1",
            ROOT / "scripts" / "Uninstall-MeowKeeperPlus.ps1",
        ]
        for path in paths:
            quoted_path = str(path).replace("'", "''")
            command = (
                "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
                f"$source=[IO.File]::ReadAllText('{quoted_path}',[Text.Encoding]::UTF8); "
                "[void][System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors); "
                "if ($errors.Count) { throw ($errors[0].Message + ' at line ' + $errors[0].Extent.StartLineNumber) }"
            )
            encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                [shutil.which("powershell.exe"), "-NoProfile", "-EncodedCommand", encoded_command],
                capture_output=True,
                text=True,
                timeout=20,
                shell=False,
                check=False,
            )
            self.assertEqual(result.returncode, 0, f"{path}\n{result.stderr}{result.stdout}")


if __name__ == "__main__":
    unittest.main()
