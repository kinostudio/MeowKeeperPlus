param()

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonExe = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
$probe = Join-Path $repositoryRoot 'tools\probe_c701_jpeg.py'
$go2rtcExe = 'C:\ProgramData\MeowKeeperPlus\App\vendor\go2rtc\v1.9.14\go2rtc.exe'
$ruleName = 'MeowKeeperPlus C701 UDP temporary'
$reportDirectory = Join-Path $repositoryRoot 'reports\c701-jpeg'

if (-not (Test-Path -LiteralPath $pythonExe) -or -not (Test-Path -LiteralPath $go2rtcExe)) {
    throw '找不到專案 Python 或已安裝 go2rtc；未變更防火牆。'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '請以系統管理員 PowerShell 執行此單次測試；未變更防火牆。'
}
if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
    throw '同名防火牆規則已存在；為避免移除既有規則，測試未啟動。'
}

$created = $false
try {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
        -Enabled True -Profile Public -Protocol UDP -Program $go2rtcExe `
        -RemoteAddress '192.168.18.62' | Out-Null
    $created = $true
    Write-Host '已建立限縮的臨時 UDP 規則；開始擷取 C701 JPEG。'
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    $reportPath = Join-Path $reportDirectory ("probe-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
    & $pythonExe $probe | Tee-Object -FilePath $reportPath
    $probeExitCode = $LASTEXITCODE
    Write-Host "C701 探測結束碼：$probeExitCode"
    Write-Host "安全化結果：$reportPath"
}
finally {
    if ($created) {
        Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction Stop
        Write-Host '已移除 C701 臨時 UDP 規則。'
    }
}
