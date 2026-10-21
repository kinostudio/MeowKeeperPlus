param(
    [ValidateRange(1024,65535)][int]$Port = 18981,
    [switch]$NoOpenBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '找不到專案開發用 Python；這不是正式版安裝器，未建立環境或下載套件。'
}
$dataRoot = Join-Path $projectRoot 'data\development-host'
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$url = 'http://127.0.0.1:{0}' -f $Port
function Test-HostReady {
    try {
        $result = Invoke-RestMethod -Uri "$url/healthz" -TimeoutSec 2
        return $result.status -eq 'ok' -and $result.service -eq 'MeowKeeper+'
    } catch { return $false }
}
if (-not (Test-HostReady)) {
    $arguments = @((Join-Path $projectRoot 'tools\run_host.py'),
        '--port', "$Port", '--dev', '--disable-monitor', '--no-lan-access', '--no-open-browser',
        '--dashboard-config', (Join-Path $dataRoot 'config.json'),
        '--database-path', (Join-Path $dataRoot 'meowkeeper.db'),
        '--event-directory', (Join-Path $dataRoot 'events'),
        '--ring-directory', (Join-Path $dataRoot 'cache\ring'),
        '--preview-directory', (Join-Path $dataRoot 'cache\preview'),
        '--admin-storage-path', (Join-Path $dataRoot 'web_admin.json'),
        '--lock-root', $dataRoot, '--go2rtc-api-port', '21986', '--go2rtc-rtsp-port', '28557',
        '--source-url-env', 'MEOWKEEPER_DEVELOPMENT_UNUSED_SOURCE')
    $quoted = @($arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $savedLocalData = $env:LOCALAPPDATA
    try {
        $env:LOCALAPPDATA = Join-Path $dataRoot 'localappdata'
        $child = Start-Process -FilePath $python -ArgumentList $quoted -WorkingDirectory $projectRoot `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataRoot 'host.stdout.log') `
            -RedirectStandardError (Join-Path $dataRoot 'host.stderr.log')
    } finally { $env:LOCALAPPDATA = $savedLocalData }
    $ready = $false
    foreach ($attempt in 1..30) {
        if (Test-HostReady) { $ready = $true; break }
        if ($child.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "開發 Host 尚未就緒，請查看 $dataRoot\host.stderr.log；未重啟或修改正式服務。" }
}
Write-Host "開發 Host 已就緒：$url/dashboard"
Write-Host "資料獨立保存於 $dataRoot；啟動時監控為停止，未安裝或升級正式服務。"
if (-not $NoOpenBrowser) { Start-Process "$url/dashboard" }
