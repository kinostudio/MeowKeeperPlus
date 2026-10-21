param(
    [ValidateSet('Diagnose', 'Install', 'Remove', 'RemoveAllOwned', 'RestoreBlocks')][string]$Mode = 'Diagnose',
    [string]$CameraIp = '',
    [ValidateRange(1, 65535)][int]$ApiPort = 1984,
    [ValidateRange(1, 65535)][int]$RtspPort = 8554,
    [string]$StreamName = '',
    [ValidateRange(0, 120)][int]$SustainedSeconds = 15,
    [switch]$ConfirmRule,
    [string]$ConfirmedBlockRules = '',
    [string]$BackupPath = '',
    [string]$AppRoot = '',
    [string]$ResultPath = ''
)

# This is an opt-in, camera-scoped Windows rule manager. No Xiaomi credentials
# or full source URL are accepted as parameters or written to its JSON output.
$ErrorActionPreference = 'Stop'
$marker = 'MeowKeeperPlus.XiaomiUdp.v1'
$disabledBlocks = @()
$createdRule = $false
$report = [ordered]@{ mode = $Mode; stage = 'preflight'; status = 'not_checked'; rule_status = 'not_checked'; single_frame = 'not_checked'; sustained_stream = 'not_checked' }

function Test-Administrator {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-UdpBlockForProfile($rule, [string]$profile, [string]$protocol) {
    $profiles = ([string]$rule.Profile) -split ',\s*'
    return $rule.Direction -eq 'Inbound' -and $rule.Enabled -eq 'True' -and
        $rule.Action -eq 'Block' -and $protocol -in @('UDP','17','Any','256') -and
        ('Any' -in $profiles -or $profile -in $profiles)
}

function Get-RuleIdentity([string]$program, [string]$camera) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($program.ToLowerInvariant() + '|' + $camera)
        $digest = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').Substring(0, 16)
    } finally { $sha.Dispose() }
    return "MeowKeeperPlus-Xiaomi-UDP-$digest"
}

function Get-ExactRule($rule, [string]$program, [string]$localIp, [string]$camera, [string]$binaryHash) {
    # Always unwrap a singleton. AssociatedNetFirewallRule rejects an array.
    if (@($rule).Count -ne 1) { return $false }
    $one = @($rule)[0]
    $app = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
    $addr = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
    $port = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
    $expectedDescription = "$marker;camera=$camera;binary_sha256=$binaryHash;local=$localIp;profile=$script:networkProfile"
    return $one.Group -eq $marker -and $one.Description -eq $expectedDescription -and
        $one.Direction -eq 'Inbound' -and $one.Action -eq 'Allow' -and
        $one.Enabled -eq 'True' -and $one.Profile -eq $script:networkProfile -and
        [StringComparer]::OrdinalIgnoreCase.Equals([string]$app.Program, $program) -and
        $port.Protocol -eq 'UDP' -and $port.LocalPort -eq 'Any' -and $port.RemotePort -eq 'Any' -and
        @($addr.LocalAddress).Count -eq 1 -and @($addr.RemoteAddress).Count -eq 1 -and
        $addr.LocalAddress -eq $localIp -and $addr.RemoteAddress -eq $camera
}

function Get-SafeCategory([string]$message, [int]$http) {
    if ($http -eq 401 -or $message -match 'unauthorized|invalid credentials|login.*fail') { return 'account_authentication_failed' }
    if ($message -match 'no p2pid|miss_get_vendor|code\s*[:=]\s*-6') { return 'xiaomi_cloud_or_p2p_failure' }
    if ($message -match 'read udp|udp.*timeout|i/o timeout') { return 'udp_timeout' }
    if ($message -match 'ref with POC|Error constructing.*RPS|decoder|invalid data') { return 'frame_decode_failed' }
    if ($message -match 'no route|network is unreachable|connection refused|404') { return 'source_unreachable' }
    return 'unknown_error'
}

function Test-ExistingStream([string]$stream) {
    if (-not $stream) { return }
    if ($stream -cnotmatch '^[A-Za-z0-9_-]{1,80}$') { throw '串流名稱格式不正確。' }
    $url = "http://127.0.0.1:$ApiPort/api/streams?src=$stream"
    try {
        $listed = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 8
        $report.stream_http = [int]$listed.StatusCode
        if ([string]::IsNullOrWhiteSpace([string]$listed.Content) -or
            ([string]$listed.Content).Trim() -in @('{}', 'null')) {
            $report.stream_status = 'not_registered'; return
        }
        $report.stream_status = 'registered'
    } catch {
        $report.stream_status = 'unreachable'
        $report.stream_error = Get-SafeCategory ([string]$_.ErrorDetails.Message) ([int]$_.Exception.Response.StatusCode)
        return
    }
    try {
        $probe = Invoke-WebRequest -Uri "${url}&video=all" -UseBasicParsing -TimeoutSec 25
        $report.open_http = [int]$probe.StatusCode
        $frame = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/frame.jpeg?src=$stream" -UseBasicParsing -TimeoutSec 25
        $report.frame_http = [int]$frame.StatusCode
        $bytes = $frame.RawContentStream.ToArray()
        $valid = $bytes.Length -gt 4 -and $bytes[0] -eq 255 -and $bytes[1] -eq 216 -and
            $bytes[$bytes.Length - 2] -eq 255 -and $bytes[$bytes.Length - 1] -eq 217
        $report.single_frame = if ($valid) { 'passed' } else { 'failed' }
        $report.frame_bytes = $bytes.Length
        if (-not $valid) { $report.stream_error = 'frame_unavailable_or_decode_failed' }
    } catch {
        $http = 0
        if ($_.Exception.Response) { $http = [int]$_.Exception.Response.StatusCode }
        $report.open_http = if ($http) { $http } else { $null }
        $report.stream_error = Get-SafeCategory ([string]$_.ErrorDetails.Message) $http
        $report.single_frame = 'failed'
    }
    if ($SustainedSeconds -gt 0) { Test-SustainedStream $stream }
}

function Test-SustainedStream([string]$stream) {
    $ffmpeg = Join-Path $root 'vendor\ffmpeg\bin\ffmpeg.exe'
    if (-not (Test-Path -LiteralPath $ffmpeg)) { $report.sustained_stream = 'ffmpeg_missing'; return }
    $progressPath = Join-Path $env:TEMP ("MeowKeeperXiaomiUdp-{0}.progress" -f [guid]::NewGuid().ToString('N'))
    $errorPath = Join-Path $env:TEMP ("MeowKeeperXiaomiUdp-{0}.err" -f [guid]::NewGuid().ToString('N'))
    $process = $null
    try {
        $arguments = @('-hide_banner', '-nostdin', '-v', 'fatal', '-rtsp_transport', 'tcp',
            '-timeout', '10000000', '-i', "rtsp://127.0.0.1:$RtspPort/$stream",
            '-map', '0:v:0', '-t', [string]$SustainedSeconds, '-f', 'null', 'NUL', '-progress', 'pipe:1')
        $process = Start-Process -FilePath $ffmpeg -ArgumentList $arguments -WindowStyle Hidden `
            -RedirectStandardOutput $progressPath -RedirectStandardError $errorPath -PassThru -ErrorAction Stop
        if (-not $process.WaitForExit(($SustainedSeconds + 25) * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $report.sustained_stream = 'timeout'; return
        }
        $values = @(Get-Content -LiteralPath $progressPath -ErrorAction SilentlyContinue)
        $frames = @($values | Where-Object { $_ -match '^frame=([0-9]+)$' } | ForEach-Object { [int]($_ -split '=')[1] })
        $lastFrame = if ($frames.Count) { [int]$frames[-1] } else { 0 }
        $report.sustained_frames = $lastFrame
        $report.sustained_seconds_requested = $SustainedSeconds
        $report.sustained_stream = if ($process.ExitCode -eq 0 -and $lastFrame -gt 1 -and
            @($values | Where-Object { $_ -eq 'progress=end' }).Count -gt 0) { 'passed' } else { 'failed' }
        if ($report.sustained_stream -eq 'failed') {
            $safeError = (Get-Content -LiteralPath $errorPath -Raw -ErrorAction SilentlyContinue)
            $report.sustained_error = Get-SafeCategory ([string]$safeError) 0
        }
    } catch {
        $report.sustained_stream = 'failed'
        $report.sustained_error = $_.Exception.GetType().Name
    } finally {
        if ($null -ne $process) { $process.Dispose() }
        Remove-Item -LiteralPath $progressPath, $errorPath -Force -ErrorAction SilentlyContinue
    }
}

try {
    if (-not $IsWindows -and $PSVersionTable.PSEdition -ne 'Desktop') { throw '此工具僅支援 Windows。' }
    $root = if ($AppRoot) { [IO.Path]::GetFullPath($AppRoot) } else { [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')) }
    $manifestPath = Join-Path $root 'vendor\go2rtc\model_manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw '找不到官方 go2rtc manifest。' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.repository -ne 'https://github.com/AlexxIT/go2rtc' -or $manifest.release_tag -ne 'v1.9.14') {
        throw 'go2rtc manifest 不是目前核准的官方版本。'
    }
    $program = [IO.Path]::GetFullPath((Join-Path $root "vendor\go2rtc\$($manifest.release_tag)\go2rtc.exe"))
    # Validate the selected packaged PTZ build separately from the official one.
    $selectionPath = Join-Path $root 'vendor\go2rtc\backend.json'
    if (Test-Path -LiteralPath $selectionPath) {
        $selection = Get-Content -LiteralPath $selectionPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($selection.backend -eq 'ptz-feedback') {
            $ptzPath = Join-Path $root 'vendor\go2rtc\ptz-feedback\manifest.json'
            $ptz = Get-Content -LiteralPath $ptzPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $approved = '3F64D4C5144350DC816ABB4DE41BD686E248A9528EB2825A00CD8FC65EFEA0F3'
            if ($ptz.binary_sha256 -ne $approved) { throw 'PTZ manifest SHA-256 不符。' }
            $program = [IO.Path]::GetFullPath((Join-Path $root 'vendor\go2rtc\ptz-feedback\go2rtc.exe'))
            $manifest = [pscustomobject]@{ release_tag='experimental-ptz-feedback'; executable_sha256=$approved }
        } elseif ($selection.backend -ne 'official') { throw 'go2rtc manifest backend 不符。' }
    }
    if (-not (Test-Path -LiteralPath $program)) { throw '找不到內附 go2rtc.exe。' }
    $binaryHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $program).Hash.ToUpperInvariant()
    if ($binaryHash -ne [string]$manifest.executable_sha256.ToUpperInvariant()) { throw 'go2rtc SHA-256 驗證失敗。' }
    $report.go2rtc_binary_verified = $true
    $report.go2rtc_version = $manifest.release_tag

    if ($Mode -eq 'RestoreBlocks') {
        if (-not $ConfirmRule -or -not (Test-Administrator)) { throw '復原需明確確認及系統管理員權限。' }
        $backupRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'MeowKeeperPlus\firewall-backups')).TrimEnd('\') + '\'
        $resolvedBackup = [IO.Path]::GetFullPath($BackupPath)
        if (-not $resolvedBackup.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase) -or
            [IO.Path]::GetFileName($resolvedBackup) -notmatch '^[0-9a-f]{32}\.json$') { throw '復原備份位置不符。' }
        $saved = Get-Content -LiteralPath $resolvedBackup -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.marker -ne $marker -or $saved.binary_sha256 -ne $binaryHash) { throw '復原備份身分不符。' }
        foreach ($snapshot in @($saved.rules)) {
            $one = Get-NetFirewallRule -Name $snapshot.name -ErrorAction Stop
            $app = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $one
            $port = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $one | Select-Object Protocol,LocalPort,RemotePort
            $address = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $one | Select-Object LocalAddress,RemoteAddress
            if ($snapshot.name -notmatch '^\{[0-9A-Fa-f-]{36}\}$' -or $snapshot.program -ine $program -or
                $app.Program -ine $program -or $one.Action -ne 'Block' -or $one.Direction -ne 'Inbound' -or
                [string]$one.Profile -ne $snapshot.profile -or $snapshot.enabled -ne 'True' -or
                ($port | ConvertTo-Json -Compress) -ne ($snapshot.port | ConvertTo-Json -Compress) -or
                ($address | ConvertTo-Json -Compress) -ne ($snapshot.address | ConvertTo-Json -Compress)) {
                throw '復原規則已變動；未修改規則。'
            }
        }
        foreach ($snapshot in @($saved.rules)) { Enable-NetFirewallRule -Name $snapshot.name -ErrorAction Stop }
        $report.status = 'passed'; $report.stage = 'blocks_restored'; return
    }

    if ($Mode -eq 'RemoveAllOwned') {
        if (-not (Test-Administrator)) {
            $handoffPath = Join-Path $env:TEMP ("MeowKeeperXiaomiUdp-{0}.json" -f [guid]::NewGuid().ToString('N'))
            $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath),
                '-Mode', 'RemoveAllOwned', '-AppRoot', ('"{0}"' -f $root),
                '-ResultPath', ('"{0}"' -f $handoffPath))
            try {
                $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
                if ($elevated.ExitCode -eq 0 -and (Test-Path -LiteralPath $handoffPath)) {
                    $elevatedReport = Get-Content -LiteralPath $handoffPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    foreach ($property in $elevatedReport.PSObject.Properties) { $report[$property.Name] = $property.Value }
                } else { $report.status = 'failed'; $report.stage = 'uac_handoff_failed' }
            } catch [System.ComponentModel.Win32Exception] {
                $report.status = 'uac_cancelled'; $report.stage = 'no_change'
            } finally { Remove-Item -LiteralPath $handoffPath -Force -ErrorAction SilentlyContinue }
            return
        }
        $owned = @(Get-NetFirewallRule -Group $marker -ErrorAction SilentlyContinue)
        # Validate every owned rule before changing any of them.
        foreach ($one in $owned) {
            $app = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $one -ErrorAction Stop
            if (-not [StringComparer]::OrdinalIgnoreCase.Equals([string]$app.Program, $program) -or
                $one.Name -notmatch '^MeowKeeperPlus-Xiaomi-UDP-[0-9A-F]{16}$' -or
                $one.Description -notmatch ('^' + [regex]::Escape($marker) + ';camera=([0-9.]+);binary_sha256=([0-9A-F]{64});local=([0-9.]+);profile=(Public|Private|Domain)$')) {
                throw '找到識別不符的同群組規則；解除安裝已停止，未更動該規則。'
            }
            $match = [regex]::Match($one.Description, '^' + [regex]::Escape($marker) + ';camera=([0-9.]+);binary_sha256=([0-9A-F]{64});local=([0-9.]+);profile=(Public|Private|Domain)$')
            $camera = $match.Groups[1].Value
            $localIp = $match.Groups[3].Value
            $script:networkProfile = $match.Groups[4].Value
            if ($match.Groups[2].Value -ne $binaryHash -or $one.Name -ne (Get-RuleIdentity $program $camera) -or
                -not (Get-ExactRule $one $program $localIp $camera $binaryHash)) {
                throw '規則內容已改變；未移除使用者或其他程式的規則。'
            }
        }
        foreach ($one in $owned) {
            Remove-NetFirewallRule -Name $one.Name -ErrorAction Stop
        }
        $report.removed_count = $owned.Count
        $report.status = 'passed'; $report.stage = 'remove_all_complete'
        return
    }

    $address = $null
    if (-not [Net.IPAddress]::TryParse($CameraIp, [ref]$address) -or
        $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw '請提供有效的攝影機 IPv4。' }
    $octets = $address.GetAddressBytes()
    if (-not ($octets[0] -eq 10 -or ($octets[0] -eq 172 -and $octets[1] -ge 16 -and $octets[1] -le 31) -or
        ($octets[0] -eq 192 -and $octets[1] -eq 168))) { throw '只接受私人區網攝影機 IPv4。' }
    $ruleName = Get-RuleIdentity $program $CameraIp
    $report.rule_name = $ruleName

    $owners = @(Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1') } |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw 'go2rtc API 必須由唯一的 localhost 程序監聽。' }
    $running = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])" -ErrorAction Stop
    if (-not $running -or -not [StringComparer]::OrdinalIgnoreCase.Equals([string]$running.ExecutablePath, $program)) {
        throw 'API port 的程序不是已驗證的 go2rtc.exe。'
    }
    $report.go2rtc_process_verified = $true
    $report.go2rtc_pid = [int]$owners[0]

    $socket = [Net.Sockets.Socket]::new([Net.Sockets.AddressFamily]::InterNetwork,
        [Net.Sockets.SocketType]::Dgram, [Net.Sockets.ProtocolType]::Udp)
    try { $socket.Connect($CameraIp, 32108); $localIp = ([Net.IPEndPoint]$socket.LocalEndPoint).Address.ToString() }
    finally { $socket.Dispose() }
    $localInterface = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $localIp -ErrorAction Stop)
    if ($localInterface.Count -ne 1) { throw '無法唯一確認攝影機方向的出口網卡。' }
    $adapter = Get-NetAdapter -InterfaceIndex $localInterface[0].InterfaceIndex -ErrorAction Stop
    if ($adapter.Status -ne 'Up' -or [string]($adapter.Name + ' ' + $adapter.InterfaceDescription) -match 'Tailscale|Wintun|VPN|TAP|Hyper-V|Docker|Virtual|Loopback') {
        throw '出口不是有效實體區網網卡；請在攝影機所在電腦診斷。'
    }
    $profile = @(Get-NetConnectionProfile -InterfaceIndex $localInterface[0].InterfaceIndex -ErrorAction Stop)
    if ($profile.Count -ne 1) { throw '無法確認出口網卡的 Windows 網路設定檔。' }
    $script:networkProfile = if ($profile[0].NetworkCategory -eq 'DomainAuthenticated') { 'Domain' } else { [string]$profile[0].NetworkCategory }
    if ($script:networkProfile -notin @('Public', 'Private', 'Domain')) { throw '無法確認 Windows 防火牆設定檔。' }
    $report.network_profile = $script:networkProfile
    $report.route = 'physical_lan'
    $report.local_ip_last_octet = $localIp.Split('.')[-1]
    $report.camera_ip_last_octet = $CameraIp.Split('.')[-1]
    $blocking = @()
    $otherAllows = 0
    $matchingApps = @(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore -ErrorAction Stop |
        Where-Object { [string]$_.Program -ieq $program })
    $matchingRules = @($matchingApps | Get-NetFirewallRule -ErrorAction Stop |
        Where-Object { $_.Direction -eq 'Inbound' -and $_.Enabled -eq 'True' })
    foreach ($rule in $matchingRules) {
        $portFilter = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule -ErrorAction Stop
        if ([string]$portFilter.Protocol -notin @('UDP','17','Any','256')) { continue }
        $profiles = ([string]$rule.Profile) -split ',\s*'
        $applies = 'Any' -in $profiles -or $script:networkProfile -in $profiles
        if (Test-UdpBlockForProfile $rule $script:networkProfile ([string]$portFilter.Protocol)) { $blocking += $rule }
        if ($rule.Action -eq 'Allow' -and -not $applies) { $otherAllows++ }
    }
    $report.blocking_rule_names = @($blocking | ForEach-Object { $_.Name })
    $report.blocking_rule_count = $blocking.Count
    $report.other_profile_allow_count = $otherAllows
    $report.block_scope_note = '程式 UDP 封鎖候選；可能另有限定埠或位址，修復需明確核准指定規則。'
    $existing = @(Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 1) { throw '同名規則重複，未修改任何防火牆設定。' }
    $exact = $existing.Count -eq 1 -and (Get-ExactRule $existing $program $localIp $CameraIp $binaryHash)
    $report.rule_status = if ($exact) { 'exact_rule_present' } elseif ($existing.Count) { 'conflicting_rule' } else { 'absent' }
    if ($existing.Count -and -not $exact) { throw '同名規則已存在但範圍不同；不覆蓋使用者設定。' }

    if ($Mode -eq 'Diagnose') {
        Test-ExistingStream $StreamName
        $report.status = 'passed'; $report.stage = 'diagnosis_complete'
        return
    }
    if ($Mode -eq 'Install' -and -not $ConfirmRule) { throw '需由使用者明確確認，才可建立限縮 UDP 規則。' }
    $approvedBlocks = @($ConfirmedBlockRules -split ',' | Where-Object { $_ })
    if ($Mode -eq 'Install' -and $blocking.Count) {
        foreach ($one in $blocking) {
            if ($one.Name -notin $approvedBlocks -or $one.Name -notmatch '^\{[0-9A-Fa-f-]{36}\}$') {
                throw '有 UDP Block 規則；需要明確確認指定規則，未修改防火牆。'
            }
        }
        if ($approvedBlocks.Count -ne $blocking.Count) { throw '核准 Block 規則與實際狀態不同。' }
    }
    if (-not (Test-Administrator)) {
        # Consent is checked before UAC. No secret is passed on the command line.
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath),
            '-Mode', $Mode, '-CameraIp', $CameraIp, '-ApiPort', [string]$ApiPort,
            '-RtspPort', [string]$RtspPort, '-SustainedSeconds', [string]$SustainedSeconds,
            '-AppRoot', ('"{0}"' -f $root))
        if ($StreamName) { $arguments += @('-StreamName', $StreamName) }
        if ($ConfirmRule) { $arguments += '-ConfirmRule' }
        if ($ConfirmedBlockRules) { $arguments += @('-ConfirmedBlockRules', ('"{0}"' -f $ConfirmedBlockRules)) }
        $handoffPath = Join-Path $env:TEMP ("MeowKeeperXiaomiUdp-{0}.json" -f [guid]::NewGuid().ToString('N'))
        $arguments += @('-ResultPath', ('"{0}"' -f $handoffPath))
        try {
            $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
            if (Test-Path -LiteralPath $handoffPath) {
                $elevatedReport = Get-Content -LiteralPath $handoffPath -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($property in $elevatedReport.PSObject.Properties) { $report[$property.Name] = $property.Value }
            } else {
                $report.status = if ($elevated.ExitCode -eq 0) { 'elevated_process_completed_without_report' } else { 'elevated_process_failed' }
                $report.stage = 'uac_handoff'
            }
        } catch [System.ComponentModel.Win32Exception] {
            $report.status = 'uac_cancelled'; $report.stage = 'no_change'
        } finally {
            Remove-Item -LiteralPath $handoffPath -Force -ErrorAction SilentlyContinue
        }
        return
    }
    if ($Mode -eq 'Remove') {
        if ($exact) { Remove-NetFirewallRule -Name $ruleName -ErrorAction Stop; $report.rule_status = 'removed' }
        $report.status = 'passed'; $report.stage = 'remove_complete'
        return
    }
    if (-not $exact) {
        $report.stage = 'creating_rule'
        New-NetFirewallRule -Name $ruleName -DisplayName 'MeowKeeper+ 小米攝影機 UDP' -Group $marker `
            -Description "$marker;camera=$CameraIp;binary_sha256=$binaryHash;local=$localIp;profile=$script:networkProfile" `
            -Direction Inbound -Action Allow -Enabled True -Profile $script:networkProfile `
            -Program $program -Protocol UDP -LocalAddress $localIp -RemoteAddress $CameraIp -ErrorAction Stop | Out-Null
        $createdRule = $true
    }
    $report.stage = 'verifying_rule'
    $installed = @(Get-NetFirewallRule -Name $ruleName -ErrorAction Stop)
    if (-not (Get-ExactRule $installed $program $localIp $CameraIp $binaryHash)) { throw '新規則未通過精確範圍檢查。' }
    $report.rule_status = 'installed_and_verified'
    if ($blocking.Count) {
        $backupDirectory = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlus\firewall-backups'
        New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
        $backupPath = Join-Path $backupDirectory (([guid]::NewGuid().ToString('N')) + '.json')
        $snapshots = @()
        foreach ($one in $blocking) {
            $fresh = Get-NetFirewallRule -Name $one.Name -ErrorAction Stop
            $freshApp = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $fresh
            $freshPort = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $fresh
            if ($freshApp.Program -ine $program -or $fresh.Action -ne 'Block' -or
                $fresh.Enabled -ne 'True' -or $fresh.Profile -ne $one.Profile -or
                [string]$freshPort.Protocol -notin @('UDP','17','Any','256')) { throw 'Block 規則狀態已改變。' }
            $snapshots += [pscustomobject]@{ name=$fresh.Name; enabled=[string]$fresh.Enabled;
                profile=[string]$fresh.Profile; program=$program;
                port=(Get-NetFirewallPortFilter -AssociatedNetFirewallRule $fresh | Select-Object Protocol,LocalPort,RemotePort);
                address=(Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $fresh | Select-Object LocalAddress,RemoteAddress) }
        }
        $backupPayload = @{marker=$marker; binary_sha256=$binaryHash; rules=$snapshots}
        [IO.File]::WriteAllText($backupPath, ($backupPayload | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        $report.block_backup_path = $backupPath
        foreach ($one in $blocking) {
            Disable-NetFirewallRule -Name $one.Name -ErrorAction Stop
            $disabledBlocks += $one.Name
            if ((Get-NetFirewallRule -Name $one.Name).Enabled -ne 'False') { throw 'Block 停用未確認。' }
        }
        $report.disabled_block_rules = $disabledBlocks
    }
    Test-ExistingStream $StreamName
    $report.status = 'passed'; $report.stage = 'rule_verified'
} catch {
    foreach ($name in $disabledBlocks) { Enable-NetFirewallRule -Name $name -ErrorAction SilentlyContinue }
    if ($createdRule) { Remove-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue }
    $report.failed_step = $report.stage
    $report.error_type = $_.Exception.GetType().Name
    $safeMessage = [string]$_.Exception.Message
    $report.error_code = if ($safeMessage -match 'manifest|SHA-256|go2rtc.exe') { 'go2rtc_identity_unverified' }
        elseif ($safeMessage -match 'API port|localhost|程序') { 'go2rtc_process_unverified' }
        elseif ($safeMessage -match 'IPv4|區網|網卡|出口') { 'camera_route_unverified' }
        elseif ($safeMessage -match '同名規則|規則內容|精確範圍') { 'firewall_rule_conflict' }
        elseif ($safeMessage -match '明確確認') { 'consent_required' }
        elseif ($_.Exception -is [System.UnauthorizedAccessException] -or $safeMessage -match '拒絕存取|Access is denied') { 'administrator_permission_required' }
        else { 'diagnostic_failed' }
    $report.status = 'failed'; $report.stage = 'failed'
} finally {
    $json = $report | ConvertTo-Json -Depth 4
    if ($ResultPath) {
        $full = [IO.Path]::GetFullPath($ResultPath)
        $tempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
        if ($full.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($full) -match '^MeowKeeperXiaomiUdp-[0-9a-f]{32}\.json$') {
            [IO.File]::WriteAllText($full, $json, [Text.UTF8Encoding]::new($false))
        }
    } else { $json }
}
