"""Guard the SYSTEM upgrade path against a leftover development Tray."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "Install-MeowKeeperPlus.ps1"


class LegacyTrayUpgradeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_upgrade_filter_preserves_unrelated_ffmpeg(self) -> None:
        # Load only the filter function; mocked inventory/stop never touch OS processes.
        command = r'''
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile('__INSTALLER__', [ref]$tokens, [ref]$errors)
        $fn = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Stop-InstalledFfmpegForUpgrade'}, $true)
        Invoke-Expression $fn.Extent.Text
        $script:owned = $true
        $script:stopped = @()
        function Get-CimInstance {
            if ($script:owned) { [pscustomobject]@{ProcessId=1; ExecutablePath='C:\TestApp\vendor\ffmpeg\bin\ffmpeg.exe'; CommandLine=''} }
            [pscustomobject]@{ProcessId=2; ExecutablePath='C:\OtherProject\ffmpeg.exe'; CommandLine=''}
        }
        function Stop-VerifiedMediaProcess($Snapshot,$ExpectedName,$ExpectedExecutablePath) {
            if ($ExpectedExecutablePath -ne 'C:\TestApp\vendor\ffmpeg\bin\ffmpeg.exe') { throw 'wrong path' }
            $script:stopped += $Snapshot.ProcessId
            $script:owned = $false
        }
        function Start-Sleep {}
        Stop-InstalledFfmpegForUpgrade 'C:\TestApp'
        if ($script:stopped.Count -ne 1 -or $script:stopped[0] -ne 1) { throw 'wrong process selected' }
        Write-Output 'owned_only_passed'
        '''.replace("__INSTALLER__", str(INSTALLER).replace("'", "''"))
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("owned_only_passed", result.stdout)

    def test_verified_repository_tray_is_stopped_before_tasks(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8-sig")
        self.assertIn("function Stop-VerifiedRepositoryTray", text)
        self.assertIn("Stop-VerifiedRepositoryTray $packageRoot", text)
        self.assertLess(
            text.index("Stop-VerifiedRepositoryTray $packageRoot"),
            text.index('schtasks.exe" /End /TN $task'),
        )
        self.assertIn("$_.CommandLine.Contains($expectedScript)", text)
        self.assertIn("$process.ExecutablePath, $expectedExe", text)
        self.assertIn("$process.CreationDate", text)
        self.assertIn("$live[0].CommandLine, $process.CommandLine", text)
        self.assertIn("Stop-Process -InputObject $handle", text)
        self.assertLess(
            text.index('schtasks.exe" /Run /TN $coreTask'),
            text.index('schtasks.exe" /Run /TN $trayTask'),
        )
        self.assertIn('if (-not $healthReady) { throw "Core 未在時限內通過健康檢查，拒絕啟動 Tray。" }', text)
        self.assertIn("Stop-CurrentUserProcesses $resolvedApp", text)
        self.assertLess(
            text.index("Stop-CurrentUserProcesses $resolvedApp"),
            text.index("Move-Item -LiteralPath $resolvedApp -Destination $upgradeBackupApp"),
        )

    def test_system_upgrade_stops_only_owned_media_before_removal(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8-sig")
        self.assertIn("function Stop-InstalledFfmpegForUpgrade([string]$ApplicationRoot)", text)
        self.assertIn('Get-CimInstance Win32_Process -Filter "Name=\'ffmpeg.exe\'"', text)
        self.assertNotIn("Stop-AllFfmpegProcessesForUpgrade", text)
        self.assertNotIn("SYSTEM 升級會停止本機所有 ffmpeg.exe", text)
        self.assertIn("function Stop-InstalledFfprobe([string]$ApplicationRoot)", text)
        self.assertIn("Stop-InstalledFfprobe $resolvedApp", text)
        self.assertIn('Stop-VerifiedMediaProcess $process "ffmpeg.exe" $expectedExe', text)
        self.assertIn("$handle.WaitForExit(15000)", text)
        self.assertIn("$handle.StartTime.ToUniversalTime()", text)
        self.assertIn("$live[0].CreationDate", text)
        self.assertLess(
            text.index("            Stop-InstalledFfmpegForUpgrade $resolvedApp\n"),
            text.index("Move-Item -LiteralPath $resolvedApp -Destination $upgradeBackupApp"),
        )
        self.assertIn("function Get-InstalledRuntimePythonProcesses", text)
        self.assertIn("multiprocessing workers", text)
        self.assertIn("視為上次升級中斷的程式目錄", text)
        self.assertIn("App.upgrade-backup-", text)
        self.assertIn("新版複製／校驗未完成，已恢復舊 App", text)

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_runtime_worker_filter_is_scoped_to_installed_python_paths(self) -> None:
        command = r'''
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile('__INSTALLER__', [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw ($errors | Out-String) }
        $fn = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Get-InstalledRuntimePythonProcesses'}, $true)
        Invoke-Expression $fn.Extent.Text
        $now = [datetime]::UtcNow
        $inventory = @(
            [pscustomobject]@{ProcessId=11; ExecutablePath='C:\TestApp\runtime\python\python.exe'; CommandLine='"C:\TestApp\runtime\python\python.exe" -c "from multiprocessing.spawn import spawn_main"'; CreationDate=$now},
            [pscustomobject]@{ProcessId=12; ExecutablePath='C:\TestApp\runtime\python\python.exe'; CommandLine='"C:\TestApp\runtime\python\python.exe" "C:\TestApp\tools\run_host.py"'; CreationDate=$now.AddSeconds(-1)},
            [pscustomobject]@{ProcessId=13; ExecutablePath='C:\TestApp\runtime\python\pythonw.exe'; CommandLine='"C:\TestApp\runtime\python\pythonw.exe" "C:\TestApp\tools\run_tray.py"'; CreationDate=$now.AddSeconds(-2)},
            [pscustomobject]@{ProcessId=99; ExecutablePath='C:\Python\python.exe'; CommandLine='python another_app.py'; CreationDate=$now}
        )
        $owned = @(Get-InstalledRuntimePythonProcesses 'C:\TestApp' $inventory)
        if ($owned.Count -ne 3 -or @($owned | Where-Object { $_.ProcessId -eq 99 }).Count -ne 0) { throw 'runtime path filter selected wrong processes' }
        if ($owned[0].ProcessId -ne 11) { throw 'AI worker must be stopped before host' }
        Write-Output 'runtime_worker_filter_passed'
        '''.replace("__INSTALLER__", str(INSTALLER).replace("'", "''"))
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime_worker_filter_passed", result.stdout)

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_upgrade_tolerates_process_exit_after_snapshot_but_fails_closed_if_pid_remains(self) -> None:
        command = r'''
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile('__INSTALLER__', [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw ($errors | Out-String) }
        foreach ($name in @('Get-ProcessHandleById','Get-ProcessHandleOrNullIfExited')) {
            $fn = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name}, $true)
            if (-not $fn) { throw "missing function: $name" }
            Invoke-Expression $fn.Extent.Text
        }
        $script:snapshotPid = 2147483646
        $script:pidPresent = $false
        $script:inventoryFails = $false
        function Get-CimInstance {
            [CmdletBinding()]
            param([string]$ClassName,[string]$Filter)
            if ($script:inventoryFails) { throw 'process inventory unavailable' }
            if ($script:pidPresent) { return [pscustomobject]@{ProcessId=$script:snapshotPid} }
        }

        # Use a real .NET missing PID so Windows PowerShell exercises its
        # MethodInvocationException wrapper, not a mocked direct exception.
        if ($null -ne (Get-ProcessHandleOrNullIfExited $script:snapshotPid)) { throw 'exited process was not treated as gone' }
        $failedClosed = $false
        $script:pidPresent = $true
        try { $null = Get-ProcessHandleOrNullIfExited $script:snapshotPid } catch { $failedClosed = $true }
        if (-not $failedClosed) { throw 'reused or still-present PID was not rejected' }
        $failedClosed = $false
        $script:pidPresent = $false
        $script:inventoryFails = $true
        try { $null = Get-ProcessHandleOrNullIfExited $script:snapshotPid } catch { $failedClosed = $true }
        if (-not $failedClosed) { throw 'unavailable process inventory was not rejected' }
        Write-Output 'process_snapshot_race_passed'
        '''.replace("__INSTALLER__", str(INSTALLER).replace("'", "''"))
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("process_snapshot_race_passed", result.stdout)

    def test_all_upgrade_process_handle_lookups_use_race_safe_helper(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8-sig")
        self.assertEqual(text.count("[Diagnostics.Process]::GetProcessById("), 1)
        self.assertIn("function Get-ProcessHandleOrNullIfExited([int]$ProcessId)", text)
        self.assertGreaterEqual(text.count("Get-ProcessHandleOrNullIfExited "), 5)


if __name__ == "__main__":
    unittest.main()
