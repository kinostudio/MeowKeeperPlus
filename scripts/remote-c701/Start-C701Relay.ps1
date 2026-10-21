param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$packageRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$go2rtc = Join-Path $packageRoot 'go2rtc.exe'
$ffmpeg = Join-Path $packageRoot 'ffmpeg.exe'
$expectedHash = '923D57252E8139A69C52E4ACC1E399A640244A8EF457FD9B7267A25847D68F8C'
if (-not (Test-Path -LiteralPath $go2rtc) -or -not (Test-Path -LiteralPath $ffmpeg)) {
    throw '測試包缺少官方 go2rtc 或內附 FFmpeg。'
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $go2rtc).Hash -ne $expectedHash) {
    throw 'go2rtc 校驗碼不符，拒絕啟動。'
}

$addresses = @(
    [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
        Where-Object { $_.OperationalStatus -eq 'Up' -and ($_.Name -match 'Tailscale' -or $_.Description -match 'Tailscale') } |
        ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
        Where-Object { $_.Address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork } |
        ForEach-Object { $_.Address.IPAddressToString } |
        Where-Object { $_ -match '^100\.' } |
        Select-Object -Unique
)
if ($addresses.Count -ne 1) {
    throw '找不到唯一、已連線的 Tailscale IPv4。請先確認遠端電腦的 Tailscale 狀態。'
}
$tailIp = $addresses[0]
$stateDir = Join-Path $env:LOCALAPPDATA 'MeowKeeperPlusC701Relay'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
Remove-Item -LiteralPath (Join-Path $stateDir 'watch.stop') -Force -ErrorAction SilentlyContinue
$configPath = Join-Path $stateDir 'config.yaml'
$ipPath = Join-Path $stateDir 'tailscale-ip.txt'
if (Test-Path -LiteralPath $configPath) {
    if (-not (Test-Path -LiteralPath $ipPath) -or (Get-Content -LiteralPath $ipPath -Raw).Trim() -ne $tailIp) {
        throw 'Tailscale IP 已改變；為保護已保存的小米 Session，未自動覆寫設定。'
    }
} else {
    $ffmpegYaml = $ffmpeg.Replace('\', '/')
    $config = @"
api:
  listen: "127.0.0.1:21984"
rtsp:
  listen: "${tailIp}:28554"
webrtc:
  listen: ""
srtp:
  listen: ""
ffmpeg:
  bin: "$ffmpegYaml"
streams: {}
"@
    [IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($ipPath, $tailIp, [Text.Encoding]::ASCII)
}

$client = [Net.Sockets.TcpClient]::new()
try {
    $alreadyRunning = $client.ConnectAsync('127.0.0.1', 21984).Wait(800) -and $client.Connected
} catch {
    $alreadyRunning = $false
} finally {
    $client.Dispose()
}
if ($alreadyRunning) {
    $listeners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 21984 -State Listen -ErrorAction SilentlyContinue)
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw '本機 21984 的程序無法唯一辨識；未啟動第二個 go2rtc。' }
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
    $expectedExe = (Resolve-Path -LiteralPath $go2rtc).Path
    if (-not $owner -or $owner.ExecutablePath -ine $expectedExe -or
        -not $owner.CommandLine -or -not $owner.CommandLine.Contains($configPath)) {
        throw '本機 21984 已由其他程序占用；未啟動第二個 go2rtc。'
    }
    $status = Invoke-WebRequest -Uri 'http://127.0.0.1:21984/api' -TimeoutSec 3 -UseBasicParsing
    if ($status.StatusCode -ne 200) { throw '既有中繼程序未通過健康檢查。' }
    [IO.File]::WriteAllText((Join-Path $stateDir 'go2rtc.pid'), [string]$owners[0], [Text.Encoding]::ASCII)
    Write-Host "已沿用相同設定的 C701 中繼；PID=$($owners[0])。"
    if (-not $NoBrowser) { Start-Process -FilePath 'http://127.0.0.1:21984/add.html' }
    exit 0
}

$process = Start-Process -FilePath $go2rtc -ArgumentList @('-config', ('"' + $configPath + '"')) `
    -WorkingDirectory $stateDir -WindowStyle Hidden -PassThru
$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:21984/api' -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    if ($process.HasExited) { break }
}
if (-not $ready) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    throw '隔離 go2rtc 未能在 15 秒內啟動；請檢查埠是否被占用。'
}
[IO.File]::WriteAllText((Join-Path $stateDir 'go2rtc.pid'), [string]$process.Id, [Text.Encoding]::ASCII)
Write-Host "隔離 go2rtc 已啟動；PID=$($process.Id)；RTSP 僅綁 Tailscale IP ${tailIp}:28554。"
Write-Host '請在此電腦的 go2rtc 頁面選 Add → Xiaomi，親自完成登入。不要把帳密或 Token 傳給 Codex。'
Write-Host '登入後，執行 Test-C701Relay.cmd。'
if (-not $NoBrowser) { Start-Process -FilePath 'http://127.0.0.1:21984/add.html' }
