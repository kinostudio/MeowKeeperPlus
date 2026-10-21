param(
    [switch]$WhatIf,
    [switch]$Upgrade,
    [switch]$SkipStart,
    [switch]$CurrentUser,
    [switch]$EnableCpuTemperature,
    [switch]$SkipCpuTemperature,
    [switch]$UserHost,
    [ValidatePattern('^S-1-5-21-(\d+-){3}\d+$')][string]$HostUserSid
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"

$packageRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$installRoot = if ($CurrentUser) {
    Join-Path $env:LOCALAPPDATA "MeowKeeperPlus"
} else {
    Join-Path $env:ProgramData "MeowKeeperPlus"
}
$appRoot = Join-Path $installRoot "App"
$dataRoot = Join-Path $installRoot "Data"
$coreTask = if ($CurrentUser) { "MeowKeeperPlus User Core" } else { "MeowKeeperPlus Core" }
$trayTask = if ($CurrentUser) { "MeowKeeperPlus User Tray" } else { "MeowKeeperPlus Tray" }
$coreRole = if ($CurrentUser -or $UserHost) { "user_core" } else { "core" }
$interactiveUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($UserHost) {
    if (-not $HostUserSid) { $HostUserSid = $interactiveUserSid }
    $interactiveUserSid = $HostUserSid
}
$startupFolder = [Environment]::GetFolderPath("Startup")
$coreStartupLink = Join-Path $startupFolder "MeowKeeperPlus User Core.cmd"
$trayStartupLink = Join-Path $startupFolder "MeowKeeperPlus User Tray.cmd"
$startMenuPrograms = if ($CurrentUser) {
    [Environment]::GetFolderPath("Programs")
} else {
    [Environment]::GetFolderPath("CommonPrograms")
}
$startMenuFolder = Join-Path $startMenuPrograms "MeowKeeperPlus"
$startMenuShortcut = Join-Path $startMenuFolder "喵喵守護者+.lnk"
$legacyStartupLinks = @(
    (Join-Path $startupFolder "MeowKeeperPlus User Core.lnk"),
    (Join-Path $startupFolder "MeowKeeperPlus User Tray.lnk")
)
$currentUserInstallMarkerPaths = @(
    (Join-Path $env:LOCALAPPDATA "MeowKeeperPlus\App\build_manifest.json"),
    $coreStartupLink,
    $trayStartupLink,
    (Join-Path (Join-Path ([Environment]::GetFolderPath("Programs")) "MeowKeeperPlus") "喵喵守護者+.lnk")
) + $legacyStartupLinks
$detectedCurrentUserInstallMarkers = @(
    $currentUserInstallMarkerPaths |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
)

if (-not $CurrentUser -and -not $WhatIf -and $detectedCurrentUserInstallMarkers.Count -gt 0) {
    $markerList = $detectedCurrentUserInstallMarkers -join "`n - "
    throw ("偵測到目前使用者模式的 MeowKeeper+ 安裝痕跡，為避免覆蓋而已在 UAC 前停止；未變更檔案、排程或程序：`n - {0}`n請先以目前使用者權限執行 Uninstall-MeowKeeperPlus.cmd -CurrentUser（預設保留 Data），完成後再重試系統模式安裝。" -f $markerList)
}

if ($WhatIf -and -not $CurrentUser -and $detectedCurrentUserInstallMarkers.Count -gt 0) {
    [pscustomobject]@{
        status = "blocked"
        package_root = $packageRoot
        install_root = $installRoot
        application_root = $appRoot
        data_root = $dataRoot
        current_user_install_markers = @($detectedCurrentUserInstallMarkers)
        system_mode_blocked_by_current_user_install = $true
        tasks = @($coreTask, $trayTask)
        startup_links = @()
        registration = "task_scheduler"
        starts_tasks = -not $SkipStart
        system_changed = $false
    } | ConvertTo-Json -Depth 3
    exit 0
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-ScheduledTaskExists([string]$Name) {
    $previousPreference = $ErrorActionPreference
    try {
        # A missing task is an expected false result. Windows PowerShell maps
        # schtasks stderr into its error stream, so suppress it only here.
        $ErrorActionPreference = "SilentlyContinue"
        & "$env:SystemRoot\System32\schtasks.exe" /Query /TN $Name *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

function New-StartupCommand(
    [string]$Path,
    [string]$Target,
    [string]$Arguments,
    [string]$WorkingDirectory
) {
    $content = "@echo off`r`nset PYTHONDONTWRITEBYTECODE=1`r`ncd /d `"$WorkingDirectory`" || exit /b 2`r`nstart `"`" /b `"$Target`" $Arguments`r`n"
    [IO.File]::WriteAllText($Path, $content, [Text.UTF8Encoding]::new($false))
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

function New-StartMenuLauncherShortcut([string]$ShortcutPath, [string]$ApplicationRoot) {
    if ((Test-Path -LiteralPath $ShortcutPath -PathType Leaf) -and -not (Test-OwnedStartMenuShortcut $ShortcutPath $ApplicationRoot)) {
        throw "開始功能表已有同名非 MeowKeeper+ 捷徑；為避免覆蓋，安裝已停止：$ShortcutPath"
    }
    $launcher = Join-Path $ApplicationRoot "scripts\Launch-Installed-MeowKeeperPlus.ps1"
    $pythonIcon = Join-Path $ApplicationRoot "assets\tray\meowkeeper-earth-ready.ico"
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf) -or -not (Test-Path -LiteralPath $pythonIcon -PathType Leaf)) {
        throw "安裝版啟動入口或圖示缺少；未建立捷徑。"
    }
    $shortcutDirectory = Split-Path -Parent $ShortcutPath
    New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $null
    try {
        $shortcut = $shell.CreateShortcut($ShortcutPath)
        $shortcut.TargetPath = $powershell
        $shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $launcher + '"'
        $shortcut.WorkingDirectory = $ApplicationRoot
        $shortcut.IconLocation = $pythonIcon + ',0'
        $shortcut.Description = '啟動喵喵守護者+ 背景監控與 Dashboard'
        $shortcut.Save()
    }
    finally {
        if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
}

if ((Test-Path -LiteralPath $startMenuShortcut -PathType Leaf) -and
    -not (Test-OwnedStartMenuShortcut $startMenuShortcut $appRoot)) {
    throw "開始功能表已有同名非 MeowKeeper+ 捷徑；安裝在任何排程或程式變更前停止：$startMenuShortcut"
}

function Get-CurrentUserMeowKeeperProcesses(
    [string]$ExpectedPythonw,
    [string]$QuotedHostScript,
    [string]$QuotedTrayScript
) {
    $processInventory = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $hostCommandPrefix = '"{0}" {1}' -f $ExpectedPythonw, $QuotedHostScript
    $trayCommandPrefix = '"{0}" {1}' -f $ExpectedPythonw, $QuotedTrayScript
    $commandLineCandidates = @(
        $processInventory |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_.CommandLine) -and
                (
                    ($_.CommandLine.StartsWith($hostCommandPrefix, [StringComparison]::OrdinalIgnoreCase) -and
                        ($_.CommandLine.Length -eq $hostCommandPrefix.Length -or [char]::IsWhiteSpace($_.CommandLine[$hostCommandPrefix.Length]))) -or
                    ($_.CommandLine.StartsWith($trayCommandPrefix, [StringComparison]::OrdinalIgnoreCase) -and
                        ($_.CommandLine.Length -eq $trayCommandPrefix.Length -or [char]::IsWhiteSpace($_.CommandLine[$trayCommandPrefix.Length])))
                )
            }
    )
    if (@($commandLineCandidates | Where-Object {
        [string]::IsNullOrWhiteSpace($_.ExecutablePath) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $ExpectedPythonw)
    }).Count -gt 0) {
        throw "A target script command line has an unverifiable executable path."
    }
    $appProcesses = @(
        $processInventory |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $ExpectedPythonw)
            }
    )
    if (@($appProcesses | Where-Object { [string]::IsNullOrWhiteSpace($_.CommandLine) }).Count -gt 0) {
        throw "Command line is unavailable for an application runtime process."
    }
    @(
        $appProcesses |
            Where-Object {
                $commandLine = $_.CommandLine
                $isHost = $commandLine.StartsWith($hostCommandPrefix, [StringComparison]::OrdinalIgnoreCase) -and
                    ($commandLine.Length -eq $hostCommandPrefix.Length -or [char]::IsWhiteSpace($commandLine[$hostCommandPrefix.Length]))
                $isTray = $commandLine.StartsWith($trayCommandPrefix, [StringComparison]::OrdinalIgnoreCase) -and
                    ($commandLine.Length -eq $trayCommandPrefix.Length -or [char]::IsWhiteSpace($commandLine[$trayCommandPrefix.Length]))
                $isHost -or $isTray
            }
    )
}

function Get-ProcessHandleById([int]$ProcessId) {
    return [Diagnostics.Process]::GetProcessById($ProcessId)
}

function Get-ProcessHandleOrNullIfExited([int]$ProcessId) {
    try {
        return (Get-ProcessHandleById $ProcessId)
    }
    catch {
        # Windows PowerShell wraps a .NET Process.GetProcessById
        # ArgumentException in MethodInvocationException. Accept only that
        # precise missing-process case, then confirm absence in a fresh query.
        $exception = $_.Exception
        $isMissingProcess = $exception -is [ArgumentException] -or
            ($exception -is [System.Management.Automation.MethodInvocationException] -and
                $exception.InnerException -is [ArgumentException])
        if (-not $isMissingProcess) { throw }
        $live = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId) -ErrorAction Stop)
        if ($live.Count -eq 0) { return $null }
        throw "程序 PID=$ProcessId 仍存在但無法開啟控制代碼；無法確認身分，拒絕升級。"
    }
}

function Stop-CurrentUserProcesses([string]$ApplicationRoot) {
    $expectedPythonw = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "runtime\python\pythonw.exe"))
    $quotedHostScript = '"{0}"' -f [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "tools\run_host.py"))
    $quotedTrayScript = '"{0}"' -f [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "tools\run_tray.py"))
    try {
        $ownedProcesses = @(Get-CurrentUserMeowKeeperProcesses $expectedPythonw $quotedHostScript $quotedTrayScript)
    }
    catch {
        throw "無法安全讀取目前使用者的程序清單；升級已中止，舊 App 尚未移除。請修復程序資訊存取後再重試。"
    }

    $deadlineUtc = [datetime]::UtcNow.AddSeconds(15)
    try {
        foreach ($process in $ownedProcesses) {
            $processHandle = $null
            try {
                if ($null -eq $process.CreationDate) { throw "Process creation time is unavailable." }
                $processHandle = Get-ProcessHandleOrNullIfExited ([int]$process.ProcessId)
                if ($null -eq $processHandle) { continue }
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
                $liveInventory = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$process.ProcessId) -ErrorAction Stop)
                if ($liveInventory.Count -ne 1) { throw "Process disappeared or PID is ambiguous." }
                $liveProcess = $liveInventory[0]
                if (-not [StringComparer]::OrdinalIgnoreCase.Equals($liveProcess.ExecutablePath, $expectedPythonw) -or
                    -not [StringComparer]::Ordinal.Equals($liveProcess.CommandLine, $process.CommandLine) -or
                    ([datetime]$liveProcess.CreationDate).ToUniversalTime() -ne $inventoryStartTime) {
                    throw "Process inventory changed before termination."
                }
                $remainingWaitMs = [int][math]::Floor(($deadlineUtc - [datetime]::UtcNow).TotalMilliseconds)
                if ($remainingWaitMs -le 0) { throw "Process exit timed out." }
                Stop-Process -InputObject $processHandle -Force -ErrorAction Stop
                if (-not $processHandle.WaitForExit($remainingWaitMs)) { throw "Process exit timed out." }
                $processHandle.Refresh()
                if (-not $processHandle.HasExited) { throw "Process exit could not be confirmed." }
            }
            finally {
                if ($null -ne $processHandle) { $processHandle.Dispose() }
            }
        }
    }
    catch {
        throw "無法停止或確認 MeowKeeper+ Host／Tray 程序已結束；升級已中止，舊 App 尚未移除。請先確認程序已退出後再重試。"
    }

    try {
        $remainingProcesses = @(Get-CurrentUserMeowKeeperProcesses $expectedPythonw $quotedHostScript $quotedTrayScript)
    }
    catch {
        throw "無法重新確認目前使用者的程序清單；升級已中止，舊 App 尚未移除。請修復程序資訊存取後再重試。"
    }
    if ($remainingProcesses.Count -gt 0) {
        throw "仍有 MeowKeeper+ Host／Tray 程序符合本安裝的安全識別條件；升級已中止，舊 App 尚未移除。"
    }
}

function Stop-InstalledHostProcess([string]$ApplicationRoot) {
    # Windows multiprocessing workers use the bundled interpreter but do not
    # include run_host.py in their command line. Stop only interpreters whose
    # executable is inside this exact App runtime; unrelated Python installs
    # are never selected. Workers are stopped before their Host/Tray parents.
    $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "runtime\python"))
    $expectedExecutables = @(
        [IO.Path]::GetFullPath((Join-Path $runtimeRoot "python.exe")),
        [IO.Path]::GetFullPath((Join-Path $runtimeRoot "pythonw.exe"))
    )
    $hostScript = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "tools\run_host.py"))
    $trayScript = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "tools\run_tray.py"))

    for ($pass = 1; $pass -le 3; $pass++) {
        $inventory = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)
        $owned = @(Get-InstalledRuntimePythonProcesses $ApplicationRoot $inventory)
        if ($owned.Count -eq 0) { return }

        foreach ($process in $owned) {
            $expectedPath = [string]$process.ExecutablePath
            $handle = Get-ProcessHandleOrNullIfExited ([int]$process.ProcessId)
            if ($null -eq $handle) { continue }
            try {
                if ([string]::IsNullOrWhiteSpace($process.CommandLine) -or $null -eq $process.CreationDate -or
                    -not [StringComparer]::OrdinalIgnoreCase.Equals($handle.MainModule.FileName, $expectedPath) -or
                    [math]::Abs(($handle.StartTime.ToUniversalTime() - ([datetime]$process.CreationDate).ToUniversalTime()).TotalSeconds) -gt 1) {
                    throw "舊版內附 Python 程序身分無法完整驗證；升級已中止。"
                }
                $live = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$process.ProcessId) -ErrorAction Stop)
                if ($live.Count -ne 1 -or
                    -not [StringComparer]::OrdinalIgnoreCase.Equals($live[0].ExecutablePath, $expectedPath) -or
                    -not [StringComparer]::Ordinal.Equals($live[0].CommandLine, $process.CommandLine) -or
                    ([datetime]$live[0].CreationDate).ToUniversalTime() -ne ([datetime]$process.CreationDate).ToUniversalTime()) {
                    throw "舊版內附 Python 程序資訊已變更；升級已中止。"
                }
                $role = if ($process.CommandLine.IndexOf($hostScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    "Core"
                } elseif ($process.CommandLine.IndexOf($trayScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    "Tray"
                } else {
                    "AI／Python 子程序"
                }
                Write-Host ("升級停止已驗證的舊 {0} PID={1} Path={2}" -f $role, $process.ProcessId, $expectedPath)
                Stop-Process -InputObject $handle -Force -ErrorAction Stop
                if (-not $handle.WaitForExit(15000)) { throw "舊版內附 Python 程序未在 15 秒內退出；升級已中止。" }
            }
            finally { $handle.Dispose() }
        }
        Start-Sleep -Milliseconds 250
    }

    $remainingInventory = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)
    $remaining = @(Get-InstalledRuntimePythonProcesses $ApplicationRoot $remainingInventory)
    if ($remaining.Count -gt 0) { throw "仍有此安裝目錄的 Python／AI 子程序執行；升級已中止。" }
}

function Get-InstalledRuntimePythonProcesses([string]$ApplicationRoot, [object[]]$ProcessInventory) {
    $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "runtime\python"))
    $expectedExecutables = @(
        [IO.Path]::GetFullPath((Join-Path $runtimeRoot "python.exe")),
        [IO.Path]::GetFullPath((Join-Path $runtimeRoot "pythonw.exe"))
    )
    $hostScript = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "tools\run_host.py"))
    $trayScript = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "tools\run_tray.py"))
    $owned = @()
    foreach ($process in $ProcessInventory) {
        $matchesExecutable = -not [string]::IsNullOrWhiteSpace($process.ExecutablePath) -and
            @($expectedExecutables | Where-Object {
                [StringComparer]::OrdinalIgnoreCase.Equals([string]$process.ExecutablePath, $_)
            }).Count -gt 0
        $matchesCommand = -not [string]::IsNullOrWhiteSpace($process.CommandLine) -and
            @($expectedExecutables | Where-Object {
                $prefix = '"{0}"' -f $_
                $plain = $_
                $process.CommandLine.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
                    $process.CommandLine.StartsWith($plain, [StringComparison]::OrdinalIgnoreCase)
            }).Count -gt 0
        if (-not $matchesExecutable -and -not $matchesCommand) { continue }
        if (-not $matchesExecutable -or [string]::IsNullOrWhiteSpace($process.CommandLine) -or $null -eq $process.CreationDate) {
            throw "發現無法驗證路徑或命令列的內附 Python 程序；升級已中止。"
        }
        $owned += $process
    }
    # The worker command line is opaque; prefer it before the parent scripts.
    @($owned | Sort-Object -Property @{
        Expression = {
            if ($_.CommandLine.IndexOf($hostScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $_.CommandLine.IndexOf($trayScript, [StringComparison]::OrdinalIgnoreCase) -ge 0) { 1 } else { 0 }
        }
    }, @{ Expression = { ([datetime]$_.CreationDate).Ticks }; Descending = $true })
}

function Stop-VerifiedRepositoryTray([string]$PackageRoot) {
    # A development Tray can hold the same session mutex as the installed
    # Tray. Only identify the repository adjacent to this verified dist package.
    $dist = Split-Path -Path $PackageRoot -Parent
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals((Split-Path -Path $dist -Leaf), 'dist')) { return }
    $repository = Split-Path -Path $dist -Parent
    $expectedExe = [IO.Path]::GetFullPath((Join-Path $repository '.venv\Scripts\pythonw.exe'))
    $expectedScript = [IO.Path]::GetFullPath((Join-Path $repository 'tools\run_tray.py'))
    if (-not (Test-Path -LiteralPath $expectedExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $expectedScript -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $repository 'AGENTS.md') -PathType Leaf)) { return }
    $prefixes = @(
        ('"{0}" "{1}"' -f $expectedExe, $expectedScript),
        ('"{0}" {1}' -f $expectedExe, $expectedScript),
        ('{0} "{1}"' -f $expectedExe, $expectedScript),
        ('{0} {1}' -f $expectedExe, $expectedScript)
    )
    $inventory = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction Stop)
    $candidates = @($inventory | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($expectedScript)
    })
    if ($candidates.Count -gt 1) { throw '找到多個舊版 repository Tray，拒絕自動停止。' }
    foreach ($process in $candidates) {
        $validPrefix = $false
        foreach ($prefix in $prefixes) {
            if ($process.CommandLine.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
                ($process.CommandLine.Length -eq $prefix.Length -or [char]::IsWhiteSpace($process.CommandLine[$prefix.Length]))) {
                $validPrefix = $true
            }
        }
        if (-not $validPrefix -or
            -not [StringComparer]::OrdinalIgnoreCase.Equals($process.ExecutablePath, $expectedExe) -or
            $null -eq $process.CreationDate) {
            throw '舊版 Tray 程序身分不明，拒絕自動停止。'
        }
        $handle = Get-ProcessHandleOrNullIfExited ([int]$process.ProcessId)
        if ($null -eq $handle) { continue }
        try {
            if (-not [StringComparer]::OrdinalIgnoreCase.Equals($handle.MainModule.FileName, $expectedExe) -or
                [math]::Abs(($handle.StartTime.ToUniversalTime() - ([datetime]$process.CreationDate).ToUniversalTime()).TotalSeconds) -gt 1) {
                throw '舊版 Tray PID 身分已變更，拒絕自動停止。'
            }
            $live = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$process.ProcessId) -ErrorAction Stop)
            if ($live.Count -ne 1 -or
                -not [StringComparer]::OrdinalIgnoreCase.Equals($live[0].ExecutablePath, $expectedExe) -or
                -not [StringComparer]::Ordinal.Equals($live[0].CommandLine, $process.CommandLine)) {
                throw '舊版 Tray 程序資訊已變更，拒絕自動停止。'
            }
            Stop-Process -InputObject $handle -Force -ErrorAction Stop
            if (-not $handle.WaitForExit(15000)) { throw '舊版 Tray 未能停止，拒絕繼續升級。' }
            Write-Host '已停止經完整路徑與命令列驗證的 repository 舊 Tray。'
        }
        finally { $handle.Dispose() }
    }
}

function Stop-InstalledGo2Rtc([string]$ApplicationRoot, [string]$RelativeBinary = "vendor\go2rtc\v1.9.14\go2rtc.exe") {
    if ($RelativeBinary -eq "vendor\go2rtc\v1.9.14\go2rtc.exe") {
        Stop-InstalledGo2Rtc $ApplicationRoot "vendor\go2rtc\ptz-feedback\go2rtc.exe"
    }
    $expectedExe = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot $RelativeBinary))
    if (-not (Test-Path -LiteralPath $expectedExe -PathType Leaf)) { return }
    $inventory = @(Get-CimInstance Win32_Process -Filter "Name='go2rtc.exe'" -ErrorAction Stop)
    $owned = @($inventory | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
        [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $expectedExe)
    })
    $quotedPrefix = '"{0}" -config ' -f $expectedExe
    $plainPrefix = '{0} -config ' -f $expectedExe
    if ($owned.Count -gt 1 -or @($inventory | Where-Object {
        [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
        -not [string]::IsNullOrWhiteSpace($_.CommandLine) -and
        ($_.CommandLine.StartsWith($quotedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            $_.CommandLine.StartsWith($plainPrefix, [StringComparison]::OrdinalIgnoreCase))
    }).Count -gt 0) {
        throw "go2rtc 程序歸屬不明或數量異常；舊 App 尚未移除。"
    }
    foreach ($process in $owned) {
        if ([string]::IsNullOrWhiteSpace($process.CommandLine) -or
            -not ($process.CommandLine.StartsWith($quotedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                $process.CommandLine.StartsWith($plainPrefix, [StringComparison]::OrdinalIgnoreCase)) -or
            $null -eq $process.CreationDate) {
            throw "無法確認安裝版 go2rtc 命令列或建立時間；舊 App 尚未移除。"
        }
        $handle = Get-ProcessHandleOrNullIfExited ([int]$process.ProcessId)
        if ($null -eq $handle) { continue }
        try {
            if (-not [StringComparer]::OrdinalIgnoreCase.Equals($handle.MainModule.FileName, $expectedExe) -or
                [math]::Abs(($handle.StartTime.ToUniversalTime() - ([datetime]$process.CreationDate).ToUniversalTime()).TotalSeconds) -gt 1) {
                throw "go2rtc 程序身分已變更；舊 App 尚未移除。"
            }
            $live = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$process.ProcessId) -ErrorAction Stop)
            if ($live.Count -ne 1 -or
                -not [StringComparer]::OrdinalIgnoreCase.Equals($live[0].ExecutablePath, $expectedExe) -or
                -not [StringComparer]::Ordinal.Equals($live[0].CommandLine, $process.CommandLine)) {
                throw "go2rtc 程序資訊已變更；舊 App 尚未移除。"
            }
            Stop-Process -InputObject $handle -Force -ErrorAction Stop
            if (-not $handle.WaitForExit(15000)) { throw "go2rtc 未在 15 秒內退出；舊 App 尚未移除。" }
        }
        finally {
            $handle.Dispose()
        }
    }
    if (@(Get-CimInstance Win32_Process -Filter "Name='go2rtc.exe'" -ErrorAction Stop | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
        [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $expectedExe)
    }).Count -gt 0) {
        throw "安裝版 go2rtc 仍在執行；舊 App 尚未移除。"
    }
}

function Stop-VerifiedMediaProcess([object]$Snapshot, [string]$ExpectedName, [string]$ExpectedExecutablePath = "") {
    $processId = [int]$Snapshot.ProcessId
    if ($null -eq $Snapshot.CreationDate) {
        throw ("{0} PID={1} 缺少建立時間，拒絕升級。" -f $ExpectedName, $processId)
    }
    $expectedProcessName = [IO.Path]::GetFileNameWithoutExtension($ExpectedName)
    $expectedPath = if ([string]::IsNullOrWhiteSpace($ExpectedExecutablePath)) { "" } else { [IO.Path]::GetFullPath($ExpectedExecutablePath) }
    $quotedPrefix = if ($expectedPath) { '"{0}" ' -f $expectedPath } else { "" }
    $plainPrefix = if ($expectedPath) { '{0} ' -f $expectedPath } else { "" }
    $snapshotCommandLineMatches = -not $expectedPath -or (
        -not [string]::IsNullOrWhiteSpace($Snapshot.ExecutablePath) -and
        [StringComparer]::OrdinalIgnoreCase.Equals($Snapshot.ExecutablePath, $expectedPath)
    ) -or (
        [string]::IsNullOrWhiteSpace($Snapshot.ExecutablePath) -and
        -not [string]::IsNullOrWhiteSpace($Snapshot.CommandLine) -and
        ($Snapshot.CommandLine.StartsWith($quotedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            $Snapshot.CommandLine.StartsWith($plainPrefix, [StringComparison]::OrdinalIgnoreCase))
    )
    if (-not $snapshotCommandLineMatches) {
        throw ("{0} PID={1} 不屬於預期安裝路徑，拒絕停止。" -f $ExpectedName, $processId)
    }

    $handle = Get-ProcessHandleOrNullIfExited $processId
    if ($null -eq $handle) { return }
    try {
        $handleCreationDelta = [math]::Abs(($handle.StartTime.ToUniversalTime() - ([datetime]$Snapshot.CreationDate).ToUniversalTime()).TotalSeconds)
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals($handle.ProcessName, $expectedProcessName) -or $handleCreationDelta -gt 1) {
            throw ("{0} PID={1} 身分已變更，拒絕停止。" -f $ExpectedName, $processId)
        }
        $live = @(Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $processId) -ErrorAction Stop)
        if ($live.Count -ne 1 -or
            -not [StringComparer]::OrdinalIgnoreCase.Equals($live[0].Name, $ExpectedName) -or
            [math]::Abs((([datetime]$live[0].CreationDate).ToUniversalTime() - ([datetime]$Snapshot.CreationDate).ToUniversalTime()).TotalSeconds) -gt 1) {
            throw ("{0} PID={1} 清單資料已變更，拒絕停止。" -f $ExpectedName, $processId)
        }
        if ($expectedPath) {
            $livePathMatches = -not [string]::IsNullOrWhiteSpace($live[0].ExecutablePath) -and
                [StringComparer]::OrdinalIgnoreCase.Equals($live[0].ExecutablePath, $expectedPath)
            $liveCommandLineMatches = [string]::IsNullOrWhiteSpace($live[0].ExecutablePath) -and
                -not [string]::IsNullOrWhiteSpace($live[0].CommandLine) -and
                ($live[0].CommandLine.StartsWith($quotedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                    $live[0].CommandLine.StartsWith($plainPrefix, [StringComparison]::OrdinalIgnoreCase))
            if (-not ($livePathMatches -or $liveCommandLineMatches) -or
                ($Snapshot.CommandLine -and -not [StringComparer]::Ordinal.Equals($live[0].CommandLine, $Snapshot.CommandLine))) {
                throw ("{0} PID={1} 路徑或命令列已變更，拒絕停止。" -f $ExpectedName, $processId)
            }
        } else {
            if ((-not [string]::IsNullOrWhiteSpace($Snapshot.ExecutablePath)) -and
                (-not [string]::IsNullOrWhiteSpace($live[0].ExecutablePath)) -and
                -not [StringComparer]::OrdinalIgnoreCase.Equals($live[0].ExecutablePath, $Snapshot.ExecutablePath)) {
                throw ("{0} PID={1} 執行檔路徑已變更，拒絕停止。" -f $ExpectedName, $processId)
            }
            if ($Snapshot.CommandLine -and -not [StringComparer]::Ordinal.Equals($live[0].CommandLine, $Snapshot.CommandLine)) {
                throw ("{0} PID={1} 命令列已變更，拒絕停止。" -f $ExpectedName, $processId)
            }
        }
        Write-Host ("升級停止 {0} PID={1} Path={2}" -f $ExpectedName, $processId, $(if ($live[0].ExecutablePath) { $live[0].ExecutablePath } else { "unavailable" }))
        Stop-Process -InputObject $handle -Force -ErrorAction Stop
        if (-not $handle.WaitForExit(15000)) { throw ("{0} PID={1} 未在 15 秒內退出，拒絕移除舊 App。" -f $ExpectedName, $processId) }
    }
    finally {
        $handle.Dispose()
    }
}

function Stop-InstalledFfmpegForUpgrade([string]$ApplicationRoot) {
    $expectedExe = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "vendor\ffmpeg\bin\ffmpeg.exe"))
    $quotedPrefix = '"{0}" ' -f $expectedExe
    $plainPrefix = '{0} ' -f $expectedExe
    for ($pass = 1; $pass -le 3; $pass++) {
        $inventory = @(Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe'" -ErrorAction Stop | Sort-Object ProcessId)
        $owned = @($inventory | Where-Object {
            (-not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $expectedExe)) -or
            ([string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                -not [string]::IsNullOrWhiteSpace($_.CommandLine) -and
                ($_.CommandLine.StartsWith($quotedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                    $_.CommandLine.StartsWith($plainPrefix, [StringComparison]::OrdinalIgnoreCase)))
        })
        if ($owned.Count -eq 0) { return }
        foreach ($process in $owned) {
            Stop-VerifiedMediaProcess $process "ffmpeg.exe" $expectedExe
        }
        Start-Sleep -Milliseconds 300
    }
    # The caller verifies both owned FFmpeg and FFprobe after stopping each.
}

function Stop-InstalledFfprobe([string]$ApplicationRoot) {
    $expectedExe = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "vendor\ffmpeg\bin\ffprobe.exe"))
    $quotedPrefix = '"{0}" ' -f $expectedExe
    $plainPrefix = '{0} ' -f $expectedExe
    $inventory = @(Get-CimInstance Win32_Process -Filter "Name='ffprobe.exe'" -ErrorAction Stop)
    $owned = @($inventory | Where-Object {
        (-not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
            [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $expectedExe)) -or
        ([string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
            -not [string]::IsNullOrWhiteSpace($_.CommandLine) -and
            ($_.CommandLine.StartsWith($quotedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
                $_.CommandLine.StartsWith($plainPrefix, [StringComparison]::OrdinalIgnoreCase)))
    })
    foreach ($process in $owned) {
        Stop-VerifiedMediaProcess $process "ffprobe.exe" $expectedExe
    }
}

function Assert-NoInstalledMediaConsumers([string]$ApplicationRoot) {
    $expectedBin = [IO.Path]::GetFullPath((Join-Path $ApplicationRoot "vendor\ffmpeg\bin"))
    $expectedExecutables = @(
        (Join-Path $expectedBin "ffmpeg.exe"),
        (Join-Path $expectedBin "ffprobe.exe")
    )
    $inventory = @(Get-CimInstance Win32_Process -Filter "Name='ffmpeg.exe' OR Name='ffprobe.exe'" -ErrorAction Stop)
    foreach ($process in $inventory) {
        foreach ($expected in $expectedExecutables) {
            $matchesPath = -not [string]::IsNullOrWhiteSpace($process.ExecutablePath) -and
                [StringComparer]::OrdinalIgnoreCase.Equals($process.ExecutablePath, $expected)
            $matchesUnknownPath = [string]::IsNullOrWhiteSpace($process.ExecutablePath) -and
                -not [string]::IsNullOrWhiteSpace($process.CommandLine) -and
                ($process.CommandLine.StartsWith(('"{0}" ' -f $expected), [StringComparison]::OrdinalIgnoreCase) -or
                    $process.CommandLine.StartsWith(('{0} ' -f $expected), [StringComparison]::OrdinalIgnoreCase))
            if ($matchesPath -or $matchesUnknownPath) {
                throw "安裝版 FFmpeg／FFprobe 程序仍占用舊 App；升級在刪除前停止。請先核對 PID、完整路徑、命令列與建立時間，勿依程序名稱批次終止。"
            }
        }
    }
}

function Write-InstallOutcome {
    param(
        [string]$DataRoot,
        [ValidateSet('passed', 'failed', 'health_failed')][string]$Status,
        [ValidateSet('preflight', 'stop', 'archive', 'data_migration', 'copy', 'verify', 'register', 'start', 'health', 'complete')][string]$Phase,
        [string]$PackageRoot
    )
    # Allowlisted metadata only. Never persist exception text, command lines,
    # usernames, tokens, source URLs, or a full transcript of installation.
    try {
        $root = [IO.Path]::GetFullPath($DataRoot)
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { return }
        $directory = Join-Path $root 'install-history'
        foreach ($candidate in @($root, $directory)) {
            if ((Test-Path -LiteralPath $candidate) -and
                ((Get-Item -LiteralPath $candidate -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { return }
        }
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        $version = 'unknown'
        if ([IO.Path]::GetFileName($PackageRoot) -match '-(r[0-9]+)$') { $version = $Matches[1] }
        $receipt = [ordered]@{
            schema_version = 1
            recorded_at_utc = [datetime]::UtcNow.ToString('o')
            package_version = $version
            status = $Status
            phase = $Phase
            incident_code = if ($Status -eq 'passed') { 'INSTALL-OK' } else { 'INSTALL-' + $Phase.ToUpperInvariant() }
        }
        $target = Join-Path $directory ('attempt-' + [guid]::NewGuid().ToString('N') + '.json')
        [IO.File]::WriteAllText($target, ($receipt | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    }
    catch { Write-Warning '安裝診斷摘要無法保存；不影響原始安裝結果。' }
}

if ($EnableCpuTemperature -and $SkipCpuTemperature) { throw 'CPU 溫度選項互相衝突。' }
if (-not $WhatIf -and -not $EnableCpuTemperature -and -not $SkipCpuTemperature -and
    (Test-Path -LiteralPath (Join-Path $packageRoot 'vendor\cpu-sensor\manifest.json'))) {
    Add-Type -AssemblyName System.Windows.Forms
    $cpuChoice = [System.Windows.Forms.MessageBox]::Show(
        "是否一併啟用 CPU 溫度監測？`n`n缺少驅動時將從官方來源下載、驗證並啟動 PawnIO 2.2.0 安裝器，另建立僅讀 CPU 的受限 SYSTEM 測溫工作，不改 Host 帳號、不開網路埠、不接收任意指令；系統安裝可共用本次 UAC。官方授權條款及版本選項請親自確認，只選官方受限版。`n`n選否不影響監控，日後可雙擊 Enable-CPU-Temperature.cmd。",
        '喵喵守護者+｜選用測溫元件', 'YesNo', 'Question', 'Button1')
    if ($cpuChoice -eq [System.Windows.Forms.DialogResult]::Yes) { $EnableCpuTemperature = $true }
    else { $SkipCpuTemperature = $true }
}
if (-not $CurrentUser -and -not $WhatIf -and -not (Test-Administrator)) {
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $PSCommandPath))
    if ($Upgrade) { $arguments += "-Upgrade" }
    if ($SkipStart) { $arguments += "-SkipStart" }
    if ($EnableCpuTemperature) { $arguments += '-EnableCpuTemperature' }
    if ($SkipCpuTemperature) { $arguments += '-SkipCpuTemperature' }
    if ($UserHost) { $arguments += @('-UserHost','-HostUserSid',$HostUserSid) }
    try {
        $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    catch [System.ComponentModel.Win32Exception] {
        Write-Host "⚠️ 使用者已取消 UAC；未複製檔案、未註冊排程，系統沒有變更。"
        exit 1223
    }
}

function Suspend-InstallerQuickEdit {
    if (-not ('MeowKeeperInstallerConsole' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class MeowKeeperInstallerConsole {
    [DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int id);
    [DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr handle, uint mode);
}
'@
    }
    $consoleHandle = [MeowKeeperInstallerConsole]::GetStdHandle(-10)
    [uint32]$originalMode = 0
    if (-not [MeowKeeperInstallerConsole]::GetConsoleMode($consoleHandle, [ref]$originalMode)) { return $null }
    # ENABLE_EXTENDED_FLAGS is required when disabling ENABLE_QUICK_EDIT_MODE.
    [uint32]$installMode = ($originalMode -bor 0x80) -band (-bnot 0x40)
    if (-not [MeowKeeperInstallerConsole]::SetConsoleMode($consoleHandle, $installMode)) { return $null }
    return [pscustomobject]@{ Handle = $consoleHandle; OriginalMode = $originalMode }
}

$upgradeBackupApp = $null
$upgradeNewAppVerified = $false
$legacyDataMigrationJournal = $null
$installerConsoleState = Suspend-InstallerQuickEdit
try {
$sourcePython = Join-Path $packageRoot "runtime\python\python.exe"
$verifier = Join-Path $packageRoot "tools\verify_portable.py"
$xmlTool = Join-Path $packageRoot "tools\create_task_scheduler_xml.py"
$dataMigrationTool = Join-Path $packageRoot "tools\migrate_legacy_app_data.py"
foreach ($required in @($sourcePython, $verifier, $xmlTool, $dataMigrationTool, (Join-Path $packageRoot "build_manifest.json"))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "發布包不完整，缺少：$required"
    }
}

Write-Host "[喵喵守護者+] 正在驗證發布包，請稍候…"
$installationPhase = 'preflight'
& $sourcePython $verifier --root $packageRoot --allow-runtime-locks
if ($LASTEXITCODE -ne 0) { throw "發布包完整性驗證失敗，已停止安裝。" }

if ((Test-Path -LiteralPath $appRoot) -and -not $Upgrade -and -not $WhatIf) {
    throw "已存在安裝目錄。若要更新，請改用 -Upgrade；資料目錄不會被覆寫。"
}

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("MeowKeeperPlus-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
$legacyDataMigrationJournal = Join-Path $tempRoot "legacy-data-migration.json"
try {
    # The destination App directory does not exist until after the package is
    # copied. Validate the task definitions against the verified source first,
    # then regenerate both XML files against the installed path below.
    $xmlRoot = $packageRoot
    $coreXml = Join-Path $tempRoot "core.xml"
    $trayXml = Join-Path $tempRoot "tray.xml"
    if (-not $CurrentUser) {
        $coreXmlArgs = @($xmlTool, "--root", $xmlRoot, "--data-root", $dataRoot, "--output", $coreXml, "--task-name", $coreTask, "--role", $coreRole)
        if ($UserHost) { $coreXmlArgs += @('--user-id', $interactiveUserSid) }
        $trayXmlArgs = @($xmlTool, "--root", $xmlRoot, "--data-root", $dataRoot, "--output", $trayXml, "--task-name", $trayTask, "--role", "tray", "--user-id", $interactiveUserSid)
        & $sourcePython @coreXmlArgs
        if ($LASTEXITCODE -ne 0) { throw "無法建立 Core Task Scheduler XML。" }
        & $sourcePython @trayXmlArgs
        if ($LASTEXITCODE -ne 0) { throw "無法建立 Tray Task Scheduler XML。" }
    }

if ($WhatIf) {
        [pscustomobject]@{
            status = if (-not $CurrentUser -and $detectedCurrentUserInstallMarkers.Count -gt 0) { "blocked" } else { "planned" }
            package_root = $packageRoot
            install_root = $installRoot
            application_root = $appRoot
            data_root = $dataRoot
            current_user_install_markers = @($detectedCurrentUserInstallMarkers)
            system_mode_blocked_by_current_user_install = (-not $CurrentUser -and $detectedCurrentUserInstallMarkers.Count -gt 0)
            tasks = @($coreTask, $trayTask)
            startup_links = if ($CurrentUser) { @($coreStartupLink, $trayStartupLink) } else { @() }
            start_menu_shortcut = $startMenuShortcut
            registration = if ($CurrentUser) { "current_user_startup" } else { "task_scheduler" }
            starts_tasks = -not $SkipStart
            system_changed = $false
        } | ConvertTo-Json -Depth 3
    exit 0
}

# Validate the old installation and stop only its verified Host/Tray before
# changing Startup entries or deleting the old App directory.
$resolvedUpgradeApp = $null
$installationPhase = 'stop'
$expectedUpgradeApp = [IO.Path]::GetFullPath((Join-Path $installRoot "App"))
if ($Upgrade -and (Test-Path -LiteralPath $appRoot)) {
    $resolvedUpgradeApp = (Resolve-Path -LiteralPath $appRoot).Path
    $upgradeItem = Get-Item -LiteralPath $resolvedUpgradeApp -Force
    if ($resolvedUpgradeApp -ne $expectedUpgradeApp -or ($upgradeItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "安裝目錄安全驗證失敗，拒絕升級。"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedUpgradeApp "build_manifest.json") -PathType Leaf)) {
        Write-Warning "舊 App 缺少完整性清單；視為上次升級中斷的程式目錄，將先封存，不會直接刪除。"
    }
    if ($CurrentUser) {
        Stop-InstalledHostProcess $appRoot
        Stop-CurrentUserProcesses $appRoot
    }
}
if ($Upgrade -and -not $CurrentUser) {
    Stop-VerifiedRepositoryTray $packageRoot
}

if ($CurrentUser) {
        foreach ($legacyLink in $legacyStartupLinks) {
            if (Test-Path -LiteralPath $legacyLink) { Remove-Item -LiteralPath $legacyLink -Force }
        }
        foreach ($link in @($coreStartupLink, $trayStartupLink)) {
            if ((Test-Path -LiteralPath $link) -and -not $Upgrade) {
                throw "已存在啟動捷徑：$link。若要更新，請改用 -Upgrade。"
            }
        }
    } else {
        foreach ($task in @($coreTask, $trayTask)) {
            $taskExists = Test-ScheduledTaskExists $task
            if ($taskExists -and -not $Upgrade) {
                throw "已存在排程工作：$task。若要更新，請改用 -Upgrade。"
            }
            if ($taskExists) {
                & "$env:SystemRoot\System32\schtasks.exe" /End /TN $task *> $null
            }
        }
    }

    if ($null -ne $resolvedUpgradeApp) {
        $installationPhase = 'stop'
        $resolvedApp = (Resolve-Path -LiteralPath $appRoot).Path
        $upgradeItem = Get-Item -LiteralPath $resolvedApp -Force
        if ($resolvedApp -ne $expectedUpgradeApp -or ($upgradeItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "安裝目錄安全驗證失敗，拒絕刪除舊版本。"
        }
        if (-not $CurrentUser) {
            # Stop the exact embedded runtime, including multiprocessing AI
            # workers whose command lines do not contain run_host.py.
            Stop-InstalledHostProcess $resolvedApp
            Stop-CurrentUserProcesses $resolvedApp
            Stop-InstalledGo2Rtc $resolvedApp
            Stop-InstalledFfmpegForUpgrade $resolvedApp
            Stop-InstalledFfprobe $resolvedApp
            Assert-NoInstalledMediaConsumers $resolvedApp
        }
        $resolvedInstallRoot = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $installRoot).Path)
        $backupLeaf = "App.upgrade-backup-$([guid]::NewGuid().ToString('N'))"
        $upgradeBackupApp = [IO.Path]::GetFullPath((Join-Path $resolvedInstallRoot $backupLeaf))
        if ([IO.Path]::GetDirectoryName($upgradeBackupApp) -ne $resolvedInstallRoot -or
            (Test-Path -LiteralPath $upgradeBackupApp)) {
            throw "舊 App 備份路徑驗證失敗；未刪除舊程式。"
        }
        try {
            $installationPhase = 'archive'
            # Same-volume directory rename is the preflight: a locked file
            # aborts here instead of leaving Remove-Item half-complete.
            Move-Item -LiteralPath $resolvedApp -Destination $upgradeBackupApp -ErrorAction Stop
            Write-Host "舊 App 已安全封存，待新版驗證完成後再清理。"
        }
        catch {
            $upgradeBackupApp = $null
            throw "無法安全封存舊 App（可能仍有檔案占用或權限不足）；舊目錄未執行遞迴刪除，升級已停止。請關閉喵喵守護者+ 後再重試。"
        }
        $legacyDataPath = Join-Path $upgradeBackupApp "data"
        $legacyConfigPath = Join-Path $upgradeBackupApp "config.json"
        $legacyDataExists = Test-Path -LiteralPath $legacyDataPath
        $legacyConfigExists = Test-Path -LiteralPath $legacyConfigPath -PathType Leaf
        if ($legacyDataExists -and -not (Test-Path -LiteralPath $legacyDataPath -PathType Container)) {
            throw "舊 App 的 data 項目不是資料夾；為避免遺失，升級已停止並保留舊 App。"
        }
        if ($legacyConfigExists -and -not $legacyDataExists) {
            New-Item -ItemType Directory -Path $legacyDataPath -Force | Out-Null
            $legacyDataExists = $true
        }
        if ($legacyDataExists) {
            $installationPhase = 'data_migration'
            Write-Host "偵測到舊版 App\data；正在安全移入獨立資料目錄，遇到同名資料時會停止且不覆寫。"
            $migrationArgs = @($dataMigrationTool, "--mode", "move", "--source", $legacyDataPath, "--destination", $dataRoot, "--journal", $legacyDataMigrationJournal)
            if ($legacyConfigExists) {
                $migrationArgs += @("--legacy-config", $legacyConfigPath)
            }
            & $sourcePython @migrationArgs
            if ($LASTEXITCODE -ne 0) { throw "舊版資料移轉未完成；已停止升級並保留舊 App。請先查看同名衝突原因，不會覆寫 Data。" }
        }
    }
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    $installationPhase = 'copy'
    Copy-Item -Path (Join-Path $packageRoot "*") -Destination $appRoot -Recurse -Force
    # Runtime sidecars belong to the running portable instance, never to the
    # installation. Remove only copied, recognized sidecars, not source locks.
    foreach ($lockName in @('MeowKeeperPlusHost', 'MeowKeeperPlusTray', 'MeowKeeperPlusGo2Rtc')) {
        $copiedLock = [IO.Path]::GetFullPath((Join-Path $appRoot "data\locks\$lockName.json"))
        if (-not $copiedLock.StartsWith([IO.Path]::GetFullPath($appRoot) + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw '執行期鎖檔目標超出安裝目錄。'
        }
        if (Test-Path -LiteralPath $copiedLock -PathType Leaf) {
            Remove-Item -LiteralPath $copiedLock -Force
        }
    }

    $installedPython = Join-Path $appRoot "runtime\python\python.exe"
    Write-Host "[喵喵守護者+] 檔案已複製，正在驗證安裝目錄的完整校驗碼，請稍候…"
    $installationPhase = 'verify'
    & $installedPython (Join-Path $appRoot "tools\verify_portable.py") --root $appRoot
    if ($LASTEXITCODE -ne 0) { throw "安裝後完整性驗證失敗。" }
    $upgradeNewAppVerified = $true
    if ($EnableCpuTemperature) {
        Write-Host '正在準備選用 CPU 測溫元件；失敗不阻擋影音功能。'
        try {
            & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $appRoot 'scripts\Manage-CpuTemperature.ps1') -Mode Install -Consent
            if ($LASTEXITCODE -ne 0) { Write-Warning ('CPU 測溫元件未完成，錯誤碼 ' + $LASTEXITCODE + '；仍繼續安裝監控。') }
        } catch { Write-Warning 'CPU 測溫元件未完成；仍繼續安裝監控，之後可從獨立入口重試。' }
    }
    if (-not $CurrentUser -and (Test-Path -LiteralPath (Join-Path $appRoot 'vendor\go2rtc\backend.json'))) {
        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $appRoot 'scripts\Sync-PtzUdpFirewall.ps1') -AppRoot $appRoot
        if ($LASTEXITCODE -ne 0) { throw 'PTZ UDP 規則沿用驗證失敗；未啟動 Host。' }
    }
    $installedPythonw = Join-Path $appRoot "runtime\python\pythonw.exe"
    $installationPhase = 'register'
    if ($UserHost -and -not $CurrentUser) {
        # Only this user gains access to application data; executable code stays
        # separate, and the CPU task uses its own protected Program Files copy.
        $dataAcl = Get-Acl -LiteralPath $dataRoot
        $hostSid = New-Object Security.Principal.SecurityIdentifier($HostUserSid)
        $dataAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($hostSid,'Modify','ContainerInherit,ObjectInherit','None','Allow')))
        Set-Acl -LiteralPath $dataRoot -AclObject $dataAcl
        Write-Host 'Host 保留指定使用者身分，登入 Windows 後啟動；不等同無人登入的 SYSTEM 服務。'
    }
    $coreArguments = ('"{0}" --no-open-browser --source-url-env MEOWKEEPER_MEDIA_SOURCE_URL --analyzer auto --dashboard-config "{1}" --database-path "{2}" --event-directory "{3}" --ring-directory "{4}" --preview-directory "{5}" --admin-storage-path "{6}" --lock-root "{7}"' -f (Join-Path $appRoot "tools\run_host.py"), (Join-Path $dataRoot "config.json"), (Join-Path $dataRoot "meowkeeper.db"), (Join-Path $dataRoot "events"), (Join-Path $dataRoot "cache\ring"), (Join-Path $dataRoot "cache\preview"), (Join-Path $dataRoot "web_admin.json"), $installRoot)
    $trayArguments = ('"{0}" --background --lock-root "{1}"' -f (Join-Path $appRoot "tools\run_tray.py"), $installRoot)
    if ($CurrentUser) {
        New-StartupCommand $coreStartupLink $installedPythonw $coreArguments $appRoot
        New-StartupCommand $trayStartupLink $installedPythonw $trayArguments $appRoot
    } else {
        $installedXmlTool = Join-Path $appRoot "tools\create_task_scheduler_xml.py"
        $coreXmlArgs = @($installedXmlTool, "--root", $appRoot, "--data-root", $dataRoot, "--output", $coreXml, "--task-name", $coreTask, "--role", $coreRole)
        if ($UserHost) { $coreXmlArgs += @('--user-id', $interactiveUserSid) }
        $trayXmlArgs = @($installedXmlTool, "--root", $appRoot, "--data-root", $dataRoot, "--output", $trayXml, "--task-name", $trayTask, "--role", "tray", "--user-id", $interactiveUserSid)
        & $installedPython @coreXmlArgs
        & $installedPython @trayXmlArgs
        & "$env:SystemRoot\System32\schtasks.exe" /Create /TN $coreTask /XML $coreXml /F | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Core 排程工作註冊失敗。" }
        & "$env:SystemRoot\System32\schtasks.exe" /Create /TN $trayTask /XML $trayXml /F | Out-Host
        if ($LASTEXITCODE -ne 0) {
            & "$env:SystemRoot\System32\schtasks.exe" /Delete /TN $coreTask /F *> $null
            throw "Tray 排程工作註冊失敗，已回退 Core 工作。"
        }
    }
    New-StartMenuLauncherShortcut $startMenuShortcut $appRoot
    if (-not $SkipStart) {
        $installationPhase = 'start'
        if ($CurrentUser) {
            & $env:ComSpec /d /c ('"{0}"' -f $coreStartupLink)
            $dashboardPort = 8000
            $configPath = Join-Path $dataRoot "config.json"
            if (Test-Path -LiteralPath $configPath) {
                try {
                    $configuredPort = [int]((Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).dashboard.port)
                    if ($configuredPort -ge 1 -and $configuredPort -le 65535) { $dashboardPort = $configuredPort }
                }
                catch { Write-Warning "Dashboard Port 設定無法解析，啟動驗證使用預設 8000。" }
            }
            $healthReady = $false
            for ($attempt = 0; $attempt -lt 60; $attempt++) {
                try {
                    $response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/healthz" -f $dashboardPort) -TimeoutSec 1
                    if ($response.StatusCode -eq 200) { $healthReady = $true; break }
                }
                catch { Start-Sleep -Milliseconds 500 }
            }
            if (-not $healthReady) { throw "Core 未在時限內通過健康檢查，拒絕提前啟動 Tray。" }
            & $env:ComSpec /d /c ('"{0}"' -f $trayStartupLink)
        } else {
            & "$env:SystemRoot\System32\schtasks.exe" /Run /TN $coreTask | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "Core 排程啟動失敗，拒絕啟動 Tray。" }
            $dashboardPort = 8000
            $configPath = Join-Path $dataRoot "config.json"
            if (Test-Path -LiteralPath $configPath) {
                try {
                    $configuredPort = [int]((Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).dashboard.port)
                    if ($configuredPort -ge 1 -and $configuredPort -le 65535) { $dashboardPort = $configuredPort }
                }
                catch { Write-Warning "Dashboard Port 設定無法解析，啟動驗證使用預設 8000。" }
            }
            $healthReady = $false
            for ($attempt = 0; $attempt -lt 60; $attempt++) {
                try {
                    $response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/healthz" -f $dashboardPort) -TimeoutSec 1
                    if ($response.StatusCode -eq 200) { $healthReady = $true; break }
                }
                catch { Start-Sleep -Milliseconds 500 }
            }
            if (-not $healthReady) { throw "Core 未在時限內通過健康檢查，拒絕啟動 Tray。" }
            & "$env:SystemRoot\System32\schtasks.exe" /Run /TN $trayTask | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "Tray 排程啟動失敗。" }
        }
        $verificationTool = Join-Path $appRoot "tools\verify_windows_installation.py"
        $installationPhase = 'health'
        $verificationArgs = @($verificationTool, "--root", $appRoot, "--data-root", $dataRoot, "--wait-seconds", "45", "--core-task-name", $coreTask, "--tray-task-name", $trayTask)
        if ($CurrentUser) { $verificationArgs += @("--startup-link", $coreStartupLink, "--startup-link", $trayStartupLink) }
        & $installedPython @verificationArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "安裝已完成，但 Host／Tray 即時健康驗證未通過。請保留資料並查看 Dashboard 日誌。"
            Write-InstallOutcome $dataRoot 'health_failed' $installationPhase $packageRoot
            exit 3
        }
    }
    if ($upgradeBackupApp -and (Test-Path -LiteralPath $upgradeBackupApp)) {
        try {
            Remove-Item -LiteralPath $upgradeBackupApp -Recurse -Force -ErrorAction Stop
            Write-Host "已清理封存的舊 App。"
            $upgradeBackupApp = $null
        }
        catch {
            Write-Warning "新版已安裝；舊版備份因檔案占用未能清理，已保留供復原：$upgradeBackupApp"
        }
    }
    Write-Host "✅ 喵喵守護者+ 已安裝。應用：$appRoot"
    Write-Host "ℹ️ 可從 Windows 開始功能表「MeowKeeperPlus」資料夾點選「喵喵守護者+」重新啟動。"
    $installationPhase = 'complete'
    Write-InstallOutcome $dataRoot 'passed' $installationPhase $packageRoot
    Write-Host "✅ 資料已與應用分離：$dataRoot"
    if ($CurrentUser) {
        Write-Host "ℹ️ 目前使用者模式會在登入 Windows 後啟動，不等同於開機前的 24H SYSTEM 服務。"
    } else {
        Write-Host "⚠️ 若 Core 以 SYSTEM 執行，請從該 Host 的 Dashboard 安全儲存 Telegram 憑證。"
    }
}
finally {
    # The outer catch still needs the migration journal to restore moved data.
    # Never delete it before rollback has had a chance to run.
}
}
catch {
    if ($upgradeBackupApp -and (Test-Path -LiteralPath $upgradeBackupApp) -and -not $upgradeNewAppVerified) {
        try {
            if ($legacyDataMigrationJournal -and (Test-Path -LiteralPath $legacyDataMigrationJournal -PathType Leaf)) {
                Write-Host "新版尚未完成驗證；正在把本次移轉的舊資料復原至封存的 App。"
                & $sourcePython $dataMigrationTool --mode rollback --journal $legacyDataMigrationJournal
                if ($LASTEXITCODE -ne 0) { throw "舊版資料自動復原未通過；已保留 App 封存目錄與資料，請勿刪除。" }
            }
            if (Test-Path -LiteralPath $appRoot) {
                $failedLeaf = "App.failed-upgrade-$([guid]::NewGuid().ToString('N'))"
                $failedPath = [IO.Path]::GetFullPath((Join-Path $installRoot $failedLeaf))
                if ([IO.Path]::GetDirectoryName($failedPath) -ne [IO.Path]::GetFullPath($installRoot)) {
                    throw "失敗版本封存路徑驗證失敗。"
                }
                Move-Item -LiteralPath $appRoot -Destination $failedPath -ErrorAction Stop
            }
            Move-Item -LiteralPath $upgradeBackupApp -Destination $appRoot -ErrorAction Stop
            Write-Warning "新版複製／校驗未完成，已恢復舊 App；資料已回復舊路徑或保留在獨立 Data。"
            $upgradeBackupApp = $null
        }
        catch {
            Write-Warning "自動回復舊 App 失敗；請保留並手動檢查封存目錄：$upgradeBackupApp"
        }
    }
    if (-not $WhatIf -and (Get-Variable installationPhase -ErrorAction SilentlyContinue)) {
        Write-InstallOutcome $dataRoot 'failed' $installationPhase $packageRoot
    }
    throw
}
finally {
    if ((Get-Variable installationPhase -ErrorAction SilentlyContinue) -and $installationPhase -eq 'complete' -and
        (Get-Variable tempRoot -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $tempRoot)) {
        $resolvedTemp = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $tempRoot).Path)
        $expectedTempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
        if ([IO.Path]::GetDirectoryName($resolvedTemp) -eq $expectedTempParent -and
            [IO.Path]::GetFileName($resolvedTemp) -match '^MeowKeeperPlus-install-[a-f0-9]{32}$' -and
            -not ((Get-Item -LiteralPath $resolvedTemp -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
        }
    }
    if ($null -ne $installerConsoleState) {
        [void][MeowKeeperInstallerConsole]::SetConsoleMode($installerConsoleState.Handle, $installerConsoleState.OriginalMode)
    }
}
