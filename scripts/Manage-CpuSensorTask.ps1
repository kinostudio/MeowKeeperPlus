param([ValidateSet('Install','Remove','Diagnose')][string]$Mode = 'Diagnose')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Never auto-load an elevated module from a caller's user-writable PSModulePath.
$taskModule = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\Modules\ScheduledTasks\ScheduledTasks.psd1'
Import-Module -Name $taskModule -ErrorAction Stop
$taskName = 'MeowKeeperPlus CPU Sensor'
$marker = 'MeowKeeperPlus CPU Sensor v1; fixed sensor-only executable'
$binaryRoot = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'MeowKeeperPlusCpuSensor'
$stateRoot = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MeowKeeperPlusCpuSensor'
$executable = Join-Path $binaryRoot 'MeowKeeperCpuSensor.exe'
$adminSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$usersSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
function Assert-PlainPath([string]$Path) {
    $item = [IO.Path]::GetFullPath($Path)
    while ($item) {
        if (Test-Path -LiteralPath $item) {
            if ((Get-Item -Force -LiteralPath $item).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '測溫路徑包含連結；未建立特權工作。' }
        }
        $item = [IO.Path]::GetDirectoryName($item)
    }
}
function Set-ProtectedDirectory([string]$Path) {
    Assert-PlainPath $Path
    if (Test-Path -LiteralPath $Path) {
        $acl = Get-Acl -LiteralPath $Path
        $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -notin @($adminSid.Value, $systemSid.Value)) { throw '既有測溫目錄不是管理員擁有；未覆寫。' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            $writes = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
            if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @($adminSid.Value,$systemSid.Value) -and ($rule.FileSystemRights -band $writes)) { throw '既有測溫目錄可由非管理員修改；未執行。' }
        }
    } else { New-Item -ItemType Directory -Path $Path | Out-Null }
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($adminSid)
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($sid in @($adminSid,$systemSid)) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($usersSid,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
$task = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
if ($null -ne $task -and ($task.Description -ne $marker -or @($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $executable -or $task.Actions[0].Arguments -ne '--broker' -or $task.Principal.UserId -notin @('SYSTEM','S-1-5-18'))) { throw '同名工作不屬於喵喵守護者+ 測溫元件；未修改。' }
if ($Mode -eq 'Diagnose') {
    [pscustomobject]@{ status = if ($null -ne $task) {'installed'} else {'absent'}; task = $taskName; network_listener = $false; temperature_verified = $false } | ConvertTo-Json
    exit 0
}
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '測溫工作需要正式管理員授權；沒有自行提權。' }
if ($Mode -eq 'Remove') {
    if ($null -ne $task) {
        Stop-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false
    }
    # Retain protected files for safe diagnosis/reinstallation; no broad deletion.
    Write-Host '已移除本程式的測溫工作；保留受保護元件，不修改 PawnIO 或其他軟體。'
    exit 0
}
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\vendor\cpu-sensor'))
Assert-PlainPath $source
$manifest = Get-Content -LiteralPath (Join-Path $source 'manifest.json') -Raw | ConvertFrom-Json
$files = @($manifest.files.PSObject.Properties)
if ($manifest.schema_version -ne 1 -or $files.Count -lt 2 -or $files.Count -gt 64) { throw '測溫元件清單不正確。' }
foreach ($file in $files) {
    if ($file.Name -notmatch '^(MeowKeeperCpuSensor\.exe|[A-Za-z0-9_.-]+\.dll)$' -or $file.Value -notmatch '^[a-f0-9]{64}$') { throw '測溫元件名稱或校驗不正確。' }
    $path = Join-Path $source $file.Name
    Assert-PlainPath $path
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw '測溫元件雜湊不符；未執行。' }
}
if ('MeowKeeperCpuSensor.exe' -notin @($files.Name) -or 'LibreHardwareMonitorLib.dll' -notin @($files.Name)) { throw '缺少受限測溫元件。' }
Set-ProtectedDirectory $binaryRoot
Set-ProtectedDirectory $stateRoot
if ($null -ne $task) {
    Stop-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Get-ScheduledTask -TaskName $taskName -TaskPath '\').State -eq 'Running') {
        if ([DateTime]::UtcNow -gt $deadline) { throw '舊測溫工作尚未停止；未覆寫執行檔。' }
        Start-Sleep -Milliseconds 100
    }
}
foreach ($file in $files) {
    $destination = Join-Path $binaryRoot $file.Name
    Assert-PlainPath $destination
    Copy-Item -LiteralPath (Join-Path $source $file.Name) -Destination $destination -Force
    $fileAcl = New-Object Security.AccessControl.FileSecurity
    $fileAcl.SetOwner($adminSid)
    $fileAcl.SetAccessRuleProtection($false,$false)
    Set-Acl -LiteralPath $destination -AclObject $fileAcl
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.Value) { throw '受保護元件複製校驗失敗；未啟動。' }
}
$request = Join-Path $stateRoot 'request.txt'
Assert-PlainPath $request
[IO.File]::WriteAllText($request,'')
$requestAcl = New-Object Security.AccessControl.FileSecurity
$requestAcl.SetOwner($adminSid)
$requestAcl.SetAccessRuleProtection($true,$false)
foreach ($sid in @($adminSid,$systemSid)) { $requestAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow'))) }
# Users can update this timestamp only, not delete/replace files or change ACLs.
$requestAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($usersSid,'Read,Write','Allow')))
Set-Acl -LiteralPath $request -AclObject $requestAcl
$action = New-ScheduledTaskAction -Execute $executable -Argument '--broker' -WorkingDirectory $binaryRoot
$triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5) -RepetitionInterval (New-TimeSpan -Minutes 1)))
$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description $marker -Force | Out-Null
Write-Host '受限測溫工作已建立：只讀 CPU 感測器；Host 帳號未變更。啟動監控後最長約一分鐘取得新讀值；仍須實機驗收。'
