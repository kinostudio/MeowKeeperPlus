param(
    [string]$Output = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\reports\r11-legacy-tray-audit.json'
)

$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus'
$script = Join-Path $repo 'tools\run_tray.py'
$runtime = Join-Path $repo '.venv\Scripts\pythonw.exe'
$tasks = @(Get-ScheduledTask | ForEach-Object {
    $task = $_
    foreach ($action in @($task.Actions)) {
        if ($task.TaskName -match 'MeowKeeper' -or
            ($action.Execute -and $action.Execute.Contains('MeowKeeperPlus')) -or
            ($action.Arguments -and $action.Arguments.Contains('MeowKeeperPlus'))) {
            [pscustomobject]@{
                name = $task.TaskName
                path = $task.TaskPath
                state = [string]$task.State
                executes_repo_venv = [StringComparer]::OrdinalIgnoreCase.Equals($action.Execute.Trim('"'), $runtime)
                runs_repo_tray = [bool]($action.Arguments -and $action.Arguments.Contains($script))
                execute_path = $action.Execute
            }
        }
    }
})
$processes = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object {
    $_.ExecutablePath -and [StringComparer]::OrdinalIgnoreCase.Equals($_.ExecutablePath, $runtime)
} | ForEach-Object {
    [pscustomobject]@{
        pid = $_.ProcessId
        parent_pid = $_.ParentProcessId
        is_repo_tray = [bool]($_.CommandLine -and $_.CommandLine.Contains($script))
    }
})
[pscustomobject]@{ tasks = $tasks; repo_pythonw = $processes } | ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $Output -Encoding UTF8
