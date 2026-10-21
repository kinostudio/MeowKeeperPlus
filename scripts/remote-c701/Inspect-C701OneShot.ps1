# Run on the Windows PC in the same LAN as C701. Prints no Xiaomi secrets.
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:21984'
$cameraIp = Read-Host '攝影機區網 IP'
$tailIp = Read-Host '此電腦 Tailscale IP'
$state = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
$report = [ordered]@{ stage = 'starting'; jpeg_valid = $false }
$jpeg = Join-Path $state ('c701-one-shot-' + [guid]::NewGuid().ToString('N') + '.jpg')
$stderr = Join-Path $state ('c701-one-shot-' + [guid]::NewGuid().ToString('N') + '.stderr')
try {
    $report.stage = 'listeners'
    $apiPids = @(Get-NetTCPConnection -State Listen -LocalPort 21984 |
        Select-Object -ExpandProperty OwningProcess -Unique)
    $rtspPids = @(Get-NetTCPConnection -State Listen -LocalPort 28554 |
        Select-Object -ExpandProperty OwningProcess -Unique)
    $report.same_process = $apiPids.Count -eq 1 -and $rtspPids.Count -eq 1 -and
        $apiPids[0] -eq $rtspPids[0]
    if (-not $report.same_process) { throw 'API 與 RTSP 並非單一程序。' }
    $process = Get-Process -Id $apiPids[0] -ErrorAction Stop
    $report.go2rtc_executable = $process.ProcessName -eq 'go2rtc'
    if (-not $report.go2rtc_executable -or -not $process.Path) { throw '無法驗證 go2rtc 執行檔。' }
    $ffmpeg = Join-Path (Split-Path $process.Path -Parent) 'ffmpeg.exe'
    $report.bundled_ffmpeg = Test-Path -LiteralPath $ffmpeg
    if (-not $report.bundled_ffmpeg) { throw '隔離測試包缺少 FFmpeg。' }

    $report.stage = 'source'
    $streams = Invoke-RestMethod "$base/api/streams" -TimeoutSec 8
    $entry = $streams.PSObject.Properties['c701_sd'].Value
    $report.stream_exists = $null -ne $entry
    if (-not $report.stream_exists) { throw 'c701_sd 串流不存在。' }
    $producers = @($entry.producers | Where-Object { $null -ne $_ })
    $report.registered_producers = $producers.Count
    $report.active_consumers_before = @($entry.consumers | Where-Object { $null -ne $_ }).Count
    if ($producers.Count -ne 1) { throw '來源 producer 數量不是一。' }
    $source = [string]$producers[0].url
    $report.source_scheme_xiaomi = $source.StartsWith('xiaomi://')
    $report.source_region_sg = $source.Contains(':sg@')
    $report.source_camera_ip_matches = $source.Contains('@' + $cameraIp)
    $report.source_subtype_1 = $source -match '[?&]subtype=1(?:&|$)'
    $report.tail_ip_matches = (Get-Content -LiteralPath (Join-Path $state 'tailscale-ip.txt') -Raw).Trim() -eq $tailIp
    $report.camera_ping = Test-Connection -ComputerName $cameraIp -Count 1 -Quiet -ErrorAction SilentlyContinue

    $accounts = @(Invoke-RestMethod "$base/api/xiaomi" -TimeoutSec 8)
    $report.account_count = $accounts.Count
    if ($accounts.Count -eq 1) {
        $account = [uri]::EscapeDataString([string]$accounts[0])
        $devices = Invoke-RestMethod "$base/api/xiaomi?id=$account&region=sg" -TimeoutSec 10
        $matches = @($devices.sources | Where-Object {
            $_.url -match '[?&]model=chuangmi\.camera\.079ae2(?:&|$)'
        })
        $report.model_match_count = $matches.Count
        if ($matches.Count -eq 1) {
            $report.source_matches_fresh_device = $source.StartsWith([string]$matches[0].url)
        }
    }

    $report.stage = 'frame'
    $rtsp = 'rtsp://' + $tailIp + ':28554/c701_sd'
    $args = @('-nostdin', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp',
        '-i', $rtsp, '-frames:v', '1', '-y', ('"' + $jpeg + '"'))
    $child = Start-Process -FilePath $ffmpeg -ArgumentList $args -WindowStyle Hidden `
        -RedirectStandardError $stderr -PassThru
    if (-not $child.WaitForExit(45000)) {
        Stop-Process -Id $child.Id -Force
        $report.ffmpeg_timeout = $true
    } else {
        $child.Refresh()
        $report.ffmpeg_exit_code = $child.ExitCode
    }
    if (Test-Path -LiteralPath $stderr) {
        $errorText = [string](Get-Content -LiteralPath $stderr -Raw)
        $report.rtsp_404 = $errorText.Contains('404')
        $report.rtsp_401 = $errorText.Contains('401')
        $report.ffmpeg_timeout_text = $errorText -match 'timed out|i/o timeout'
    }
    if (Test-Path -LiteralPath $jpeg) {
        $bytes = [IO.File]::ReadAllBytes($jpeg)
        $report.jpeg_valid = $bytes.Length -gt 4 -and $bytes[0] -eq 255 -and
            $bytes[1] -eq 216 -and $bytes[$bytes.Length - 2] -eq 255 -and
            $bytes[$bytes.Length - 1] -eq 217
        if ($report.jpeg_valid) {
            Add-Type -AssemblyName System.Drawing
            $image = [Drawing.Image]::FromFile($jpeg)
            try {
                $report.width = $image.Width
                $report.height = $image.Height
                $report.jpeg_bytes = $bytes.Length
            } finally { $image.Dispose() }
        }
    }
    $report.stage = 'after_frame'
    $after = Invoke-RestMethod "$base/api/streams" -TimeoutSec 8
    $current = $after.PSObject.Properties['c701_sd'].Value
    $report.active_consumers_after = @($current.consumers | Where-Object { $null -ne $_ }).Count
    $logs = [string](Invoke-WebRequest "$base/api/log" -UseBasicParsing -TimeoutSec 8).Content
    $recent = $logs.Substring([Math]::Max(0, $logs.Length - 100000))
    $report.log_chars_checked = $recent.Length
    $report.udp_timeout = [regex]::Matches($recent, 'read udp|i/o timeout', 'IgnoreCase').Count
    $report.no_p2pid = [regex]::Matches($recent, 'no p2pid', 'IgnoreCase').Count
    $report.miss_get_vendor = [regex]::Matches($recent, 'miss_get_vendor', 'IgnoreCase').Count
    $report.general_errors = [regex]::Matches($recent, 'error=|"error"', 'IgnoreCase').Count
    $report.stage = 'completed'
} catch {
    $report.error_type = $_.Exception.GetType().Name
    if ($_.Exception -is [Net.WebException] -and $_.Exception.Response) {
        $report.http_status = [int]$_.Exception.Response.StatusCode
    }
} finally {
    if (Test-Path -LiteralPath $stderr) { Remove-Item -LiteralPath $stderr -Force }
    if (-not $report.jpeg_valid -and (Test-Path -LiteralPath $jpeg)) {
        Remove-Item -LiteralPath $jpeg -Force
    }
    if ($report.jpeg_valid) { $report.jpeg_saved_locally = $true }
    $report | ConvertTo-Json -Depth 3
}
