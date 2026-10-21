param(
    [string]$Output = 'C:\Users\Aaron\Documents\Codex\MeowKeeperPlus\reports\r11-upgrade-diagnostic.json'
)

$ErrorActionPreference = 'Stop'
$app = [IO.Path]::GetFullPath('C:\ProgramData\MeowKeeperPlus\App')
$prefix = $app.TrimEnd('\') + '\'
$names = @('python.exe', 'pythonw.exe', 'go2rtc.exe', 'ffmpeg.exe', 'ffprobe.exe')
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in $names -and $_.ExecutablePath -and
    $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object {
    $commandHash = $null
    if ($_.CommandLine) {
        $bytes = [Text.Encoding]::UTF8.GetBytes($_.CommandLine)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try { $commandHash = [BitConverter]::ToString($algorithm.ComputeHash($bytes)).Replace('-', '') }
        finally { $algorithm.Dispose() }
    }
    [pscustomobject]@{
        name = $_.Name
        pid = $_.ProcessId
        parent_pid = $_.ParentProcessId
        executable_path = $_.ExecutablePath
        command_sha256 = $commandHash
        created_at = $_.CreationDate
        session_id = $_.SessionId
    }
})
$tasks = @('MeowKeeperPlus Core', 'MeowKeeperPlus Tray') | ForEach-Object {
    $task = Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
    $info = if ($task) { Get-ScheduledTaskInfo -TaskName $_ -ErrorAction SilentlyContinue } else { $null }
    [pscustomobject]@{ name = $_; exists = $null -ne $task; state = if ($task) { [string]$task.State } else { $null }; last_result = if ($info) { $info.LastTaskResult } else { $null }; last_run = if ($info) { $info.LastRunTime } else { $null } }
}
$ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {
    $_.LocalPort -in @(8000, 1984, 8554)
} | ForEach-Object {
    [pscustomobject]@{ port = $_.LocalPort; local_address = $_.LocalAddress; owning_pid = $_.OwningProcess }
})
$payload = [pscustomobject]@{ app_root = $app; processes = $processes; tasks = $tasks; listeners = $ports }
$target = [IO.Path]::GetFullPath($Output)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
$payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $target -Encoding UTF8
