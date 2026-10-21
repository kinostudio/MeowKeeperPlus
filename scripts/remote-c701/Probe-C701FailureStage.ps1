# Single C701 failure-stage diagnostic. Never print Xiaomi source or raw errors.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:21984'
$name = 'c701_failure_stage_probe'
$report = [ordered]@{
    model = 'chuangmi.camera.079ae2'
    region = 'sg'
    account_count = $null
    model_match_count = $null
    patch_http = $null
    probe_http = $null
    producer_count = $null
    consumer_count = $null
    temporary_stream_removed = $false
}

function ReadSafeFailure($failure) {
    $result = [ordered]@{
        exception_type = $failure.Exception.GetType().Name
        http = $null
        body_bytes = 0
        category = 'unknown'
    }
    $body = [string]$failure.ErrorDetails.Message
    if ($failure.Exception -is [Net.WebException] -and $failure.Exception.Response) {
        $response = $failure.Exception.Response
        $result.http = [int]$response.StatusCode
        $result.content_type = [string]$response.ContentType
        if ([string]::IsNullOrEmpty($body)) {
            $reader = [IO.StreamReader]::new($response.GetResponseStream())
            try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        }
    }
    $result.body_bytes = [Text.Encoding]::UTF8.GetByteCount($body)
    $text = $body.ToLowerInvariant()
    if ($text -match 'no p2pid|code\s*[:=]\s*-6') { $result.category = 'cloud_p2pid_or_code_minus_6' }
    elseif ($text -match 'miss_get_vendor|vendor') { $result.category = 'cloud_vendor_request' }
    elseif ($text -match 'cs2.*(dial|connect|timeout)|connect.*cs2') { $result.category = 'cs2_connect' }
    elseif ($text -match 'read udp|udp.*timeout|i/o timeout') { $result.category = 'udp_or_io_timeout' }
    elseif ($text -match 'dial tcp|tcp.*timeout|connection refused') { $result.category = 'tcp_connect' }
    elseif ($text -match 'probe: no video|no video|no media|no tracks') { $result.category = 'no_video_tracks' }
    elseif ($text -match 'unsupported|source not supported') { $result.category = 'unsupported_source' }
    elseif ($text -match 'unauthorized|forbidden|401|403') { $result.category = 'authorization' }
    $result.has_xiaomi = $text.Contains('xiaomi')
    $result.has_miss = $text.Contains('miss')
    $result.has_cs2 = $text.Contains('cs2')
    $result.has_udp = $text.Contains('udp')
    $result.has_tcp = $text.Contains('tcp')
    return $result
}

function GetStreams {
    $r = Invoke-WebRequest -Uri "$base/api/streams" -UseBasicParsing -TimeoutSec 8
    if ([string]::IsNullOrWhiteSpace([string]$r.Content)) { return $null }
    return ConvertFrom-Json -InputObject ([string]$r.Content)
}

try {
    $accounts = @(Invoke-RestMethod -Uri "$base/api/xiaomi" -TimeoutSec 8)
    $report.account_count = $accounts.Count
    if ($accounts.Count -ne 1) { throw '隔離 Xiaomi Session 帳號數不是 1；停止。' }
    $account = [Uri]::EscapeDataString([string]$accounts[0])
    $devices = Invoke-RestMethod -Uri "$base/api/xiaomi?id=$account&region=sg" -TimeoutSec 12
    $matches = @($devices.sources | Where-Object { $_.url -match '[?&]model=chuangmi\.camera\.079ae2(?:&|$)' })
    $report.model_match_count = $matches.Count
    if ($matches.Count -ne 1) { throw 'SG 清單沒有唯一的 C701；停止。' }
    $source = [string]$matches[0].url
    if (-not $source.StartsWith('xiaomi://') -or $source -notmatch '[?&]did=') { throw '裝置來源格式不符。' }

    $streams = GetStreams
    if ($null -ne $streams -and $streams.PSObject.Properties.Name -contains $name) {
        throw '同名暫存串流已存在，為避免覆寫而停止。'
    }
    $logBefore = try { [string](Invoke-WebRequest -Uri "$base/api/log" -UseBasicParsing -TimeoutSec 5).Content } catch { '' }

    $encoded = [Uri]::EscapeDataString($source)
    $patch = Invoke-WebRequest -Method Patch -Uri "$base/api/streams?name=$name&src=$encoded" -UseBasicParsing -TimeoutSec 10
    $report.patch_http = [int]$patch.StatusCode
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $probe = Invoke-WebRequest -Uri "$base/api/streams?src=$name&video=all" -UseBasicParsing -TimeoutSec 35
        $report.probe_http = [int]$probe.StatusCode
    } catch {
        $report.probe_failure = ReadSafeFailure $_
        $report.probe_http = $report.probe_failure.http
    } finally { $stopwatch.Stop(); $report.probe_elapsed_seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2) }

    $streams = GetStreams
    if ($null -ne $streams -and $streams.PSObject.Properties.Name -contains $name) {
        $item = $streams.PSObject.Properties[$name].Value
        $report.producer_count = @($item.producers | Where-Object { $null -ne $_ }).Count
        $report.consumer_count = @($item.consumers | Where-Object { $null -ne $_ }).Count
    }
    $logAfter = try { [string](Invoke-WebRequest -Uri "$base/api/log" -UseBasicParsing -TimeoutSec 5).Content } catch { '' }
    $newLog = if ($logAfter.Length -ge $logBefore.Length) { $logAfter.Substring($logBefore.Length) } else { $logAfter }
    $report.log_new_chars = $newLog.Length
    $report.log_no_p2pid = [regex]::Matches($newLog, 'no p2pid', 'IgnoreCase').Count
    $report.log_udp_timeout = [regex]::Matches($newLog, 'read udp|i/o timeout', 'IgnoreCase').Count
    $report.log_no_video = [regex]::Matches($newLog, 'no video|no media|no tracks', 'IgnoreCase').Count
    $report.log_cs2 = [regex]::Matches($newLog, 'cs2', 'IgnoreCase').Count
    $report.log_miss_get_vendor = [regex]::Matches($newLog, 'miss_get_vendor', 'IgnoreCase').Count
} catch {
    $report.outer_failure = ReadSafeFailure $_
} finally {
    try {
        $streams = GetStreams
        if ($null -ne $streams -and $streams.PSObject.Properties.Name -contains $name) {
            try { Invoke-WebRequest -Method Delete -Uri "$base/api/streams?src=$name" -UseBasicParsing -TimeoutSec 8 | Out-Null } catch { }
        }
        $streams = GetStreams
        $report.temporary_stream_removed = $null -eq $streams -or $streams.PSObject.Properties.Name -notcontains $name
    } catch { $report.cleanup_unverified = $true }
}
$report | ConvertTo-Json -Depth 6
