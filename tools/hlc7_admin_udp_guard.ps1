[CmdletBinding()]
param(
    [string]$CameraAddress = "192.168.31.56",
    [string]$Go2RtcPath = "C:\ProgramData\MeowKeeperPlus\App\vendor\go2rtc\v1.9.14\go2rtc.exe",
    [string]$SignalDirectory = "C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\reports\phase3c7\admin-udp"
)

$ErrorActionPreference = "Stop"
$ruleName = "MeowKeeperPlus HLC7 UDP temporary diagnostic"
$readyPath = Join-Path $SignalDirectory "ready.json"
$donePath = Join-Path $SignalDirectory "done.flag"
$resultPath = Join-Path $SignalDirectory "result.json"

New-Item -ItemType Directory -Path $SignalDirectory -Force | Out-Null
Remove-Item -LiteralPath $readyPath, $donePath, $resultPath -Force -ErrorAction SilentlyContinue

function Write-Status([hashtable]$Value) {
    $Value.generated_at = (Get-Date).ToUniversalTime().ToString("o")
    $Value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultPath -Encoding UTF8
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Status @{ status = "not_admin"; user = $identity.Name; rule_created = $false }
    exit 740
}

if (-not (Test-Path -LiteralPath $Go2RtcPath -PathType Leaf)) {
    Write-Status @{ status = "missing_go2rtc"; rule_created = $false }
    exit 2
}

$created = $false
try {
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $ruleName `
        -Direction Inbound -Action Allow -Program $Go2RtcPath `
        -Protocol UDP -RemoteAddress $CameraAddress -Profile Any `
        -Description "Temporary MeowKeeper+ HLC7 diagnostic; auto-removed." | Out-Null
    $created = $true
    @{ status = "ready"; rule_created = $true; rule_name = $ruleName; remote_address = $CameraAddress; executable = $Go2RtcPath } |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $readyPath -Encoding UTF8

    $deadline = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $donePath)) {
        Start-Sleep -Seconds 1
    }
    Write-Status @{ status = if (Test-Path -LiteralPath $donePath) { "test_signalled" } else { "timeout" }; rule_created = $true }
}
catch {
    Write-Status @{ status = "error"; rule_created = $created; error = $_.Exception.Message }
    exit 1
}
finally {
    if ($created) {
        Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    }
    Write-Status @{ status = "removed"; rule_created = $false; rule_name = $ruleName }
}
