# One bounded, sequential comparison on the Windows PC beside C701.
# Never print or persist Xiaomi sources, credentials, identifiers, or raw logs.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:21984'
$stream = 'c701_canonical_probe'
$report = [ordered]@{
    model = 'chuangmi.camera.079ae2'
    region = 'sg'
    official_go2rtc_api = 21984
    attempts = @()
}

function SafeFailure($failure) {
    $result = [ordered]@{ type = $failure.Exception.GetType().Name; http = $null; category = 'other' }
    if ($failure.Exception -is [Net.WebException] -and $failure.Exception.Response) {
        $response = $failure.Exception.Response
        $result.http = [int]$response.StatusCode
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        $text = $body.ToLowerInvariant()
        if ($text.Contains('no p2pid')) { $result.category = 'no_p2pid' }
        elseif ($text.Contains('miss_get_vendor')) { $result.category = 'miss_get_vendor' }
        elseif ($text.Contains('timeout') -or $text.Contains('i/o timeout')) { $result.category = 'timeout' }
        elseif ($text.Contains('unauthorized') -or $text.Contains('401')) { $result.category = 'authorization' }
        elseif ($text.Contains('unsupported')) { $result.category = 'unsupported' }
        $result.body_bytes = [Text.Encoding]::UTF8.GetByteCount($body)
    }
    return $result
}

function GetStreamNames {
    $response = Invoke-WebRequest -Uri "$base/api/streams" -UseBasicParsing -TimeoutSec 8
    if ([int]$response.StatusCode -ne 200) { throw '串流清單 HTTP 不是 200。' }
    if ([string]::IsNullOrWhiteSpace([string]$response.Content)) { return @() }
    $value = ConvertFrom-Json -InputObject ([string]$response.Content)
    if ($null -eq $value) { return @() }
    if ($value -isnot [pscustomobject]) { throw '串流清單格式不是物件。' }
    return @($value.PSObject.Properties.Name)
}

try {
    $accounts = @(Invoke-RestMethod -Uri "$base/api/xiaomi" -TimeoutSec 8)
    $report.account_count = $accounts.Count
    if ($accounts.Count -ne 1) { throw '隔離 Session 必須只有一個已登入帳號。' }
    $account = [Uri]::EscapeDataString([string]$accounts[0])
    $devices = Invoke-RestMethod -Uri "$base/api/xiaomi?id=$account&region=sg" -TimeoutSec 12
    $matches = @($devices.sources | Where-Object { $_.url -match '[?&]model=chuangmi\.camera\.079ae2(?:&|$)' })
    $report.model_match_count = $matches.Count
    if ($matches.Count -ne 1) { throw 'SG 裝置清單未找到唯一 C701。' }
    $source = [string]$matches[0].url
    if (-not $source.StartsWith('xiaomi://') -or $source -notmatch '[?&]did=') {
        throw '裝置來源格式不符。'
    }
    $names = @(GetStreamNames)
    if ($names -contains $stream) {
        # A previous PUT can create the in-memory stream before config persistence fails.
        # Remove only this exact probe name; never touch c701_sd or another stream.
        try { Invoke-WebRequest -Method Delete -Uri "$base/api/streams?src=$stream" -UseBasicParsing -TimeoutSec 8 | Out-Null } catch { }
        if (@(GetStreamNames) -contains $stream) { throw '無法清除先前的暫存測試串流；停止。' }
        $report.stale_probe_removed = $true
    }

    $profiles = @(
        [ordered]@{ name = 'official_device_list_unmodified'; source = $source },
        [ordered]@{ name = 'official_subtype_3'; source = ($source + '&subtype=3') }
    )
    foreach ($profile in $profiles) {
        $item = [ordered]@{ profile = $profile.name; patch_http = $null; probe_http = $null; frame_http = $null; jpeg_valid = $false; frame_bytes = 0 }
        $created = $false
        $logBeforeLength = 0
        try {
            try { $logBeforeLength = ([string](Invoke-WebRequest -Uri "$base/api/log" -UseBasicParsing -TimeoutSec 5).Content).Length } catch { }
            $encoded = [Uri]::EscapeDataString([string]$profile.source)
            # PATCH changes only the in-memory stream; it avoids PatchConfig writes.
            $patch = Invoke-WebRequest -Method Patch -Uri "$base/api/streams?name=$stream&src=$encoded" -UseBasicParsing -TimeoutSec 10
            $item.patch_http = [int]$patch.StatusCode
            $created = $true
            try {
                $probe = Invoke-WebRequest -Uri "$base/api/streams?src=$stream&video=all" -UseBasicParsing -TimeoutSec 35
                $item.probe_http = [int]$probe.StatusCode
                if (-not [string]::IsNullOrWhiteSpace([string]$probe.Content)) {
                    $probeData = ConvertFrom-Json -InputObject ([string]$probe.Content)
                    if ($probeData -is [pscustomobject]) {
                        $item.probe_has_video_field = @($probeData.PSObject.Properties.Name) -contains 'video'
                    }
                }
            } catch { $item.probe_error = SafeFailure $_ }
            try {
                $frame = Invoke-WebRequest -Uri "$base/api/frame.jpeg?src=$stream" -UseBasicParsing -TimeoutSec 35
                $item.frame_http = [int]$frame.StatusCode
                $bytes = $frame.RawContentStream.ToArray()
                $item.frame_bytes = $bytes.Length
                $item.jpeg_valid = $bytes.Length -gt 4 -and $bytes[0] -eq 255 -and $bytes[1] -eq 216 -and
                    $bytes[$bytes.Length - 2] -eq 255 -and $bytes[$bytes.Length - 1] -eq 217
                if ($item.jpeg_valid) {
                    $path = Join-Path (Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay') 'c701-canonical-proof.jpg'
                    [IO.File]::WriteAllBytes($path, $bytes)
                    Add-Type -AssemblyName System.Drawing
                    $image = [Drawing.Image]::FromFile($path)
                    try { $item.width = $image.Width; $item.height = $image.Height } finally { $image.Dispose() }
                    $item.jpeg_saved_locally = $true
                }
            } catch { $item.frame_error = SafeFailure $_ }
        } catch { $item.outer_error = SafeFailure $_ }
        finally {
            try {
                $logText = [string](Invoke-WebRequest -Uri "$base/api/log" -UseBasicParsing -TimeoutSec 5).Content
                $recent = if ($logText.Length -ge $logBeforeLength) { $logText.Substring($logBeforeLength) } else { $logText }
                $item.log_no_p2pid = [regex]::Matches($recent, 'no p2pid', 'IgnoreCase').Count
                $item.log_udp_timeout = [regex]::Matches($recent, 'read udp|i/o timeout', 'IgnoreCase').Count
                $item.log_no_video = [regex]::Matches($recent, 'no video|probe: no video', 'IgnoreCase').Count
                $item.log_miss_get_vendor = [regex]::Matches($recent, 'miss_get_vendor', 'IgnoreCase').Count
            } catch { $item.log_summary_unavailable = $true }
            if (@(GetStreamNames) -contains $stream) {
                try {
                    $deleted = Invoke-WebRequest -Method Delete -Uri "$base/api/streams?src=$stream" -UseBasicParsing -TimeoutSec 8
                    $item.temporary_stream_removed = [int]$deleted.StatusCode -lt 300
                } catch { $item.temporary_stream_removed = -not (@(GetStreamNames) -contains $stream) }
            } else {
                $item.temporary_stream_removed = $true
            }
            $report.attempts += $item
        }
        if (-not $item.temporary_stream_removed) { break }
        if ($item.jpeg_valid) { break }
        Start-Sleep -Seconds 3
    }
    $report.final_temporary_stream_absent = -not (@(GetStreamNames) -contains $stream)
} catch {
    $report.error_type = $_.Exception.GetType().Name
    $report.error_stage = 'preflight_or_cleanup'
}
$report | ConvertTo-Json -Depth 6
