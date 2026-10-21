param(
    [switch]$AllowRelativeWithoutPosition,
    [string]$ReportPath = ''
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.venv\Scripts\python.exe'
$trial = Join-Path $root 'tools\trial_xiaomi_miss_ptz_026c02.py'
$report = if ($ReportPath) { [IO.Path]::GetFullPath($ReportPath) } else {
    Join-Path $root ('reports\ptz-026c02\trial-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
}
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $trial)) {
    throw 'The project Python runtime or trial script is missing.'
}
$trialArgs = @($trial, '--confirm-interruption', '--report', $report)
if ($AllowRelativeWithoutPosition) { $trialArgs += '--allow-relative-without-position' }
& $python @trialArgs
$code = $LASTEXITCODE
if (Test-Path -LiteralPath $report) {
    Get-Content -LiteralPath $report -Raw -Encoding UTF8 | Write-Host
}
exit $code
