param()

$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$ffmpeg = Join-Path $packageRoot 'ffmpeg.exe'
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
$ipPath = Join-Path $stateDir 'tailscale-ip.txt'
$baseUrl = 'http://127.0.0.1:21984'
$streamName = 'c701_sd'
$jpegPath = Join-Path $stateDir 'c701-probe.jpg'
$reportPath = Join-Path $stateDir 'c701-probe.json'
$stderrPath = Join-Path $stateDir 'c701-ffmpeg-stderr.tmp'
$report = [ordered]@{
    model = 'chuangmi.camera.079ae2'
    region = 'sg'
    profile = 'subtype=1'
    jpeg_valid = $false
    stream_name = $streamName
    stage = 'starting'
}
$created = $false
$keepStream = $false
try {
    if (-not (Test-Path -LiteralPath $ipPath)) { throw '找不到隔離 Relay 的 Tailscale 位址記錄，請先啟動 Relay。' }
    $tailIp = (Get-Content -LiteralPath $ipPath -Raw).Trim()
    if ($tailIp -notmatch '^100\.(?:\d{1,3}\.){2}\d{1,3}$') { throw '隔離 Relay 的 Tailscale 位址格式不正確。' }
    $report.stage = 'accounts'
    $accounts = @(Invoke-RestMethod -Uri "$baseUrl/api/xiaomi" -TimeoutSec 8)
    $report.account_count = $accounts.Count
    if ($accounts.Count -ne 1) { throw '請先在遠端電腦的本機頁面完成一次 Xiaomi 登入。' }
    $account = [Uri]::EscapeDataString([string]$accounts[0])
    $report.stage = 'device_list'
    $list = Invoke-RestMethod -Uri "$baseUrl/api/xiaomi?id=$account&region=sg" -TimeoutSec 10
    $matches = @($list.sources | Where-Object { $_.url -match '[?&]model=chuangmi\.camera\.079ae2(?:&|$)' })
    $report.model_match_count = $matches.Count
    if ($matches.Count -ne 1) { throw 'SG 清單中未找到唯一的 C701；未建立串流。' }
    $source = [string]$matches[0].url
    if (-not $source.StartsWith('xiaomi://')) { throw '來源格式不正確；未建立串流。' }
    if ($source -notmatch '[?&]audio=') { $source += '&audio=0' }
    $source += '&subtype=1'
    $report.stage = 'stream_list'
    $streamResponse = Invoke-WebRequest -Uri "$baseUrl/api/streams" -TimeoutSec 8 -UseBasicParsing
    $report.stream_list_http_status = [int]$streamResponse.StatusCode
    $existing = $null
    if (-not [string]::IsNullOrWhiteSpace([string]$streamResponse.Content)) {
        $existing = ConvertFrom-Json -InputObject ([string]$streamResponse.Content) -ErrorAction Stop
    }
    $existingNames = @()
    if ($existing -is [pscustomobject]) {
        $existingNames = @($existing.PSObject.Properties.Name)
        $report.stream_list_shape = 'object'
    } elseif ($null -eq $existing) {
        $report.stream_list_shape = 'empty'
    } else {
        $report.stream_list_shape = 'other'
        throw 'go2rtc 串流清單格式不符；未建立新串流。'
    }
    $reuseStream = $false
    if ($existingNames -contains $streamName) {
        $current = $existing.PSObject.Properties[$streamName].Value
        $consumers = @($current.consumers | Where-Object { $null -ne $_ })
        $producers = @($current.producers | Where-Object { $null -ne $_ })
        $report.existing_producer_count = $producers.Count
        $report.existing_consumer_count = $consumers.Count
        if ($consumers.Count -ne 0 -or $producers.Count -ne 1) {
            throw '既有 c701_sd 並非唯一且無觀看者的 producer；為保護其他串流，未操作。'
        }
        $currentUrl = [string]$producers[0].url
        $freshDid = [regex]::Match($source, '(?:[?&])did=([^&]+)')
        $currentDid = [regex]::Match($currentUrl, '(?:[?&])did=([^&]+)')
        $sourceBase = ($source -split '\?', 2)[0]
        $currentBase = ($currentUrl -split '\?', 2)[0]
        $sameDevice = $currentUrl.StartsWith('xiaomi://') -and
            $sourceBase -ceq $currentBase -and
            $freshDid.Success -and $currentDid.Success -and
            $freshDid.Groups[1].Value -ceq $currentDid.Groups[1].Value -and
            $currentUrl -match '[?&]model=chuangmi\.camera\.079ae2(?:&|$)' -and
            $currentUrl -match '[?&]subtype=1(?:&|$)'
        $report.existing_source_matches_fresh_device = [bool]$sameDevice
        if (-not $sameDevice) {
            throw '既有 c701_sd 與本輪 C701 裝置來源不完全相符；為保護其他串流，未操作。'
        }
        $reuseStream = $true
        $keepStream = $true
        $report.existing_stream_reused = $true
    }
    if (-not $reuseStream) {
        $encoded = [Uri]::EscapeDataString($source)
        $report.stage = 'stream_put'
        Invoke-WebRequest -Method Put -Uri "$baseUrl/api/streams?name=$streamName&src=$encoded" `
            -TimeoutSec 10 -UseBasicParsing | Out-Null
        $created = $true
        $report.stream_put = 'success'
    }
    if (Test-Path -LiteralPath $jpegPath) { Remove-Item -LiteralPath $jpegPath -Force }
    $report.stage = 'ffmpeg_frame'
    $arguments = @(
        '-nostdin', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp',
        '-i', "rtsp://${tailIp}:28554/$streamName", '-frames:v', '1', '-y',
        ('"' + $jpegPath + '"')
    )
    $process = Start-Process -FilePath $ffmpeg -ArgumentList $arguments -WindowStyle Hidden `
        -RedirectStandardError $stderrPath -PassThru
    try {
        Wait-Process -Id $process.Id -Timeout 35 -ErrorAction Stop
    } catch {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
        throw 'FFmpeg 在 35 秒內未取得影格。'
    }
    $process.Refresh()
    $report.ffmpeg_exit_code = $process.ExitCode
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $jpegPath)) {
        throw 'FFmpeg 未輸出 JPEG；小米串流仍未接通。'
    }
    $bytes = [IO.File]::ReadAllBytes($jpegPath)
    if ($bytes.Length -lt 4 -or $bytes[0] -ne 255 -or $bytes[1] -ne 216 -or
        $bytes[$bytes.Length - 2] -ne 255 -or $bytes[$bytes.Length - 1] -ne 217) {
        throw '輸出不是完整 JPEG。'
    }
    Add-Type -AssemblyName System.Drawing
    $image = [Drawing.Image]::FromFile($jpegPath)
    try {
        $report.width = $image.Width
        $report.height = $image.Height
    } finally { $image.Dispose() }
    $report.jpeg_bytes = $bytes.Length
    $report.jpeg_valid = $true
    $report.stage = 'completed'
    $report.jpeg_path = $jpegPath
    $keepStream = $true
    Write-Host "已取得 C701 JPEG：$($report.width)×$($report.height)，$($bytes.Length) bytes。"
    Write-Host '已保留單一 c701_sd stream，供下一步從 MeowKeeper+ 驗證 Tailscale RTSP。'
} catch {
    $report.error_type = $_.Exception.GetType().Name
    $report.error_category = 'stream_or_frame_failed'
    if ($_.Exception -is [System.Net.WebException] -and $_.Exception.Response) {
        $response = $_.Exception.Response
        $report.http_status = [int]$response.StatusCode
        $report.content_type = [string]$response.ContentType
        $reader = [IO.StreamReader]::new($response.GetResponseStream())
        try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
        $report.response_bytes = [Text.Encoding]::UTF8.GetByteCount($body)
        $hash = [Security.Cryptography.SHA256]::Create()
        try {
            $digest = $hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))
            $report.response_sha256 = [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
        } finally { $hash.Dispose() }
        $lower = $body.ToLowerInvariant()
        if ($lower.Contains('bad request')) { $report.response_category = 'bad_request' }
        elseif ($lower.Contains('forbidden')) { $report.response_category = 'forbidden' }
        elseif ($lower.Contains('unauthorized')) { $report.response_category = 'unauthorized' }
        elseif ($lower.Contains('no p2pid')) { $report.response_category = 'no_p2pid' }
        elseif ($lower.Contains('timeout')) { $report.response_category = 'timeout' }
        elseif ($lower.Contains('yaml')) { $report.response_category = 'yaml_error' }
        else { $report.response_category = 'other' }
    }
    if (Test-Path -LiteralPath $stderrPath) {
        $errorText = ([string](Get-Content -LiteralPath $stderrPath -Raw)).ToLowerInvariant()
        if ($errorText.Contains('404')) { $report.error_category = 'rtsp_404' }
        elseif ($errorText.Contains('timed out')) { $report.error_category = 'timeout' }
        elseif ($errorText.Contains('invalid data')) { $report.error_category = 'invalid_data' }
    }
    Write-Host 'C701 尚未取得 JPEG；請不要重複點擊。安全化結果已保存。'
} finally {
    if (Test-Path -LiteralPath $stderrPath) { Remove-Item -LiteralPath $stderrPath -Force }
    if ($created -and -not $keepStream) {
        try {
            Invoke-WebRequest -Method Delete -Uri "$baseUrl/api/streams?src=$streamName" `
                -TimeoutSec 8 -UseBasicParsing | Out-Null
            $report.temporary_stream_removed = $true
        } catch { $report.temporary_stream_removed = $false }
    }
    $report | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host "安全化報告：$reportPath"
}
