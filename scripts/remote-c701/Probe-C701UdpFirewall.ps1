param([Parameter(Mandatory)][string]$CameraIp, [ValidateSet('ReadOnly', 'TemporaryRule')][string]$Mode = 'ReadOnly')

# Run on the Windows PC beside C701. Never print Xiaomi secrets or raw URLs.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:21984'
$officialHash = '923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C'
$report = [ordered]@{
    mode = $Mode
    model = 'chuangmi.camera.079ae2'
    go2rtc_verified = $false
    route_same_subnet = $false
    firewall_rule_created = $false
    firewall_rule_removed = $false
    probe_http = $null
    jpeg_valid = $false
}
$rule = $null

function SafeCategory([string]$body) {
    $t = $body.ToLowerInvariant()
    if ($t -match 'no p2pid|code\s*[:=]\s*-6') { return 'cloud_p2pid_or_minus_6' }
    if ($t -match 'miss_get_vendor') { return 'cloud_vendor' }
    if ($t -match 'read udp|i/o timeout|udp.*timeout') { return 'udp_or_io_timeout' }
    if ($t -match 'cs2.*(dial|connect|timeout)') { return 'cs2_connect' }
    if ($t -match 'no video|no tracks') { return 'no_video' }
    if ($t -match 'unsupported') { return 'unsupported' }
    return 'other'
}

function SafeWebFailure($failure) {
    $value = [ordered]@{ type = $failure.Exception.GetType().Name; http = $null; category = 'other' }
    $body = [string]$failure.ErrorDetails.Message
    if ($failure.Exception -is [Net.WebException] -and $failure.Exception.Response) {
        $response = $failure.Exception.Response
        $value.http = [int]$response.StatusCode
        if ([string]::IsNullOrEmpty($body)) {
            $reader = [IO.StreamReader]::new($response.GetResponseStream())
            try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        }
    }
    $value.category = SafeCategory $body
    return $value
}

try {
    $owners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 21984 -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw '找不到唯一的隔離 go2rtc API 程序。' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
    if ($null -eq $process -or -not (Test-Path -LiteralPath $process.ExecutablePath)) { throw '無法確認隔離 go2rtc 執行檔。' }
    $command = [string]$process.CommandLine
    if (-not $command.Contains('-config') -or -not $command.Contains('MeowKeeperPlusC701Relay')) { throw '程序不是隔離 C701 go2rtc。' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $process.ExecutablePath).Hash -ne $officialHash) { throw 'go2rtc 不是已驗證的官方 v1.9.14。' }
    $report.go2rtc_verified = $true
    $report.go2rtc_pid = [int]$owners[0]

    $socket = [Net.Sockets.Socket]::new([Net.Sockets.AddressFamily]::InterNetwork, [Net.Sockets.SocketType]::Dgram, [Net.Sockets.ProtocolType]::Udp)
    try {
        $socket.Connect($cameraIp, 32108)
        $localIp = ([Net.IPEndPoint]$socket.LocalEndPoint).Address.ToString()
    } finally { $socket.Dispose() }
    $report.route_same_subnet = $localIp.StartsWith('192.168.18.')
    $report.route_uses_tailscale = $localIp.StartsWith('100.')
    $localAddress = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $localIp -ErrorAction Stop)
    if ($localAddress.Count -ne 1) { throw '無法確認唯一的本機出口網卡。' }
    $connection = @(Get-NetConnectionProfile -InterfaceIndex $localAddress[0].InterfaceIndex -ErrorAction Stop)
    if ($connection.Count -ne 1) { throw '無法確認出口網卡的防火牆類型。' }
    $category = [string]$connection[0].NetworkCategory
    $report.network_profile = $category
    $firewallProfile = if ($category -eq 'DomainAuthenticated') { 'Domain' } else { $category }
    if ($firewallProfile -notin @('Domain', 'Private', 'Public')) { throw '不支援的網路防火牆類型。' }
    $report.firewall_enabled = [bool](Get-NetFirewallProfile -Name $firewallProfile -ErrorAction Stop).Enabled
    $report.existing_program_udp_rules = @(
        Get-NetFirewallRule -Enabled True -Direction Inbound -Action Allow -ErrorAction Stop |
            Where-Object { (Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $_ -ErrorAction SilentlyContinue).Program -ieq $process.ExecutablePath -and
                (Get-NetFirewallPortFilter -AssociatedNetFirewallRule $_ -ErrorAction SilentlyContinue).Protocol -eq 'UDP' }
    ).Count

    $stream = Invoke-RestMethod -Uri "$base/api/streams?src=c701_sd" -TimeoutSec 8
    if ($null -eq $stream) { throw 'c701_sd 串流不存在。' }
    $report.consumers_before = @($stream.consumers | Where-Object { $null -ne $_ }).Count
    if ($report.consumers_before -gt 0) { throw 'c701_sd 正有觀看者，未執行額外取流。' }

    if ($Mode -eq 'ReadOnly') { $report.stage = 'read_only_complete'; return }
    if (-not $report.route_same_subnet) { throw '出口路由不是攝影機所在區網，未修改防火牆。' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '需要在已提升權限的 PowerShell 執行限縮測試。' }

    $ruleName = 'MeowKeeperC701Temp-' + [guid]::NewGuid().ToString('N')
    $rule = New-NetFirewallRule -Name $ruleName -DisplayName 'MeowKeeper+ C701 temporary UDP diagnostic' `
        -Direction Inbound -Action Allow -Enabled True -Profile $firewallProfile -Program $process.ExecutablePath `
        -Protocol UDP -LocalAddress $localIp -RemoteAddress $cameraIp -ErrorAction Stop
    $report.firewall_rule_created = $true
    Start-Sleep -Seconds 1

    try {
        $probe = Invoke-WebRequest -Uri "$base/api/streams?src=c701_sd&video=all" -UseBasicParsing -TimeoutSec 35
        $report.probe_http = [int]$probe.StatusCode
    } catch {
        $report.probe_failure = SafeWebFailure $_
        $report.probe_http = $report.probe_failure.http
    }
    if ($report.probe_http -eq 200) {
        try {
            $frame = Invoke-WebRequest -Uri "$base/api/frame.jpeg?src=c701_sd" -UseBasicParsing -TimeoutSec 35
            $report.frame_http = [int]$frame.StatusCode
            $bytes = $frame.RawContentStream.ToArray()
            $report.frame_bytes = $bytes.Length
            $report.jpeg_valid = $bytes.Length -gt 4 -and $bytes[0] -eq 255 -and $bytes[1] -eq 216 -and $bytes[$bytes.Length-2] -eq 255 -and $bytes[$bytes.Length-1] -eq 217
            if ($report.jpeg_valid) {
                $path = Join-Path (Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay') 'c701-firewall-proof.jpg'
                [IO.File]::WriteAllBytes($path, $bytes)
                Add-Type -AssemblyName System.Drawing
                $image = [Drawing.Image]::FromFile($path)
                try { $report.width = $image.Width; $report.height = $image.Height } finally { $image.Dispose() }
                $report.jpeg_saved_locally = $true
            }
        } catch { $report.frame_failure = SafeWebFailure $_ }
    }
    $report.stage = 'temporary_rule_test_complete'
} catch {
    $report.error_type = $_.Exception.GetType().Name
    $report.stage = 'preflight_or_test_failed'
} finally {
    if ($null -ne $rule) {
        try {
            Remove-NetFirewallRule -Name $rule.Name -ErrorAction Stop
            $report.firewall_rule_removed = $null -eq (Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue)
        } catch { $report.firewall_rule_removed = $false }
    }
    $report | ConvertTo-Json -Depth 5
}
