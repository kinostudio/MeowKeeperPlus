param()

$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$expectedExe = (Resolve-Path -LiteralPath (Join-Path $packageRoot 'go2rtc.exe')).Path
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$lockPath = Join-Path $stateDir 'watch.lock'
$stopPath = Join-Path $stateDir 'watch.stop'
$pidPath = Join-Path $stateDir 'go2rtc.pid'
$configPath = Join-Path $stateDir 'config.yaml'
try {
    $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch [IO.IOException] {
    exit 0 # Another watcher owns the cross-process lock.
}
try {
    Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue
    while (-not (Test-Path -LiteralPath $stopPath)) {
        $healthy = $false
        if (Test-Path -LiteralPath $pidPath) {
            $relayPid = 0
            if ([int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$relayPid)) {
                $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$relayPid" -ErrorAction SilentlyContinue
                if ($owner -and $owner.ExecutablePath -ieq $expectedExe -and
                    $owner.CommandLine -and $owner.CommandLine.Contains($configPath)) {
                    try {
                        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:21984/api' -UseBasicParsing -TimeoutSec 3
                        $healthy = $response.StatusCode -eq 200
                    } catch { }
                }
            }
        }
        if (-not $healthy) {
            try {
                & (Join-Path $PSScriptRoot 'Start-C701Relay.ps1') -NoBrowser *> $null
            } catch { }
        }
        if (Test-Path -LiteralPath (Join-Path $stateDir 'control-credential.dpapi')) {
            try { & (Join-Path $PSScriptRoot 'Start-C701Control.ps1') *> $null } catch { }
        }
        Start-Sleep -Seconds 15
    }
} finally {
    $lock.Dispose()
}
