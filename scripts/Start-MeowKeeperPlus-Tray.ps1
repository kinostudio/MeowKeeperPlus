$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot ".." )).Path
if ($args.Count -eq 0) {
    $installedRoot = Join-Path $env:ProgramData 'MeowKeeperPlus'
    $installedApp = Join-Path $installedRoot 'App'
    if (Test-Path -LiteralPath (Join-Path $installedApp 'build_manifest.json') -PathType Leaf) {
        $installedPythonw = Join-Path $installedApp 'runtime\python\pythonw.exe'
        $installedTray = Join-Path $installedApp 'tools\run_tray.py'
        if (-not (Test-Path -LiteralPath $installedPythonw -PathType Leaf) -or
            -not (Test-Path -LiteralPath $installedTray -PathType Leaf)) {
            Write-Error '已安裝的 MeowKeeper+ Tray 檔案不完整；不啟動開發版 Tray。'
            exit 2
        }
        Start-Process -FilePath $installedPythonw -WorkingDirectory $installedApp -WindowStyle Hidden `
            -ArgumentList @(('"' + $installedTray + '"'), '--background', '--lock-root', ('"' + $installedRoot + '"'))
        exit 0
    }
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error "找不到專案 .venv runtime：$python"
    exit 2
}
Set-Location -LiteralPath $root
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    Write-Error "找不到專案 .venv pythonw runtime：$pythonw"
    exit 2
}
if ($args.Count -gt 0) {
    & $python (Join-Path $root "tools\run_tray.py") @args
    exit $LASTEXITCODE
}
Start-Process -FilePath $pythonw -WorkingDirectory $root -ArgumentList @((Join-Path $root "tools\run_tray.py"), "--no-browser", "--background") -WindowStyle Hidden
exit 0
