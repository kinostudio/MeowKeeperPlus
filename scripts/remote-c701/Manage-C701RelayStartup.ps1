param([ValidateSet('Install', 'Remove', 'Status')][string]$Mode = 'Status')

$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
if (-not $startup) { throw '無法取得目前使用者的 Windows 啟動資料夾。' }
$shortcutPath = Join-Path $startup 'MeowKeeperPlus C701 Relay.lnk'
$watchScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'Watch-C701Relay.ps1')).Path
$powershellExe = Join-Path $PSHOME 'powershell.exe'
$shell = New-Object -ComObject WScript.Shell
$existing = $null
if (Test-Path -LiteralPath $shortcutPath) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.Arguments -notlike "*$watchScript*") {
        throw '同名啟動捷徑不是此 C701 中繼建立的，未修改。'
    }
}
if ($Mode -eq 'Install') {
    $link = $shell.CreateShortcut($shortcutPath)
    $link.TargetPath = $powershellExe
    $link.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchScript + '"'
    $link.WorkingDirectory = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $link.Description = '登入 Windows 後監看 MeowKeeper+ C701 Tailscale 中繼'
    $link.Save()
} elseif ($Mode -eq 'Remove' -and $existing) {
    Remove-Item -LiteralPath $shortcutPath -Force
}
$installed = Test-Path -LiteralPath $shortcutPath
[pscustomobject]@{
    mode = $Mode
    installed_for_current_user = $installed
    runs_before_windows_login = $false
    requires_admin = $false
} | ConvertTo-Json -Compress
