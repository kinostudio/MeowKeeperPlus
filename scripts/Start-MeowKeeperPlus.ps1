$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot ".." )).Path
if ($args.Count -eq 0) {
    $installedRoot = Join-Path $env:ProgramData 'MeowKeeperPlus'
    $installedApp = Join-Path $installedRoot 'App'
    if (Test-Path -LiteralPath (Join-Path $installedApp 'build_manifest.json') -PathType Leaf) {
        $port = 8000
        $configPath = Join-Path $installedRoot 'Data\config.json'
        if (Test-Path -LiteralPath $configPath -PathType Leaf) {
            try {
                $configuredPort = [int]((Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).dashboard.port)
                if ($configuredPort -ge 1 -and $configuredPort -le 65535) { $port = $configuredPort }
            } catch { Write-Warning 'Dashboard Port 設定無法讀取，使用預設 8000 驗證。' }
        }
        $url = 'http://127.0.0.1:{0}/dashboard' -f $port
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:{0}/healthz' -f $port) -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                Start-Process $url
                exit 0
            }
        } catch { }
        Write-Error '已安裝 SYSTEM 版 MeowKeeper+，但 Dashboard 未回應；不啟動開發版 Host／Tray，避免重複實例。'
        exit 3
    }
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error "找不到專案 .venv runtime：$python"
    exit 2
}
Set-Location -LiteralPath $root
& $python (Join-Path $root "tools\launcher.py") --root $root @args
exit $LASTEXITCODE
