# Run on the Windows PC beside C701. Do not print raw Xiaomi/API responses.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:21984'
$report = [ordered]@{
    stream = 'c701_sd'
    probe_http = $null
    frame_http = $null
    jpeg_valid = $false
}
function SafeErrorCategory([string]$body) {
    if ($body -match 'no p2pid') { return 'no_p2pid' }
    if ($body -match 'miss_get_vendor') { return 'miss_get_vendor' }
    if ($body -match 'read udp|i/o timeout|timed out') { return 'timeout' }
    if ($body -match '401|unauthorized|permission') { return 'authorization' }
    if ($body -match 'unsupported') { return 'unsupported' }
    return 'other'
}
function SafeWebFailure($errorRecord) {
    $result = [ordered]@{ type = $errorRecord.Exception.GetType().Name }
    if ($errorRecord.Exception -is [Net.WebException] -and $errorRecord.Exception.Response) {
        $response = $errorRecord.Exception.Response
        $result.http = [int]$response.StatusCode
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        $result.body_bytes = [Text.Encoding]::UTF8.GetByteCount($body)
        $result.category = SafeErrorCategory $body
    }
    return $result
}
try {
    try {
        $r = Invoke-WebRequest "$base/api/streams?src=c701_sd&video=all" -UseBasicParsing -TimeoutSec 40
        $report.probe_http = [int]$r.StatusCode
        $data = ConvertFrom-Json -InputObject ([string]$r.Content) -ErrorAction Stop
        $report.probe_is_object = $data -is [pscustomobject]
        if ($data -is [pscustomobject]) {
            $report.probe_field_count = @($data.PSObject.Properties).Count
            if ($data.PSObject.Properties.Name -contains 'producers') {
                $report.probe_producer_count = @($data.producers | Where-Object { $null -ne $_ }).Count
            }
        }
    } catch {
        $report.probe_failure = SafeWebFailure $_
    }

    try {
        $r = Invoke-WebRequest "$base/api/frame.jpeg?src=c701_sd" -UseBasicParsing -TimeoutSec 40
        $report.frame_http = [int]$r.StatusCode
        $bytes = $r.RawContentStream.ToArray()
        $report.frame_bytes = $bytes.Length
        $report.jpeg_valid = $bytes.Length -gt 4 -and $bytes[0] -eq 255 -and
            $bytes[1] -eq 216 -and $bytes[$bytes.Length - 2] -eq 255 -and
            $bytes[$bytes.Length - 1] -eq 217
        if ($report.jpeg_valid) {
            $name = 'c701-http-proof-' + [guid]::NewGuid().ToString('N') + '.jpg'
            $path = Join-Path (Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay') $name
            [IO.File]::WriteAllBytes($path, $bytes)
            Add-Type -AssemblyName System.Drawing
            $image = [Drawing.Image]::FromFile($path)
            try {
                $report.width = $image.Width
                $report.height = $image.Height
            } finally { $image.Dispose() }
            $report.jpeg_saved_locally = $true
        }
    } catch {
        $report.frame_failure = SafeWebFailure $_
    }

    $log = [string](Invoke-WebRequest "$base/api/log" -UseBasicParsing -TimeoutSec 8).Content
    $recent = $log.Substring([Math]::Max(0, $log.Length - 100000))
    $report.log_chars_checked = $recent.Length
    $report.log_udp_timeout = [regex]::Matches($recent, 'read udp|i/o timeout', 'IgnoreCase').Count
    $report.log_no_p2pid = [regex]::Matches($recent, 'no p2pid', 'IgnoreCase').Count
    $report.log_general_errors = [regex]::Matches($recent, 'error=|"error"', 'IgnoreCase').Count
} catch {
    $report.outer_error_type = $_.Exception.GetType().Name
}
$report | ConvertTo-Json -Depth 4
