param(
    [ValidateSet('Diagnose', 'Install')][string]$Mode = 'Diagnose',
    [switch]$Consent,
    [switch]$NoElevation
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$expectedHash = '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032'
$officialUrl = 'https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe'
function Get-PawnIoState {
    $version = $null
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
        try {
            $key = $base.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO')
            if ($null -ne $key) {
                try { $version = [string]$key.GetValue('DisplayVersion') } finally { $key.Dispose() }
            }
        } finally { $base.Dispose() }
    }
    $parsed = $null
    $compatible = -not [string]::IsNullOrWhiteSpace($version) -and [version]::TryParse($version, [ref]$parsed) -and $parsed -ge [version]'2.2.0'
    $service = Get-Service -Name PawnIO -ErrorAction SilentlyContinue
    return [pscustomobject]@{ compatible = $compatible; version = $version; driver_present = ($null -ne $service) }
}
$state = Get-PawnIoState
if ($Mode -eq 'Diagnose') {
    [pscustomobject]@{ status = if ($state.compatible -and $state.driver_present) { 'driver_present' } else { 'driver_missing_or_incompatible' }; driver = $state; temperature_verified = $false } | ConvertTo-Json -Depth 3
    exit 0
}
if (-not $Consent) {
    Add-Type -AssemblyName System.Windows.Forms
    $choice = [System.Windows.Forms.MessageBox]::Show(
        "啟用 CPU 溫度監測需要官方 PawnIO 2.2.0 核心驅動，以及僅讀 CPU 的受限 SYSTEM 測溫工作。Host 原帳號不變；不開網路埠、不接收任意指令、不放寬驅動存取權。程式會從官方 GitHub 下載並核對簽章與 SHA-256；不自動重開機。`n`n官方安裝器若顯示授權條款，請親自確認。不同意不影響攝影機、錄影及通知。是否繼續？",
        '喵喵守護者+｜CPU 溫度監測', 'YesNo', 'Question', 'Button1')
    if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) { exit 1223 }
}
$administrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $administrator) {
    if ($NoElevation) { throw '需要管理員授權；沒有修改驅動或權限。' }
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath), '-Mode', 'Install', '-Consent', '-NoElevation')
    try {
        $elevated = Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -PassThru -Wait
        exit $elevated.ExitCode
    } catch [System.ComponentModel.Win32Exception] { exit 1223 }
}
if ($state.compatible -and $state.driver_present) {
    Write-Host '已存在相容測溫驅動，不重複安裝。實際溫度請以 Host 感測狀態確認。'
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Manage-CpuSensorTask.ps1') -Mode Install
    exit $LASTEXITCODE
}
$work = Join-Path ([IO.Path]::GetTempPath()) ('MeowKeeperPlus-PawnIO-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work -ErrorAction Stop | Out-Null
$installer = Join-Path $work 'PawnIO_setup.exe'
Write-Host '正在從官方來源下載 CPU 測溫驅動；不影響攝影機監控。'
Invoke-WebRequest -Uri $officialUrl -OutFile $installer -UseBasicParsing -TimeoutSec 60
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw '官方驅動雜湊不符；未執行。請檢查版本，不可略過驗證。'
}
$signature = Get-AuthenticodeSignature -LiteralPath $installer
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne 'F380DCC9F706E2756A5047B832FFE719E1BC35F5') {
    throw '官方驅動簽章驗證失敗；未執行。'
}
# Keep the official interactive installer for any license/edition confirmation.
# Do not use unrestricted mode, ACL changes, silent EULA acceptance or reboot.
Write-Host '請完成官方 PawnIO 安裝視窗；只選官方受限版，不選 Unrestricted。'
$process = Start-Process -FilePath $installer -ArgumentList '-install' -PassThru
try {
    if (-not $process.WaitForExit(300000)) { throw '驅動安裝尚未完成；沒有強制關閉安裝器，請查看官方視窗。' }
    if ($process.ExitCode -notin @(0, 3010)) { throw ('驅動安裝未成功，錯誤碼：' + $process.ExitCode) }
    if ($process.ExitCode -eq 3010) { Write-Warning '官方驅動要求重新開機；本程式不會自動重開機。'; exit 3010 }
} finally { $process.Dispose() }
$state = Get-PawnIoState
if (-not ($state.compatible -and $state.driver_present)) { throw '安裝結束但尚未偵測到相容驅動；CPU 溫度未驗收。' }
Write-Host '測溫驅動已就緒；不代表溫度已取得，請等待 Host 回報有效的新鮮讀值。'
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'Manage-CpuSensorTask.ps1') -Mode Install
exit $LASTEXITCODE
