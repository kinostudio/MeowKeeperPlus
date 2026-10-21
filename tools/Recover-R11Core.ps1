param(
    [string]$ResultPath = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\reports\r11-recovery.json'
)

$ErrorActionPreference = 'Stop'
$app = 'C:\ProgramData\MeowKeeperPlus\App'
$expected = Join-Path $app 'runtime\python\python.exe'
$hostScript = Join-Path $app 'tools\run_host.py'
$result = [ordered]@{ task_valid = $false; task_started = $false; health_status = 'not_checked'; error = $null }
try {
    if (-not (Test-Path -LiteralPath $expected -PathType Leaf) -or -not (Test-Path -LiteralPath $hostScript -PathType Leaf)) {
        throw 'Installed Core files are missing.'
    }
    $task = Get-ScheduledTask -TaskName 'MeowKeeperPlus Core' -ErrorAction Stop
    $valid = @($task.Actions | Where-Object {
        [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($_.Execute.Trim('"')), $expected) -and
        $_.Arguments.Contains($hostScript)
    })
    if ($valid.Count -ne 1) { throw 'Core task action does not match installed files.' }
    $result.task_valid = $true
    & "$env:SystemRoot\System32\schtasks.exe" /Run /TN 'MeowKeeperPlus Core' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Core scheduled task failed to start.' }
    $result.task_started = $true
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/healthz' -TimeoutSec 1
            if ($response.StatusCode -eq 200) { $result.health_status = 'passed'; break }
        }
        catch { Start-Sleep -Seconds 1 }
    }
    if ($result.health_status -ne 'passed') { $result.health_status = 'failed' }
}
catch {
    $result.error = $_.Exception.Message
    if ($result.health_status -eq 'not_checked') { $result.health_status = 'failed' }
}
[IO.File]::WriteAllText($ResultPath, ($result | ConvertTo-Json -Depth 3), [Text.UTF8Encoding]::new($false))
if ($result.health_status -ne 'passed') { exit 1 }
