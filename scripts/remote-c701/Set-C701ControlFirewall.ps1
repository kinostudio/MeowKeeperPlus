param(
    [ValidateSet('Install', 'Status', 'Remove')][string]$Mode = 'Status',
    [string]$HostTailnetIp
)

$ErrorActionPreference = 'Stop'
$ruleName = 'MeowKeeperPlus-C701-Control-Tailnet'
$ruleGroup = 'MeowKeeperPlus-C701-Relay'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = (Resolve-Path -LiteralPath (Join-Path $packageRoot 'runtime\python.exe')).Path
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
$localIp = (Get-Content -LiteralPath (Join-Path $stateDir 'tailscale-ip.txt') -Raw).Trim()
$tailnet = [Net.IPAddress]::Parse($localIp).GetAddressBytes()
if ($tailnet.Length -ne 4 -or $tailnet[0] -ne 100 -or $tailnet[1] -lt 64 -or $tailnet[1] -ge 128) {
    throw '本機 Tailscale 位址不符合 100.64.0.0/10。'
}
if ($Mode -eq 'Install') {
    $remote = [Net.IPAddress]::Parse($HostTailnetIp).GetAddressBytes()
    if ($remote.Length -ne 4 -or $remote[0] -ne 100 -or $remote[1] -lt 64 -or $remote[1] -ge 128) {
        throw 'Host Tailscale 位址不符合 100.64.0.0/10。'
    }
}
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (@($existing).Count -gt 1) { throw '同名防火牆規則不唯一；未變更任何規則。' }
if ($Mode -eq 'Status') {
    if (-not $existing) { Write-Host '遠端控制防火牆規則：尚未建立。'; return }
    $existing | Get-NetFirewallApplicationFilter | Select-Object Program
    $existing | Get-NetFirewallAddressFilter | Select-Object LocalAddress,RemoteAddress
    $existing | Get-NetFirewallPortFilter | Select-Object Protocol,LocalPort
    return
}
if ($existing) {
    $application = $existing | Get-NetFirewallApplicationFilter
    $address = $existing | Get-NetFirewallAddressFilter
    $port = $existing | Get-NetFirewallPortFilter
    if ($existing.Group -ne $ruleGroup -or $application.Program -ine $python -or
        [string]$address.LocalAddress -ne $localIp -or
        $port.Protocol -ne 'TCP' -or [string]$port.LocalPort -ne '28555' -or
        ($Mode -eq 'Install' -and [string]$address.RemoteAddress -ne $HostTailnetIp)) {
        throw '同名規則內容不符；未覆蓋或移除其他規則。'
    }
    if ($Mode -eq 'Remove') { $existing | Remove-NetFirewallRule; Write-Host '已移除此包建立的限縮控制規則。'; return }
    Write-Host '限縮控制規則已存在且內容相符。'
    return
}
if ($Mode -eq 'Remove') { Write-Host '沒有本包的控制規則可移除。'; return }
New-NetFirewallRule -DisplayName $ruleName -Group $ruleGroup -Direction Inbound -Action Allow -Enabled True `
    -Program $python -Protocol TCP -LocalPort 28555 -LocalAddress $localIp `
    -RemoteAddress $HostTailnetIp -Profile Any | Out-Null
Write-Host '已建立限縮控制規則：只允許指定 Host Tailscale IP 連到此 Python 控制程序的 TCP 28555。'
