param([Parameter(Mandatory)][string]$CameraIp, [ValidateSet('Status', 'Install', 'Remove')][string]$Mode = 'Status')

# Run only on the Windows PC on C701's LAN. Never print Xiaomi secrets.
$ErrorActionPreference = 'Stop'
$api = 'http://127.0.0.1:21984'
$ruleName = 'MeowKeeperPlus-C701-Go2rtc-UDP-LAN'
$officialHash = '923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C'
$report = [ordered]@{ mode = $Mode; rule_name = $ruleName; stage = 'preflight'; rule_status = 'not_checked'; jpeg_valid = $false }

function Assert-ExactRule($existing, [string]$program, [string]$localIp) {
    if (@($existing).Count -ne 1) { throw '同名防火牆規則數量異常；未更動任何規則。' }
    $one = @($existing)[0]
    $app = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
    $address = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
    $port = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
    if ($one.Direction -ne 'Inbound' -or $one.Action -ne 'Allow' -or
        $one.Enabled -ne 'True' -or $one.Profile -ne 'Public' -or
        $app.Program -ine $program -or $port.Protocol -ne 'UDP' -or
        $address.LocalAddress -ne $localIp -or $address.RemoteAddress -ne $cameraIp) {
        throw '同名規則內容與 C701 限縮條件不符；未覆蓋或刪除。'
    }
}

function Test-Frame {
    $stream = Invoke-RestMethod -Uri "$api/api/streams?src=c701_sd" -TimeoutSec 8
    if ($null -eq $stream) { throw 'c701_sd 尚未建立；防火牆規則已設定，但未進行取流。' }
    if (@($stream.consumers | Where-Object { $null -ne $_ }).Count -gt 0) {
        throw 'c701_sd 已有觀看者；不再建立測試 consumer。'
    }
    $probe = Invoke-WebRequest -Uri "$api/api/streams?src=c701_sd&video=all" -UseBasicParsing -TimeoutSec 35
    $report.probe_http = [int]$probe.StatusCode
    $frame = Invoke-WebRequest -Uri "$api/api/frame.jpeg?src=c701_sd" -UseBasicParsing -TimeoutSec 35
    $report.frame_http = [int]$frame.StatusCode
    $bytes = $frame.RawContentStream.ToArray()
    $report.frame_bytes = $bytes.Length
    $report.jpeg_valid = $bytes.Length -gt 4 -and $bytes[0] -eq 255 -and $bytes[1] -eq 216 -and
        $bytes[$bytes.Length - 2] -eq 255 -and $bytes[$bytes.Length - 1] -eq 217
    if ($report.jpeg_valid) {
        Add-Type -AssemblyName System.Drawing
        $memory = [IO.MemoryStream]::new($bytes)
        try {
            $image = [Drawing.Image]::FromStream($memory)
            try { $report.width = $image.Width; $report.height = $image.Height }
            finally { $image.Dispose() }
        } finally { $memory.Dispose() }
    }
}

try {
    $owners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 21984 -State Listen -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw '找不到唯一的隔離 go2rtc API 程序。' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
    if ($null -eq $process -or $process.Name -ine 'go2rtc.exe' -or
        -not (Test-Path -LiteralPath $process.ExecutablePath)) { throw '無法確認隔離 go2rtc 執行檔。' }
    $command = [string]$process.CommandLine
    if (-not $command.Contains('-config') -or -not $command.Contains('MeowKeeperPlusC701Relay')) {
        throw 'API 程序不是隔離 C701 go2rtc。'
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $process.ExecutablePath).Hash -ne $officialHash) {
        throw 'go2rtc 不是已驗證的官方 v1.9.14。'
    }
    $report.go2rtc_verified = $true
    $report.go2rtc_pid = [int]$owners[0]

    $socket = [Net.Sockets.Socket]::new([Net.Sockets.AddressFamily]::InterNetwork,
        [Net.Sockets.SocketType]::Dgram, [Net.Sockets.ProtocolType]::Udp)
    try {
        $socket.Connect($cameraIp, 32108)
        $localIp = ([Net.IPEndPoint]$socket.LocalEndPoint).Address.ToString()
    } finally { $socket.Dispose() }
    if (-not $localIp.StartsWith('192.168.18.')) { throw '攝影機路由不在相同 LAN；未修改防火牆。' }
    $interface = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $localIp -ErrorAction Stop)
    if ($interface.Count -ne 1) { throw '無法確認唯一的本機 LAN 網卡。' }
    $profile = @(Get-NetConnectionProfile -InterfaceIndex $interface[0].InterfaceIndex -ErrorAction Stop)
    if ($profile.Count -ne 1 -or $profile[0].NetworkCategory -ne 'Public') {
        throw '目前 LAN 不是已授權的 Public 網路類型；未修改防火牆。'
    }
    $report.route_same_subnet = $true
    $report.network_profile = 'Public'
    $existing = @(Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0) {
        Assert-ExactRule $existing $process.ExecutablePath $localIp
        $report.rule_status = 'exact_rule_present'
    } else { $report.rule_status = 'absent' }
    if ($Mode -eq 'Status') { $report.stage = 'status_complete'; return }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '建立或移除規則需要已提升權限的 PowerShell。'
    }
    if ($Mode -eq 'Remove') {
        if ($existing.Count -eq 1) {
            Remove-NetFirewallRule -Name $ruleName -ErrorAction Stop
            $report.rule_status = 'removed'
        }
        $report.stage = 'remove_complete'
        return
    }
    $created = $false
    if ($existing.Count -eq 0) {
        $report.stage = 'creating_scoped_rule'
        New-NetFirewallRule -Name $ruleName -DisplayName 'MeowKeeper+ C701 go2rtc LAN UDP' `
            -Direction Inbound -Action Allow -Enabled True -Profile Public `
            -Program $process.ExecutablePath -Protocol UDP -LocalAddress $localIp `
            -RemoteAddress $cameraIp -ErrorAction Stop | Out-Null
        $created = $true
    }
    $report.stage = 'verifying_scoped_rule'
    $installed = @(Get-NetFirewallRule -Name $ruleName -ErrorAction Stop)
    Assert-ExactRule $installed $process.ExecutablePath $localIp
    $report.rule_status = 'installed_and_verified'
    Start-Sleep -Seconds 1
    try {
        Test-Frame
        if (-not $report.jpeg_valid) { throw '取得的資料不是有效 JPEG。' }
        $report.stage = 'jpeg_verified'
    } catch {
        $report.probe_error_type = $_.Exception.GetType().Name
        $report.stage = 'rule_verified_frame_not_verified'
        if ($created) {
            Remove-NetFirewallRule -Name $ruleName -ErrorAction Stop
            $report.rule_status = 'rolled_back_after_probe_failure'
        }
    }
} catch {
    $report.failed_step = $report.stage
    $report.error_type = $_.Exception.GetType().Name
    $report.failed_command = if ($_.InvocationInfo.MyCommand) { $_.InvocationInfo.MyCommand.Name } else { 'unknown' }
    $report.rule_may_exist = $report.stage -in @('creating_scoped_rule', 'verifying_scoped_rule')
    $report.stage = 'preflight_or_rule_failed'
} finally {
    $report | ConvertTo-Json -Depth 4
}
