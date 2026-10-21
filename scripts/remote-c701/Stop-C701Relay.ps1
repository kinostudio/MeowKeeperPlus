param()

$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$expectedExe = (Resolve-Path -LiteralPath (Join-Path $packageRoot 'go2rtc.exe')).Path
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $stateDir 'watch.stop'), 'stop', [Text.Encoding]::ASCII)
$pidPath = Join-Path $stateDir 'go2rtc.pid'
$controlPidPath = Join-Path $stateDir 'control.pid'
if (Test-Path -LiteralPath $controlPidPath) {
    $controlPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $controlPidPath -Raw).Trim(), [ref]$controlPid)) {
        $control = Get-CimInstance Win32_Process -Filter "ProcessId=$controlPid" -ErrorAction SilentlyContinue
        $expectedPython = Join-Path $packageRoot 'runtime\python.exe'
        $expectedAgent = Join-Path $packageRoot 'scripts\relay_control_agent.py'
        if ($control -and $control.ExecutablePath -ieq $expectedPython -and
            $control.CommandLine -and $control.CommandLine.Contains($expectedAgent)) {
            Stop-Process -Id $controlPid -Force
        }
    }
    Remove-Item -LiteralPath $controlPidPath -Force
}
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host '沒有此測試包保存的 go2rtc PID；未停止其他程序。'
    exit 0
}
$relayPid = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$relayPid)) {
    throw 'PID 記錄格式不正確；未停止任何程序。'
}
$process = Get-Process -Id $relayPid -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Host '測試 go2rtc 已不在執行；已清理舊 PID 記錄。'
    exit 0
}
if (-not $process.Path -or $process.Path -ine $expectedExe) {
    throw 'PID 已被其他程序使用或無法核對執行檔；未停止任何程序。'
}
Stop-Process -Id $relayPid -Force
Remove-Item -LiteralPath $pidPath -Force
Write-Host '已停止此隔離測試包的 go2rtc；正式 MeowKeeper+ 不受影響。'
