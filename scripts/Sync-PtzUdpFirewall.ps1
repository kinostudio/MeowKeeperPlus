param(
    [Parameter(Mandatory=$true)][string]$AppRoot,
    [ValidateSet('Install','Remove')][string]$Mode = 'Install'
)
$ErrorActionPreference = 'Stop'
$group = 'MeowKeeperPlus.PtzUdp.v1'
$root = [IO.Path]::GetFullPath($AppRoot)
$official = Join-Path $root 'vendor\go2rtc\v1.9.14\go2rtc.exe'
$target = Join-Path $root 'vendor\go2rtc\ptz-feedback\go2rtc.exe'
function Test-PrivateAddress([string]$value) {
    $ip = $null
    if (-not [Net.IPAddress]::TryParse($value, [ref]$ip) -or $ip.AddressFamily -ne 'InterNetwork') { return $false }
    $b = $ip.GetAddressBytes()
    return $b[0] -eq 10 -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168)
}
function Get-Identity([string]$sourceName) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return 'MeowKeeperPlus-PTZ-UDP-' + [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($target.ToLowerInvariant() + '|' + $sourceName))).Replace('-','').Substring(0,16) }
    finally { $sha.Dispose() }
}
function Test-OwnedRule($rule) {
    $app = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule
    $port = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule
    $addr = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule
    return $rule.Group -eq $group -and $rule.Name -match '^MeowKeeperPlus-PTZ-UDP-[0-9A-F]{16}$' -and
        $app.Program -ieq $target -and $port.Protocol -eq 'UDP' -and $rule.Direction -eq 'Inbound' -and
        $rule.Action -eq 'Allow' -and $rule.Description -like "$group;source=*" -and
        @($addr.RemoteAddress).Count -gt 0 -and @($addr.RemoteAddress | Where-Object { -not (Test-PrivateAddress $_) }).Count -eq 0
}
try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '需要管理員權限。' }
    $owned = @(Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue)
    foreach ($rule in $owned) { if (-not (Test-OwnedRule $rule)) { throw '既有 PTZ 規則身分不符，未修改。' } }
    if ($Mode -eq 'Remove') {
        foreach ($rule in $owned) { Remove-NetFirewallRule -Name $rule.Name }
        Write-Host ('已移除 MeowKeeper+ 自建 PTZ UDP 規則：{0} 條。' -f $owned.Count)
        exit 0
    }
    if (-not (Test-Path -LiteralPath (Join-Path $root 'vendor\go2rtc\backend.json'))) { exit 0 }
    $selection = Get-Content -Raw -LiteralPath (Join-Path $root 'vendor\go2rtc\backend.json') | ConvertFrom-Json
    if ($selection.backend -ne 'ptz-feedback') { exit 0 }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne '3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3') { throw 'PTZ 執行檔校驗失敗。' }
    $count = 0
    foreach ($rule in @(Get-NetFirewallRule | Where-Object { $_.Name -like 'MeowKeeper*' -and $_.Group -ne $group })) {
        $app = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule
        $port = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule
        $addr = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule
        $iface = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $rule
        $ifaceType = Get-NetFirewallInterfaceTypeFilter -AssociatedNetFirewallRule $rule
        $service = Get-NetFirewallServiceFilter -AssociatedNetFirewallRule $rule
        if ($app.Program -ine $official -or $port.Protocol -ne 'UDP' -or $rule.Direction -ne 'Inbound' -or
            $rule.Action -ne 'Allow' -or $rule.Enabled -ne 'True' -or [string]$rule.Profile -eq 'Any') { continue }
        if ($service.Service -ne 'Any') { continue }
        if (@($addr.RemoteAddress).Count -eq 0 -or @($addr.RemoteAddress | Where-Object { -not (Test-PrivateAddress $_) }).Count) { continue }
        $name = Get-Identity $rule.Name
        $existing = @(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)
        if ($existing.Count) {
            if ($existing.Count -ne 1 -or -not (Test-OwnedRule $existing[0])) { throw 'PTZ 防火牆規則名稱衝突。' }
            # Recreate only our verified rule so the exact source scope remains current.
            Remove-NetFirewallRule -Name $name
        }
        New-NetFirewallRule -Name $name -DisplayName 'MeowKeeper+ Xiaomi PTZ UDP' -Group $group `
            -Description "$group;source=$($rule.Name)" -Program $target -Direction Inbound -Action Allow `
            -Enabled True -Profile $rule.Profile -Protocol UDP -LocalAddress $addr.LocalAddress `
            -RemoteAddress $addr.RemoteAddress -LocalPort $port.LocalPort -RemotePort $port.RemotePort `
            -InterfaceAlias $iface.InterfaceAlias -InterfaceType $ifaceType.InterfaceType | Out-Null
        $check = Get-NetFirewallRule -Name $name
        if (-not (Test-OwnedRule $check)) { throw '新 PTZ 規則範圍驗證失敗。' }
        $count++
    }
    Write-Host ('已沿用 {0} 條既有逐攝影機 UDP 規則；原規則及官方版本未修改。' -f $count)
    if (-not $count) { Write-Warning '未找到已核准的逐攝影機 UDP 規則；請先在 Dashboard 診斷防火牆，不會開放整個區網。' }
} catch {
    Write-Error ('PTZ 防火牆處理失敗：' + $_.Exception.GetType().Name)
    exit 1
}
