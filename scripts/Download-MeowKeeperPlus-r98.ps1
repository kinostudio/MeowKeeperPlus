param([string]$Destination = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'MeowKeeperPlus-r98'))
$ErrorActionPreference = 'Stop'
$base = 'https://github.com/kinostudio/MeowKeeperPlus/releases/download/r98'
$expected = '0CCD64BB7E35E3E17CB84CF091871751550E5262952F0B4CE88267CBF779AEAC'
if (Test-Path -LiteralPath $Destination) { throw '目標資料夾已存在；不會覆蓋任何資料。' }
$downloadRoot = Join-Path $env:TEMP ('MeowKeeperPlus-r98-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $downloadRoot | Out-Null
$manifestFile = Join-Path $downloadRoot 'update-manifest.json'
Invoke-WebRequest -Uri "$base/update-manifest.json" -OutFile $manifestFile -UseBasicParsing -TimeoutSec 60
$manifest = (Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json).manifest
if ($manifest.version -ne 'r98' -or $manifest.repository -ne 'kinostudio/MeowKeeperPlus' -or
    $manifest.sha256.ToUpperInvariant() -ne $expected -or @($manifest.parts).Count -ne 3) {
    throw '發布資訊不符；未執行更新。'
}
$archive = Join-Path $downloadRoot 'package.zip'
$output = [IO.File]::Open($archive, [IO.FileMode]::CreateNew)
try {
    for ($index=0; $index -lt 3; $index++) {
        $part = $manifest.parts[$index]
        $name = 'MeowKeeperPlus-r98-windows-x64.zip.part{0:D2}' -f ($index+1)
        if ($part.name -ne $name -or $part.size -gt 1073741824 -or $part.size -le 0) { throw '分卷資訊不合法。' }
        $file = Join-Path $downloadRoot $name
        Write-Output ('正在下載分卷 {0}/3…' -f ($index+1))
        Invoke-WebRequest -Uri "$base/$name" -OutFile $file -UseBasicParsing -TimeoutSec 3600
        if ((Get-Item -LiteralPath $file).Length -ne $part.size -or
            (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $part.sha256.ToUpperInvariant()) { throw '分卷校驗失敗。' }
        $inputStream = [IO.File]::OpenRead($file)
        try { $inputStream.CopyTo($output) } finally { $inputStream.Dispose() }
    }
} finally { $output.Dispose() }
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw '完整包校驗失敗。' }
Expand-Archive -LiteralPath $archive -DestinationPath $Destination
Write-Output ('已驗證並解壓至：' + $Destination)
Write-Output '可攜使用：MeowKeeperPlus.cmd。安裝／升級：Install-MeowKeeperPlus.cmd -Upgrade（由你自行核准）。'
Write-Output ('下載暫存保留於：' + $downloadRoot)
