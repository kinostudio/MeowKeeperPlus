param()

$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$ffmpeg = Join-Path $packageRoot 'ffmpeg.exe'
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
$ipPath = Join-Path $stateDir 'tailscale-ip.txt'
$framePath = Join-Path $stateDir 'pairing-frame.tmp.jpg'
$stderrPath = Join-Path $stateDir 'pairing-ffmpeg.tmp.txt'
$api = 'http://127.0.0.1:21984'
$model = 'chuangmi.camera.079ae2'
$streamName = 'c701_sd'

try {
    if (-not (Test-Path -LiteralPath $ffmpeg) -or -not (Test-Path -LiteralPath $ipPath)) {
        throw '請先啟動包含 FFmpeg 的 C701 中繼。'
    }
    $tailIp = (Get-Content -LiteralPath $ipPath -Raw).Trim()
    $address = [Net.IPAddress]::Parse($tailIp)
    $octets = $address.GetAddressBytes()
    if ($octets.Length -ne 4 -or $octets[0] -ne 100 -or $octets[1] -lt 64 -or $octets[1] -gt 127) {
        throw '中繼必須綁定 Tailscale 的 100.64.0.0/10 位址。'
    }
    $accounts = @(Invoke-RestMethod -Uri "$api/api/xiaomi" -TimeoutSec 8)
    if ($accounts.Count -ne 1) { throw '小米帳號尚未就緒；請在中繼電腦本機完成登入。' }
    $account = [Uri]::EscapeDataString([string]$accounts[0])
    $list = Invoke-RestMethod -Uri "$api/api/xiaomi?id=$account&region=sg" -TimeoutSec 10
    $matches = @($list.sources | Where-Object { $_.url -match '[?&]model=chuangmi\.camera\.079ae2(?:&|$)' })
    if ($matches.Count -ne 1) { throw 'SG 裝置清單中沒有唯一的 C701。' }
    $source = [string]$matches[0].url
    $didMatch = [regex]::Match($source, '(?:[?&])did=([^&]+)')
    if (-not $source.StartsWith('xiaomi://') -or -not $didMatch.Success) {
        throw '本輪 C701 清單沒有可核對的裝置識別。'
    }
    $did = [Uri]::UnescapeDataString($didMatch.Groups[1].Value)
    $streams = Invoke-RestMethod -Uri "$api/api/streams" -TimeoutSec 8
    if ($null -eq $streams -or -not $streams.PSObject.Properties[$streamName]) {
        throw '尚未建立 c701_sd 中繼串流。'
    }

    # Only one short JPEG consumer.  The frame never leaves this computer and
    # is deleted even if the test or pairing fails.
    $arguments = @(
        '-nostdin', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp',
        '-i', "rtsp://${tailIp}:28554/$streamName", '-frames:v', '1', '-y',
        ('"' + $framePath + '"')
    )
    $process = Start-Process -FilePath $ffmpeg -ArgumentList $arguments -WorkingDirectory $stateDir `
        -WindowStyle Hidden -RedirectStandardError $stderrPath -PassThru
    try {
        Wait-Process -Id $process.Id -Timeout 35 -ErrorAction Stop
    } catch {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
        throw '中繼未在 35 秒內輸出影格，不產生配對碼。'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $framePath)) {
        throw '中繼尚未取得有效影格，不產生配對碼。'
    }
    $frame = [IO.File]::ReadAllBytes($framePath)
    if ($frame.Length -lt 4 -or $frame[0] -ne 255 -or $frame[1] -ne 216 -or
        $frame[$frame.Length - 2] -ne 255 -or $frame[$frame.Length - 1] -ne 217) {
        throw '中繼輸出不是完整 JPEG，不產生配對碼。'
    }
    $streams = Invoke-RestMethod -Uri "$api/api/streams" -TimeoutSec 8
    $stream = $streams.PSObject.Properties[$streamName].Value
    $producers = @($stream.producers | Where-Object { $null -ne $_ })
    if ($producers.Count -ne 1 -or [string]$producers[0].user_agent -ne "CS2 ($model)") {
        throw '中繼 producer 無法唯一核對 C701 型號。'
    }
    $producerDid = [regex]::Match([string]$producers[0].url, '(?:[?&])did=([^&]+)')
    if (-not $producerDid.Success -or
        [Uri]::UnescapeDataString($producerDid.Groups[1].Value) -cne $did) {
        throw '中繼 producer 與本輪小米裝置清單不一致。'
    }
    $random = [byte[]]::new(16)
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($random)
    $nonce = -join ($random | ForEach-Object { $_.ToString('x2') })
    $fingerprintInput = "$nonce`0$did`0$model"
    $hash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($fingerprintInput))
    $fingerprint = -join ($hash | ForEach-Object { $_.ToString('x2') })
    $expires = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + 600
    $payload = [ordered]@{
        version = 1; model = $model; region = 'sg'; tailnet_ip = $tailIp
        rtsp_port = 28554; stream_name = $streamName; nonce = $nonce
        fingerprint = $fingerprint; expires_at = $expires
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Compress)))
    $code = 'MKP1.' + $encoded.TrimEnd('=').Replace('+', '-').Replace('/', '_')
    # The pairing nonce is also a Tailnet-only control credential.  Never
    # write it in plaintext: only this Windows user can decrypt the blob.
    $controlCredential = [ordered]@{
        nonce = $nonce; fingerprint = $fingerprint; model = $model; stream = $streamName
    } | ConvertTo-Json -Compress
    $plain = [Text.Encoding]::UTF8.GetBytes($controlCredential)
    $sealed = [Security.Cryptography.ProtectedData]::Protect(
        $plain, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes((Join-Path $stateDir 'control-credential.dpapi'), $sealed)
    & (Join-Path $PSScriptRoot 'Start-C701Control.ps1') -Refresh
    Write-Host '已驗證 C701 真實影格與同一裝置 producer。請在 10 分鐘內將以下配對碼貼到正式 MeowKeeper+ 的該裝置卡片。'
    Write-Output $code
} catch {
    # Never print the raw HTTP/FFmpeg exception: it may contain a Xiaomi URL.
    Write-Error '配對碼未產生：請確認遠端 Tailscale、go2rtc、小米登入與 C701 影格均正常。'
    exit 1
} finally {
    Remove-Item -LiteralPath $framePath, $stderrPath -Force -ErrorAction SilentlyContinue
}
