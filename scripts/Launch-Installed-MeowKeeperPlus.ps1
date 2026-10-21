$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Show-LaunchError([string]$Message) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($Message, 0, '喵喵守護者+', 0x10)
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
    }
    catch {
        Write-Error $Message
    }
}

function Get-DashboardUrl([string]$DataRoot) {
    $port = 8000
    $configPath = Join-Path $DataRoot 'config.json'
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        try {
            $configuredPort = [int]((Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).dashboard.port)
            if ($configuredPort -ge 1 -and $configuredPort -le 65535) { $port = $configuredPort }
        }
        catch { }
    }
    return "http://127.0.0.1:$port"
}

function Wait-Dashboard([string]$BaseUrl) {
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/healthz" -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        }
        catch { Start-Sleep -Milliseconds 500 }
    }
    return $false
}

function Start-CurrentUserCommand([string]$CommandPath, [string]$Role, [string]$AppRoot, [string]$DataRoot, [string]$InstallRoot) {
    if (-not (Test-Path -LiteralPath $CommandPath -PathType Leaf)) {
        throw '目前使用者安裝的啟動入口不完整；請以 MeowKeeper+ 安裝包修復安裝。'
    }
    $pythonw = Join-Path $AppRoot 'runtime\python\pythonw.exe'
    if ($Role -eq 'core') {
        $arguments = ('"{0}" --no-open-browser --source-url-env MEOWKEEPER_MEDIA_SOURCE_URL --analyzer auto --dashboard-config "{1}" --database-path "{2}" --event-directory "{3}" --ring-directory "{4}" --preview-directory "{5}" --admin-storage-path "{6}" --lock-root "{7}"' -f (Join-Path $AppRoot 'tools\run_host.py'), (Join-Path $DataRoot 'config.json'), (Join-Path $DataRoot 'meowkeeper.db'), (Join-Path $DataRoot 'events'), (Join-Path $DataRoot 'cache\ring'), (Join-Path $DataRoot 'cache\preview'), (Join-Path $DataRoot 'web_admin.json'), $InstallRoot)
    }
    else {
        $arguments = ('"{0}" --background --lock-root "{1}"' -f (Join-Path $AppRoot 'tools\run_tray.py'), $InstallRoot)
    }
    $expected = "@echo off`r`nset PYTHONDONTWRITEBYTECODE=1`r`ncd /d `"$AppRoot`" || exit /b 2`r`nstart `"`" /b `"$pythonw`" $arguments`r`n"
    $actual = [IO.File]::ReadAllText($CommandPath, [Text.Encoding]::UTF8)
    if (-not [StringComparer]::Ordinal.Equals($actual, $expected)) {
        throw '啟動捷徑內容與此安裝不符；為安全起見沒有執行。請使用安裝包修復安裝。'
    }
    $comspec = Join-Path $env:SystemRoot 'System32\cmd.exe'
    & $comspec /d /c ('"{0}"' -f $CommandPath)
    if ($LASTEXITCODE -ne 0) { throw '啟動背景元件時發生錯誤；請稍後再試。' }
}

function Assert-SystemTaskTarget([string]$TaskName, [string]$Role, [string]$AppRoot) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    $actions = @($task.Actions)
    $expectedPython = Join-Path $AppRoot 'runtime\python\python.exe'
    $expectedPythonw = Join-Path $AppRoot 'runtime\python\pythonw.exe'
    $expectedExecutable = if ($Role -eq 'core') { $expectedPython } else { $expectedPythonw }
    $expectedScript = if ($Role -eq 'core') { Join-Path $AppRoot 'tools\run_host.py' } else { Join-Path $AppRoot 'tools\run_tray.py' }
    if ($actions.Count -ne 1 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($actions[0].Execute), [IO.Path]::GetFullPath($expectedExecutable)) -or
        -not $actions[0].Arguments.StartsWith(('"{0}"' -f $expectedScript), [StringComparison]::OrdinalIgnoreCase)) {
        throw "已註冊的「$TaskName」工作與此安裝不符；為安全起見沒有啟動。"
    }
}

function Get-SystemTaskDataRoot([string]$AppRoot, [string]$InstallRoot) {
    $task = Get-ScheduledTask -TaskName 'MeowKeeperPlus Core' -ErrorAction Stop
    $actions = @($task.Actions)
    $expectedPython = Join-Path $AppRoot 'runtime\python\python.exe'
    $expectedScript = Join-Path $AppRoot 'tools\run_host.py'
    if ($actions.Count -ne 1 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($actions[0].Execute), [IO.Path]::GetFullPath($expectedPython)) -or
        -not $actions[0].Arguments.StartsWith(('"{0}"' -f $expectedScript), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Core 啟動工作與目前安裝不符；無法安全判斷資料目錄，請先修復安裝。'
    }

    $eventMatch = [regex]::Match([string]$actions[0].Arguments, '(?:^|\s)--event-directory\s+"([^"]+)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($eventMatch.Success) {
        $eventsPath = [IO.Path]::GetFullPath($eventMatch.Groups[1].Value)
        $canonicalEvents = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'Data\events'))
        $legacyEvents = [IO.Path]::GetFullPath((Join-Path $AppRoot 'data\events'))
        if ([StringComparer]::OrdinalIgnoreCase.Equals($eventsPath, $canonicalEvents)) { return (Join-Path $InstallRoot 'Data') }
        if ([StringComparer]::OrdinalIgnoreCase.Equals($eventsPath, $legacyEvents)) { return (Join-Path $AppRoot 'data') }
        throw 'Core 工作指定了非預期的事件資料目錄；為避免開錯資料，啟動已停止。'
    }

    $configMatch = [regex]::Match([string]$actions[0].Arguments, '(?:^|\s)--dashboard-config\s+"([^"]+)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($configMatch.Success) {
        $configPath = [IO.Path]::GetFullPath($configMatch.Groups[1].Value)
        $canonicalConfig = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'Data\config.json'))
        $legacyConfig = [IO.Path]::GetFullPath((Join-Path $AppRoot 'data\config.json'))
        if ([StringComparer]::OrdinalIgnoreCase.Equals($configPath, $canonicalConfig)) { return (Join-Path $InstallRoot 'Data') }
        if ([StringComparer]::OrdinalIgnoreCase.Equals($configPath, $legacyConfig)) { return (Join-Path $AppRoot 'data') }
        throw 'Core 工作指定了非預期的設定檔位置；為避免開錯資料，啟動已停止。'
    }

    # Before explicit path arguments were added, installed run_host.py used
    # its working directory as ROOT, which resolved persistent files to App\data.
    return (Join-Path $AppRoot 'data')
}

try {
    $systemRoot = Join-Path $env:ProgramData 'MeowKeeperPlus'
    $systemApp = Join-Path $systemRoot 'App'
    $systemData = Join-Path $systemRoot 'Data'
    $userRoot = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlus'
    $userApp = Join-Path $userRoot 'App'
    $userData = Join-Path $userRoot 'Data'

    if (Test-Path -LiteralPath (Join-Path $systemApp 'build_manifest.json') -PathType Leaf) {
        $appRoot = $systemApp
        $installRoot = $systemRoot
        $systemInstall = $true
    }
    elseif (Test-Path -LiteralPath (Join-Path $userApp 'build_manifest.json') -PathType Leaf) {
        $appRoot = $userApp
        $dataRoot = $userData
        $installRoot = $userRoot
        $systemInstall = $false
    }
    else {
        throw '找不到已安裝的喵喵守護者+；請先完成安裝。'
    }

    $appItem = Get-Item -LiteralPath $appRoot -Force
    if ($appItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw '安裝目錄安全檢查未通過；沒有啟動程式。'
    }
    $pythonw = Join-Path $appRoot 'runtime\python\pythonw.exe'
    $trayScript = Join-Path $appRoot 'tools\run_tray.py'
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf) -or -not (Test-Path -LiteralPath $trayScript -PathType Leaf)) {
        throw '安裝檔案不完整；請使用安裝包修復安裝。'
    }

    if ($systemInstall) {
        $dataRoot = Get-SystemTaskDataRoot $appRoot $installRoot
    }

    $baseUrl = Get-DashboardUrl $dataRoot
    if (-not (Wait-Dashboard $baseUrl)) {
        if ($systemInstall) {
            $scheduler = Join-Path $env:SystemRoot 'System32\schtasks.exe'
            Assert-SystemTaskTarget 'MeowKeeperPlus Core' 'core' $appRoot
            & $scheduler /Run /TN 'MeowKeeperPlus Core' *> $null
            if ($LASTEXITCODE -ne 0) { throw '無法啟動背景監控核心；請透過安裝版的「啟動喵喵守護者+」修復入口重試。' }
        }
        else {
            $startup = [Environment]::GetFolderPath('Startup')
            Start-CurrentUserCommand (Join-Path $startup 'MeowKeeperPlus User Core.cmd') 'core' $appRoot $dataRoot $installRoot
        }
        if (-not (Wait-Dashboard $baseUrl)) { throw '背景核心沒有在時限內啟動；請檢查工作排程器或稍後重試。' }
    }

    if ($systemInstall) {
        $scheduler = Join-Path $env:SystemRoot 'System32\schtasks.exe'
        Assert-SystemTaskTarget 'MeowKeeperPlus Tray' 'tray' $appRoot
        & $scheduler /Run /TN 'MeowKeeperPlus Tray' *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Dashboard 已啟動，但 Tray 圖示未能啟動；請再按一次此入口。' }
    }
    else {
        $startup = [Environment]::GetFolderPath('Startup')
        Start-CurrentUserCommand (Join-Path $startup 'MeowKeeperPlus User Tray.cmd') 'tray' $appRoot $dataRoot $installRoot
    }

    Start-Process $baseUrl
}
catch {
    Show-LaunchError $_.Exception.Message
    exit 1
}
