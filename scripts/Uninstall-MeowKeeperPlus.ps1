param([switch]$RemoveData, [switch]$WhatIf, [switch]$CurrentUser)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$installRoot = if ($CurrentUser) {
    Join-Path $env:LOCALAPPDATA "MeowKeeperPlus"
} else {
    Join-Path $env:ProgramData "MeowKeeperPlus"
}
$appRoot = Join-Path $installRoot "App"
$dataRoot = Join-Path $installRoot "Data"
$tasks = if ($CurrentUser) {
    @("MeowKeeperPlus User Core", "MeowKeeperPlus User Tray")
} else {
    @("MeowKeeperPlus Core", "MeowKeeperPlus Tray")
}
$startMenuPrograms = if ($CurrentUser) {
    [Environment]::GetFolderPath("Programs")
} else {
    [Environment]::GetFolderPath("CommonPrograms")
}
$startMenuFolder = Join-Path $startMenuPrograms "MeowKeeperPlus"
$startMenuShortcut = Join-Path $startMenuFolder "喵喵守護者+.lnk"
$startupFolder = [Environment]::GetFolderPath("Startup")
$startupLinks = @(
    (Join-Path $startupFolder "MeowKeeperPlus User Core.cmd"),
    (Join-Path $startupFolder "MeowKeeperPlus User Tray.cmd"),
    (Join-Path $startupFolder "MeowKeeperPlus User Core.lnk"),
    (Join-Path $startupFolder "MeowKeeperPlus User Tray.lnk")
)

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-ScheduledTaskExists([string]$Name) {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & "$env:SystemRoot\System32\schtasks.exe" /Query /TN $Name *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Test-OwnedStartMenuShortcut([string]$ShortcutPath, [string]$ApplicationRoot) {
    if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) { return $false }
    $expectedPowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $expectedLauncher = Join-Path $ApplicationRoot "scripts\Launch-Installed-MeowKeeperPlus.ps1"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $null
    try {
        $shortcut = $shell.CreateShortcut($ShortcutPath)
        return [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($shortcut.TargetPath), [IO.Path]::GetFullPath($expectedPowerShell)) -and
            $shortcut.Arguments.Contains(('"{0}"' -f $expectedLauncher))
    }
    finally {
        if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
}

function Assert-OwnedStartMenuShortcut([string]$ShortcutPath, [string]$ApplicationRoot) {
    if ((Test-Path -LiteralPath $ShortcutPath -PathType Leaf) -and -not (Test-OwnedStartMenuShortcut $ShortcutPath $ApplicationRoot)) {
        throw "開始功能表的同名捷徑不是此份 MeowKeeper+ 建立的；未刪除任何應用或資料：$ShortcutPath"
    }
}

function Get-CurrentUserMeowKeeperProcesses(
    [string]$ExpectedPythonw,
    [string]$QuotedHostScript,
    [string]$QuotedTrayScript
) {
    $processInventory = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    @(
        $processInventory |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                -not [string]::IsNullOrWhiteSpace($_.CommandLine) -and
                [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $ExpectedPythonw) -and
                (
                    $_.CommandLine.IndexOf($QuotedHostScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $_.CommandLine.IndexOf($QuotedTrayScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
                )
            }
    )
}

if (-not $CurrentUser -and -not $WhatIf -and -not (Test-Administrator)) {
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $PSCommandPath))
    if ($RemoveData) { $arguments += "-RemoveData" }
    try {
        $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    catch [System.ComponentModel.Win32Exception] {
        Write-Host "⚠️ 使用者已取消 UAC；未移除排程、應用或資料。"
        exit 1223
    }
}

if ($WhatIf) {
    [pscustomobject]@{
        status = "planned"
        tasks_to_remove = $tasks
        application_root = $appRoot
        data_root = $dataRoot
        remove_data = [bool]$RemoveData
        startup_links_to_remove = if ($CurrentUser) { $startupLinks } else { @() }
        start_menu_shortcut_to_remove = $startMenuShortcut
        system_changed = $false
    } | ConvertTo-Json -Depth 3
    exit 0
}

Assert-OwnedStartMenuShortcut $startMenuShortcut $appRoot

if (-not $CurrentUser) {
    $sensorManager = Join-Path $PSScriptRoot 'Manage-CpuSensorTask.ps1'
    if (Test-Path -LiteralPath $sensorManager -PathType Leaf) {
        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $sensorManager -Mode Remove
        if ($LASTEXITCODE -ne 0) { throw '測溫工作識別或移除失敗；未刪除程式檔案。' }
    }
}

if ($CurrentUser) {
    $expectedPythonw = [IO.Path]::GetFullPath((Join-Path $appRoot "runtime\python\pythonw.exe"))
    $quotedHostScript = '"{0}"' -f [IO.Path]::GetFullPath((Join-Path $appRoot "tools\run_host.py"))
    $quotedTrayScript = '"{0}"' -f [IO.Path]::GetFullPath((Join-Path $appRoot "tools\run_tray.py"))
    try {
        $ownedProcesses = @(Get-CurrentUserMeowKeeperProcesses $expectedPythonw $quotedHostScript $quotedTrayScript)
    }
    catch {
        throw "無法安全讀取目前使用者的程序清單；解除安裝已中止，零檔案已移除。Startup 啟動檔、App 應用與 Data 資料均未變更。請修復程序資訊存取後再重試。"
    }
    try {
        foreach ($process in $ownedProcesses) {
            $processHandle = $null
            try {
                if ($null -eq $process.CreationDate) { throw "Process creation time is unavailable." }
                $processHandle = [Diagnostics.Process]::GetProcessById([int]$process.ProcessId)
                $null = $processHandle.Handle
                $handlePath = [IO.Path]::GetFullPath($processHandle.MainModule.FileName)
                if (-not [StringComparer]::OrdinalIgnoreCase.Equals($handlePath, $expectedPythonw)) {
                    throw "Process executable path changed."
                }
                $inventoryStartTime = ([datetime]$process.CreationDate).ToUniversalTime()
                $handleStartTime = $processHandle.StartTime.ToUniversalTime()
                if ([math]::Abs(($handleStartTime - $inventoryStartTime).TotalSeconds) -gt 1) {
                    throw "Process identity changed."
                }
                Stop-Process -InputObject $processHandle -Force -ErrorAction Stop
                if (-not $processHandle.WaitForExit(15000)) { throw "Process exit timed out." }
                $processHandle.Refresh()
                if (-not $processHandle.HasExited) { throw "Process exit could not be confirmed." }
            }
            finally {
                if ($null -ne $processHandle) { $processHandle.Dispose() }
            }
        }
    }
    catch {
        throw "無法停止或確認 MeowKeeper+ Host／Tray 程序已結束；解除安裝已中止，Startup 啟動檔、App 應用與 Data 資料均未移除。請先確認程序已退出後再重試。"
    }
    try {
        $remainingProcesses = @(Get-CurrentUserMeowKeeperProcesses $expectedPythonw $quotedHostScript $quotedTrayScript)
    }
    catch {
        throw "無法重新確認目前使用者的程序清單；解除安裝已中止，Startup 啟動檔、App 應用與 Data 資料均未移除。請修復程序資訊存取後再重試。"
    }
    if ($remainingProcesses.Count -gt 0) {
        throw "仍有 MeowKeeper+ Host／Tray 程序符合本安裝的安全識別條件；解除安裝已中止，Startup 啟動檔、App 應用與 Data 資料均未移除。"
    }
    foreach ($link in $startupLinks) {
        if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link -Force }
    }
} else {
    foreach ($task in $tasks) {
        if (Test-ScheduledTaskExists $task) {
            & "$env:SystemRoot\System32\schtasks.exe" /End /TN $task *> $null
            & "$env:SystemRoot\System32\schtasks.exe" /Delete /TN $task /F | Out-Host
        }
    }
}

if (Test-Path -LiteralPath $startMenuShortcut -PathType Leaf) {
    Assert-OwnedStartMenuShortcut $startMenuShortcut $appRoot
    Remove-Item -LiteralPath $startMenuShortcut -Force
    if ((Test-Path -LiteralPath $startMenuFolder -PathType Container) -and
        @(Get-ChildItem -LiteralPath $startMenuFolder -Force).Count -eq 0) {
        Remove-Item -LiteralPath $startMenuFolder -Force
    }
}

if (Test-Path -LiteralPath $appRoot) {
    $resolvedApp = (Resolve-Path -LiteralPath $appRoot).Path
    $expectedApp = [IO.Path]::GetFullPath((Join-Path $installRoot "App"))
    if ($resolvedApp -ne $expectedApp -or -not (Test-Path (Join-Path $resolvedApp "build_manifest.json"))) {
        throw "安裝目錄安全驗證失敗，拒絕刪除。"
    }
    $ptzFirewallTool = Join-Path $resolvedApp 'scripts\Sync-PtzUdpFirewall.ps1'
    if (Test-Path -LiteralPath $ptzFirewallTool) {
        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $ptzFirewallTool -Mode Remove -AppRoot $resolvedApp
        if ($LASTEXITCODE -ne 0) { throw '無法安全移除 MeowKeeper+ 自建 PTZ UDP 規則；未刪除應用。' }
    }
    $xiaomiFirewallTool = Join-Path $resolvedApp "scripts\Manage-XiaomiUdpFirewall.ps1"
    if (Test-Path -LiteralPath $xiaomiFirewallTool) {
        $firewallJson = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $xiaomiFirewallTool -Mode RemoveAllOwned -AppRoot $resolvedApp
        $firewallResult = $firewallJson | ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or $firewallResult.status -ne "passed") {
            throw "無法安全確認或移除 MeowKeeper+ 自建的小米 UDP 防火牆規則；應用檔案仍保留，請檢查管理員授權。"
        }
    }
    Remove-Item -LiteralPath $resolvedApp -Recurse -Force
}
if ($RemoveData -and (Test-Path -LiteralPath $dataRoot)) {
    $resolvedData = (Resolve-Path -LiteralPath $dataRoot).Path
    $expectedData = [IO.Path]::GetFullPath((Join-Path $installRoot "Data"))
    if ($resolvedData -ne $expectedData) { throw "資料目錄安全驗證失敗，拒絕刪除。" }
    Remove-Item -LiteralPath $resolvedData -Recurse -Force
}
Write-Host "✅ 已移除 MeowKeeper+ 排程工作與應用檔案。"
if (-not $RemoveData) { Write-Host "ℹ️ 設定、事件與日誌已保留在：$dataRoot" }
