param(
    [string]$PackageRoot = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\dist\MeowKeeperPlus-portable-2026-09-24-r13',
    [string]$TranscriptPath = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\reports\r13-upgrade-transcript.txt'
)

$ErrorActionPreference = 'Stop'
$package = [IO.Path]::GetFullPath($PackageRoot)
$installer = Join-Path $package 'scripts\Install-MeowKeeperPlus.ps1'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Verified r13 installer is missing.' }
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($TranscriptPath))) | Out-Null
$exitCode = 0
Start-Transcript -LiteralPath $TranscriptPath -Force | Out-Null
try {
    & $installer -Upgrade
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { $exitCode = [int]$LASTEXITCODE }
}
catch {
    Write-Output ('Upgrade error type: {0}' -f $_.Exception.GetType().Name)
    Write-Output ('Upgrade error message: {0}' -f $_.Exception.Message)
    $exitCode = 1
}
finally { Stop-Transcript | Out-Null }
exit $exitCode
