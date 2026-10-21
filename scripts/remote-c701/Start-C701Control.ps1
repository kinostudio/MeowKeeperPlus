param([switch]$Refresh)

$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $packageRoot 'runtime\python.exe'
$agent = Join-Path $packageRoot 'scripts\relay_control_agent.py'
$ffmpeg = Join-Path $packageRoot 'ffmpeg.exe'
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
$pidPath = Join-Path $stateDir 'control.pid'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $agent) -or
    -not (Test-Path -LiteralPath $ffmpeg) -or -not (Test-Path -LiteralPath (Join-Path $stateDir 'control-credential.dpapi'))) {
    throw '遠端控制元件或配對憑證尚未準備好。'
}
$tailIp = (Get-Content -LiteralPath (Join-Path $stateDir 'tailscale-ip.txt') -Raw).Trim()
$listeners = @(Get-NetTCPConnection -LocalAddress $tailIp -LocalPort 28555 -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw '遠端控制埠擁有者不唯一。' }
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
    if (-not $owner -or $owner.ExecutablePath -ine (Resolve-Path -LiteralPath $python).Path -or
        -not $owner.CommandLine -or -not $owner.CommandLine.Contains($agent)) {
        throw '遠端控制埠已由其他程序占用；未啟動第二個控制程序。'
    }
    [IO.File]::WriteAllText($pidPath, [string]$owners[0], [Text.Encoding]::ASCII)
    if (-not $Refresh) { return }
    Stop-Process -Id $owners[0] -Force
    Start-Sleep -Milliseconds 300
}
$arguments = @(('"' + $agent + '"'), '--state-dir', ('"' + $stateDir + '"'), '--ffmpeg', ('"' + $ffmpeg + '"'))
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $stateDir -WindowStyle Hidden -PassThru
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) { throw '遠端控制程序啟動失敗；未開放控制埠。' }
    $ready = @(Get-NetTCPConnection -LocalAddress $tailIp -LocalPort 28555 -State Listen -ErrorAction SilentlyContinue)
    if ($ready.Count -eq 1 -and $ready[0].OwningProcess -eq $process.Id) {
        [IO.File]::WriteAllText($pidPath, [string]$process.Id, [Text.Encoding]::ASCII)
        return
    }
}
Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
throw '遠端控制程序未能在五秒內監聽 Tailscale 控制埠。'
