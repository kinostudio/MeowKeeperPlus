param(
    [ValidateSet('Inspect','Install','Remove')][string]$Mode = 'Inspect',
    [Parameter(Mandatory=$true)][string]$Program,
    [Parameter(Mandatory=$true)][string]$Peer,
    [Parameter(Mandatory=$true)][string]$LocalAddress
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($address in @($Peer,$LocalAddress)) {
    $ip = [Net.IPAddress]::Parse($address)
    $octets = $ip.GetAddressBytes()
    if ($ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $octets[0] -ne 100 -or $octets[1] -lt 64 -or $octets[1] -gt 127) { throw 'Invalid Tailnet address' }
}
$resolved = (Resolve-Path -LiteralPath $Program).Path
if ([IO.Path]::GetFileName($resolved) -notin @('python.exe','pythonw.exe')) { throw 'Invalid Host executable' }
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("$resolved|$Peer|$LocalAddress")))).Replace('-','').Substring(0,20) } finally { $sha.Dispose() }
$name = "MeowKeeperPlus-XiaomiRelay-$key"
$rule = @(Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)
$verified = $false
if ($rule.Count -eq 1) {
    $app = $rule[0] | Get-NetFirewallApplicationFilter
    $ports = $rule[0] | Get-NetFirewallPortFilter
    $scope = $rule[0] | Get-NetFirewallAddressFilter
    $verified = $rule[0].Group -eq 'MeowKeeperPlus.XiaomiRelay.v1' -and $app.Program -ieq $resolved -and $rule[0].Action -eq 'Allow' -and $rule[0].Direction -eq 'Inbound' -and $rule[0].Enabled -eq 'True' -and @($scope.RemoteAddress).Count -eq 1 -and $scope.RemoteAddress -eq $Peer -and @($scope.LocalAddress).Count -eq 1 -and $scope.LocalAddress -eq $LocalAddress -and $ports.Protocol -eq 'TCP' -and (@($ports.LocalPort | Sort-Object) -join ',') -eq '28554,28555'
    if (-not $verified) { throw 'Existing rule ownership or scope mismatch' }
}
if ($Mode -eq 'Install' -and -not $verified) {
    New-NetFirewallRule -Name $name -DisplayName 'MeowKeeper+ Xiaomi Tailnet Relay' -Group 'MeowKeeperPlus.XiaomiRelay.v1' -Direction Inbound -Action Allow -Enabled True -Profile Any -Program $resolved -Protocol TCP -LocalPort 28554,28555 -LocalAddress $LocalAddress -RemoteAddress $Peer | Out-Null
    $verified = $true
}
if ($Mode -eq 'Remove' -and $verified) { Remove-NetFirewallRule -Name $name; $verified = $false }
@{ verified=$verified; rule_name=$name; mode=$Mode } | ConvertTo-Json -Compress
