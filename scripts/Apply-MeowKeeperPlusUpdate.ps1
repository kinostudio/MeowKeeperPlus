param(
    [string]$AppRoot = (Join-Path $env:ProgramData 'MeowKeeperPlus\App'),
    [string]$DataRoot = (Join-Path $env:ProgramData 'MeowKeeperPlus\Data'),
    [string]$VerifierRoot = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$installRoot = Join-Path $env:ProgramData 'MeowKeeperPlus'
$expectedApp = [IO.Path]::GetFullPath((Join-Path $installRoot 'App'))
$expectedData = [IO.Path]::GetFullPath((Join-Path $installRoot 'Data'))
$AppRoot = [IO.Path]::GetFullPath($AppRoot)
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$updaterRoot = Join-Path $installRoot 'Updater'
$updatesRoot = Join-Path $DataRoot 'updates'
$requestPath = Join-Path $updatesRoot 'apply-request.json'
$statusPath = Join-Path $updaterRoot 'hotfix-status.json'
$expectedPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$expectedCoreTaskName = 'MeowKeeperPlus Core'
$expectedTrayTaskName = 'MeowKeeperPlus Tray'
$coreTaskName = 'MeowKeeperPlus Core'
$trayTaskName = 'MeowKeeperPlus Tray'
$requestId = $null
$preparedId = $null
$workRoot = $null
$taskRecords = @()
$updateStarted = $false
$oldManifestHash = $null
$installerResultPath = $null
$version = ''
$updateSucceeded = $false

function Invoke-BoundedProcess([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds) {
    $outputPath = Join-Path $workRoot ('process-' + [guid]::NewGuid().ToString('N') + '.txt')
    $errorPath = $outputPath + '.error'
    $quoted = @($Arguments | ForEach-Object {
        if ($_ -match '["\r\n]') { throw 'process_argument_invalid' }
        '"' + $_ + '"'
    })
    $process = Start-Process -FilePath $Executable -ArgumentList ($quoted -join ' ') -PassThru -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath
    try {
        $null = $process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            [void]$process.WaitForExit(10000)
            throw 'update_process_timeout'
        }
        if ($process.ExitCode -ne 0) { throw 'update_process_failed' }
        if (Test-Path -LiteralPath $outputPath -PathType Leaf) { Get-Content -LiteralPath $outputPath -Encoding UTF8 }
    } finally { $process.Dispose() }
}

function Stop-UpdateProcesses {
    foreach ($taskName in @($coreTaskName, $trayTaskName)) {
        & "$env:SystemRoot\System32\schtasks.exe" /End /TN $taskName *> $null
    }
    $prefix = [IO.Path]::GetFullPath($AppRoot).TrimEnd('\') + '\'
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    })
    foreach ($snapshot in $processes) {
        $current = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $snapshot.ProcessId) -ErrorAction Stop
        if (-not $current) { continue }
        if ($current.ExecutablePath -ne $snapshot.ExecutablePath -or $current.CreationDate -ne $snapshot.CreationDate) {
            throw 'rollback_process_identity_changed'
        }
        $handle = $null
        try {
            try { $handle = [Diagnostics.Process]::GetProcessById([int]$snapshot.ProcessId) }
            catch [ArgumentException] { continue }
            if ([Math]::Abs(($handle.StartTime.ToUniversalTime() - $snapshot.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
                throw 'rollback_process_handle_changed'
            }
            $handle.Kill()
            if (-not $handle.WaitForExit(15000)) { throw 'rollback_process_exit_timeout' }
        } finally { if ($handle) { $handle.Dispose() } }
    }
    if (@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    }).Count -gt 0) { throw 'rollback_processes_remain' }
}

function Write-UpdateStatus([string]$Status, [string]$Message, [string]$Version = '') {
    try {
        Assert-PlainPath $updaterRoot
        $payload = [ordered]@{ schema = 1; status = $Status; message_zh_tw = $Message }
        if ($Version -match '^r[1-9]\d*$') { $payload.version = $Version }
        if ($requestId -match '^[a-f0-9]{32}$') { $payload.request_id = $requestId }
        if ($preparedId -match '^prepared-[a-f0-9]{8,32}$') { $payload.prepared_id = $preparedId }
        $temporary = Join-Path $updaterRoot ('.hotfix-status-' + [guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($temporary, ($payload | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $statusPath -Force
    } catch { }
}

function Remove-OwnedRequest {
    try {
        if ($requestId -notmatch '^[a-f0-9]{32}$' -or -not (Test-Path -LiteralPath $requestPath -PathType Leaf)) { return }
        $current = Get-Content -Raw -LiteralPath $requestPath | ConvertFrom-Json
        if ($current.request_id -eq $requestId) { Remove-Item -LiteralPath $requestPath -Force }
    } catch { }
}

function Assert-PlainPath([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { throw 'path_missing' }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse_point' }
}

function Get-TaskRecord([string]$Name, [string]$ExpectedScript, [string]$ExpectedRuntime, [string]$TaskRole) {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    $task = $folder.GetTask($Name)
    if ($null -eq $task) { throw 'task_missing' }
    $definition = $task.Definition
    if ($definition.Actions.Count -ne 1) { throw 'task_action_count' }
    $action = $definition.Actions.Item(1)
    $actualRuntime = [IO.Path]::GetFullPath([string]$action.Path)
    $expectedRuntimeFull = [IO.Path]::GetFullPath($ExpectedRuntime)
    $arguments = [string]$action.Arguments
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($actualRuntime, $expectedRuntimeFull) -or
        $arguments.IndexOf(('"' + [IO.Path]::GetFullPath($ExpectedScript) + '"'), [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'task_target_mismatch'
    }
    $expectedInstallRoot = [IO.Path]::GetFullPath((Join-Path $AppRoot '..'))
    $matchedDataRoot = $null
    if ($TaskRole -eq 'core') {
        $allowedDataRoots = @(
            $expectedData,
            [IO.Path]::GetFullPath((Join-Path $AppRoot 'data'))
        )
        $matchedDataRoot = $null
        foreach ($candidateRoot in $allowedDataRoots) {
            $requiredArguments = @(
                ('--dashboard-config "' + (Join-Path $candidateRoot 'config.json') + '"'),
                ('--database-path "' + (Join-Path $candidateRoot 'meowkeeper.db') + '"'),
                ('--event-directory "' + (Join-Path $candidateRoot 'events') + '"'),
                ('--ring-directory "' + (Join-Path $candidateRoot 'cache\ring') + '"'),
                ('--preview-directory "' + (Join-Path $candidateRoot 'cache\preview') + '"'),
                ('--admin-storage-path "' + (Join-Path $candidateRoot 'web_admin.json') + '"'),
                ('--lock-root "' + $expectedInstallRoot + '"')
            )
            if (@($requiredArguments | Where-Object { $arguments.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -lt 0 }).Count -eq 0) {
                $matchedDataRoot = $candidateRoot
                break
            }
        }
        if (-not $matchedDataRoot) { throw 'core_task_data_path_not_supported' }
    } elseif ($TaskRole -eq 'tray' -and
        $arguments.IndexOf(('--lock-root "' + $expectedInstallRoot + '"'), [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'tray_task_lock_root_mismatch'
    }
    $principal = [string]$definition.Principal.UserId
    if ([string]::IsNullOrWhiteSpace($principal)) { throw 'task_principal_missing' }
    $sid = if ($principal -match '^S-\d-') {
        $principal
    } else {
        (New-Object Security.Principal.NTAccount($principal)).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $security = $task.GetSecurityDescriptor(0)
    $xmlPath = Join-Path $workRoot ($Name.Replace(' ', '-') + '.xml')
    $sddlPath = Join-Path $workRoot ($Name.Replace(' ', '-') + '.sddl')
    [IO.File]::WriteAllText($xmlPath, [string]$task.Xml, [Text.UTF8Encoding]::new($true))
    [IO.File]::WriteAllText($sddlPath, [string]$security, [Text.UTF8Encoding]::new($false))
    [pscustomobject]@{ Name = $Name; Sid = $sid; DataRoot = $matchedDataRoot; WasRunning = ([int]$task.State -eq 4); XmlPath = $xmlPath; SddlPath = $sddlPath }
}

function Restore-TaskRecords {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    foreach ($record in $taskRecords) {
        $xml = [IO.File]::ReadAllText($record.XmlPath, [Text.Encoding]::UTF8)
        $sddl = [IO.File]::ReadAllText($record.SddlPath, [Text.Encoding]::UTF8)
        $restored = $folder.RegisterTask($record.Name, $xml, 6, $null, $null, 0, $sddl)
        if ($null -eq $restored) { throw 'task_restore_failed' }
    }
    foreach ($record in $taskRecords) {
        if ($record.WasRunning -and $record.Name -ne 'MeowKeeperPlus Update Worker') {
            & "$env:SystemRoot\System32\schtasks.exe" /Run /TN $record.Name *> $null
            if ($LASTEXITCODE -ne 0) { throw 'task_restart_failed' }
        }
    }
}

function Get-UpdaterTaskRecord {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    $task = $folder.GetTask('MeowKeeperPlus Update Worker')
    if ($null -eq $task -or $task.Definition.Actions.Count -ne 1) { throw 'updater_task_missing_or_invalid' }
    $principal = [string]$task.Definition.Principal.UserId
    if ($principal -notin @('S-1-5-18', 'SYSTEM', 'NT AUTHORITY\SYSTEM')) { throw 'updater_principal_not_system' }
    $action = $task.Definition.Actions.Item(1)
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath([string]$action.Path), [IO.Path]::GetFullPath($expectedPowerShell))) {
        throw 'updater_executable_mismatch'
    }
    $arguments = [string]$action.Arguments
    $match = [regex]::Match($arguments, '(?i)-File\s+"([^"]+\.ps1)"')
    if (-not $match.Success) { throw 'updater_script_missing' }
    $scriptPath = [IO.Path]::GetFullPath($match.Groups[1].Value)
    $scriptRoot = [IO.Path]::GetFullPath($updaterRoot)
    $nameMatch = [regex]::Match([IO.Path]::GetFileName($scriptPath), '^Apply-MeowKeeperPlusUpdate-([a-f0-9]{16})\.ps1$')
    $expectedArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $scriptPath + '" -AppRoot "' + $expectedApp + '" -DataRoot "' + $expectedData + '"'
    if ([IO.Path]::GetDirectoryName($scriptPath) -ne $scriptRoot -or -not $nameMatch.Success -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($arguments.Trim(), $expectedArguments) -or
        -not (Test-Path -LiteralPath $scriptPath -PathType Leaf) -or
        ((Get-Item -LiteralPath $scriptPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $scriptPath).Hash.Substring(0, 16).ToLowerInvariant() -ne $nameMatch.Groups[1].Value -or
        $task.Enabled -ne $true -or $task.Definition.Triggers.Count -ne 0) {
        throw 'updater_task_target_invalid'
    }
    $xmlPath = Join-Path $workRoot 'MeowKeeperPlus-Update-Worker.xml'
    $sddlPath = Join-Path $workRoot 'MeowKeeperPlus-Update-Worker.sddl'
    [IO.File]::WriteAllText($xmlPath, [string]$task.Xml, [Text.UTF8Encoding]::new($true))
    [IO.File]::WriteAllText($sddlPath, [string]$task.GetSecurityDescriptor(0), [Text.UTF8Encoding]::new($false))
    [pscustomobject]@{ Name = 'MeowKeeperPlus Update Worker'; Sid = 'S-1-5-18'; WasRunning = $true; XmlPath = $xmlPath; SddlPath = $sddlPath }
}

function Test-ExpectedTaskDefinition(
    [string]$Name,
    [string]$ExpectedScript,
    [string]$ExpectedRuntime,
    [string[]]$RequiredArguments,
    [string]$ExpectedPrincipalSid
) {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $task = $service.GetFolder('\').GetTask($Name)
    if ($null -eq $task -or $task.Definition.Actions.Count -ne 1) { return $false }
    $principal = [string]$task.Definition.Principal.UserId
    $principalSid = if ($principal -match '^S-\d+(?:-\d+)+$') { $principal } else {
        try { (New-Object Security.Principal.NTAccount($principal)).Translate([Security.Principal.SecurityIdentifier]).Value }
        catch { '' }
    }
    if ($principalSid -ne $ExpectedPrincipalSid) { return $false }
    $action = $task.Definition.Actions.Item(1)
    $arguments = [string]$action.Arguments
    if ($Name -eq 'MeowKeeperPlus Update Worker') {
        if ($task.Enabled -ne $true -or $task.Definition.Triggers.Count -ne 0) { return $false }
        $expectedArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + [IO.Path]::GetFullPath($ExpectedScript) + '" -AppRoot "' + $AppRoot + '" -DataRoot "' + $DataRoot + '"'
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals($arguments.Trim(), $expectedArguments)) { return $false }
    }
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath([string]$action.Path), [IO.Path]::GetFullPath($ExpectedRuntime)) -or
        $arguments.IndexOf(('"' + [IO.Path]::GetFullPath($ExpectedScript) + '"'), [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    foreach ($required in $RequiredArguments) {
        if ($arguments.IndexOf($required, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    }
    return $true
}

function Test-TreeWithoutReparsePoints([string]$Root) {
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($Root)
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($item in @(Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
    return $true
}

function Restore-PreviousApp([string]$ResultPath, [string]$ExpectedOldHash) {
    if (-not (Test-Path -LiteralPath $ResultPath -PathType Leaf)) { return $false }
    $payload = Get-Content -Raw -LiteralPath $ResultPath | ConvertFrom-Json
    if ($payload.schema -ne 1 -or $payload.status -notin @('switch_started', 'installed_health_passed') -or
        $payload.old_manifest_sha256 -notmatch '^[a-f0-9]{64}$' -or
        $payload.old_manifest_sha256 -ne $ExpectedOldHash.ToLowerInvariant()) { throw 'installer_result_contract_invalid' }
    $backup = [IO.Path]::GetFullPath([string]$payload.old_backup_app)
    $installParent = [IO.Path]::GetFullPath($installRoot)
    if ([IO.Path]::GetDirectoryName($backup) -ne $installParent -or
        [IO.Path]::GetFileName($backup) -notmatch '^App\.upgrade-backup-[a-f0-9]{32}$') { throw 'old_app_backup_path_invalid' }
    if (-not (Test-Path -LiteralPath $backup)) {
        $existingManifest = Join-Path $AppRoot 'build_manifest.json'
        if ((Test-Path -LiteralPath $existingManifest -PathType Leaf) -and
            (Get-FileHash -Algorithm SHA256 -LiteralPath $existingManifest).Hash.ToLowerInvariant() -eq $ExpectedOldHash.ToLowerInvariant()) {
            return $false
        }
        throw 'old_app_backup_missing'
    }
    Assert-PlainPath $backup
    $backupManifest = Join-Path $backup 'build_manifest.json'
    if (-not (Test-Path -LiteralPath $backupManifest -PathType Leaf) -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $backupManifest).Hash.ToLowerInvariant() -ne $ExpectedOldHash.ToLowerInvariant()) {
        throw 'old_app_backup_hash_invalid'
    }
    Stop-UpdateProcesses
    $currentApp = [IO.Path]::GetFullPath($AppRoot)
    $failedApp = Join-Path $installRoot ('App.failed-hotfix-' + [guid]::NewGuid().ToString('N'))
    if (Test-Path -LiteralPath $currentApp) {
        Assert-PlainPath $currentApp
        Move-Item -LiteralPath $currentApp -Destination $failedApp -ErrorAction Stop
    }
    Move-Item -LiteralPath $backup -Destination $currentApp -ErrorAction Stop
    if ($payload.migration_journal -and (Test-Path -LiteralPath ([string]$payload.migration_journal) -PathType Leaf)) {
        $journalPath = [IO.Path]::GetFullPath([string]$payload.migration_journal)
        $expectedJournalParent = [IO.Path]::GetFullPath([string]$payload.installer_temp_root)
        if ([IO.Path]::GetDirectoryName($journalPath) -ne $expectedJournalParent -or
            [IO.Path]::GetFileName($journalPath) -ne 'legacy-data-migration.json' -or
            -not (Test-TreeWithoutReparsePoints $expectedJournalParent)) { throw 'migration_journal_path_invalid' }
        $legacyPython = Join-Path $currentApp 'runtime\python\python.exe'
        $migrationTool = Join-Path $currentApp 'tools\migrate_legacy_app_data.py'
        & $legacyPython $migrationTool --mode rollback --journal $journalPath *> $null
        if ($LASTEXITCODE -ne 0) { throw 'legacy_data_rollback_failed' }
    }
    $restoredHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $currentApp 'build_manifest.json')).Hash.ToLowerInvariant()
    if ($restoredHash -ne $ExpectedOldHash.ToLowerInvariant()) { throw 'old_app_restore_hash_invalid' }
    return $true
}

function Wait-ForDashboard([int]$TimeoutSeconds = 60, [string]$ConfigDataRoot = $DataRoot) {
    $port = 8000
    $configPath = Join-Path $ConfigDataRoot 'config.json'
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        try {
            $candidate = [int]((Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).dashboard.port)
            if ($candidate -ge 1 -and $candidate -le 65535) { $port = $candidate }
        } catch { }
    }
    $deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([datetime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/healthz" -f $port) -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 750 }
    }
    return $false
}

try {
    $isSystem = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -eq 'S-1-5-18'
    $isAdministrator = ([Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isSystem -and -not $isAdministrator) { throw 'worker_requires_system' }
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($AppRoot, $expectedApp) -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($DataRoot, $expectedData)) { throw 'install_roots_mismatch' }
    Assert-PlainPath $updaterRoot
    if ($VerifierRoot) {
        $VerifierRoot = [IO.Path]::GetFullPath($VerifierRoot)
        if (-not $isAdministrator -or [IO.Path]::GetDirectoryName($VerifierRoot) -ne [IO.Path]::GetFullPath($updaterRoot) -or
            [IO.Path]::GetFileName($VerifierRoot) -notmatch '^bootstrap-[a-f0-9]{32}$') { throw 'bootstrap_verifier_root_invalid' }
        Assert-PlainPath $VerifierRoot
        if (-not (Test-TreeWithoutReparsePoints $VerifierRoot)) { throw 'bootstrap_verifier_reparse_point' }
    } else { $VerifierRoot = $AppRoot }
    Set-Location -LiteralPath $updaterRoot
    Assert-PlainPath $AppRoot
    Assert-PlainPath $DataRoot
    Assert-PlainPath $updatesRoot
    Assert-PlainPath $requestPath
    $request = Get-Content -Raw -LiteralPath $requestPath | ConvertFrom-Json
    if ($request.schema -ne 1 -or $request.request_id -notmatch '^[a-f0-9]{32}$' -or
        $request.prepared_id -notmatch '^prepared-[a-f0-9]{8,32}$' -or $request.version -notmatch '^r[1-9]\d*$') {
        throw 'request_contract_invalid'
    }
    $requestId = [string]$request.request_id
    $preparedId = [string]$request.prepared_id
    $version = [string]$request.version
    $sourceRoot = Join-Path $updatesRoot $preparedId
    Assert-PlainPath $sourceRoot
    $sourceArchive = Join-Path $sourceRoot 'package.zip'
    $sourceEnvelope = Join-Path $sourceRoot 'update-manifest.json'
    Assert-PlainPath $sourceArchive
    Assert-PlainPath $sourceEnvelope
    $archiveLength = [long](Get-Item -LiteralPath $sourceArchive -Force).Length
    $envelopeLength = [long](Get-Item -LiteralPath $sourceEnvelope -Force).Length
    if ($archiveLength -le 0 -or $archiveLength -gt 6GB -or $envelopeLength -le 0 -or $envelopeLength -gt 65536) {
        throw 'prepared_package_size_invalid'
    }

    $workRoot = Join-Path $updaterRoot ('work-' + $requestId)
    if (Test-Path -LiteralPath $workRoot) { throw 'work_directory_exists' }
    New-Item -ItemType Directory -Path $workRoot | Out-Null
    & "$env:SystemRoot\System32\icacls.exe" $workRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' *> $null
    if ($LASTEXITCODE -ne 0) { throw 'work_acl_failed' }
    Write-UpdateStatus 'applying' '正在將已下載套件複製到受保護暫存區。' $version
    Copy-Item -LiteralPath $sourceArchive -Destination (Join-Path $workRoot 'package.zip')
    Copy-Item -LiteralPath $sourceEnvelope -Destination (Join-Path $workRoot 'update-manifest.json')
    if ((Get-Item -LiteralPath (Join-Path $workRoot 'package.zip')).Length -ne $archiveLength -or
        (Get-Item -LiteralPath (Join-Path $workRoot 'update-manifest.json')).Length -ne $envelopeLength) {
        throw 'protected_copy_size_mismatch'
    }
    Write-UpdateStatus 'applying' '正在受保護的暫存區重新驗證官方簽章與更新檔案。' $version

    $taskRecords = @(
        (Get-TaskRecord $coreTaskName (Join-Path $AppRoot 'tools\run_host.py') (Join-Path $AppRoot 'runtime\python\python.exe') 'core'),
        (Get-TaskRecord $trayTaskName (Join-Path $AppRoot 'tools\run_tray.py') (Join-Path $AppRoot 'runtime\python\pythonw.exe') 'tray'),
        (Get-UpdaterTaskRecord)
    )
    $coreSid = $taskRecords[0].Sid
    $traySid = $taskRecords[1].Sid
    $systemSid = 'S-1-5-18'
    if ($coreSid -ne $systemSid -and $coreSid -notmatch '^S-1-5-21-(\d+-){3}\d+$') { throw 'core_principal_unsupported' }
    if ($traySid -ne $systemSid -and $traySid -notmatch '^S-1-5-21-(\d+-){3}\d+$') { throw 'tray_principal_unsupported' }
    $oldManifestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $AppRoot 'build_manifest.json')).Hash

    $python = Join-Path $AppRoot 'runtime\python\python.exe'
    $verifyTool = Join-Path $VerifierRoot 'tools\apply_verified_update.py'
    $stageArgs = @($verifyTool, '--archive', (Join-Path $workRoot 'package.zip'), '--envelope', (Join-Path $workRoot 'update-manifest.json'), '--app-root', $AppRoot, '--stage-root', $workRoot)
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONPATH = Join-Path $AppRoot 'src'
    $verificationOutput = Invoke-BoundedProcess $python $stageArgs 900
    $staged = ($verificationOutput | Select-Object -Last 1) | ConvertFrom-Json
    if ($staged.status -ne 'staged' -or $staged.version -ne $version) { throw 'staging_contract_invalid' }
    $packageRoot = [IO.Path]::GetFullPath([string]$staged.package_path)
    if (-not $packageRoot.StartsWith([IO.Path]::GetFullPath($workRoot) + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'staging_path_invalid' }
    $expectedNewManifestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $packageRoot 'build_manifest.json')).Hash.ToLowerInvariant()
    $installerResultPath = Join-Path $workRoot 'installer-result.json'

    Write-UpdateStatus 'applying' '套件已再次驗證；正在短暫停止監控並切換程式版本。' $version
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $installer = Join-Path $packageRoot 'scripts\Install-MeowKeeperPlus.ps1'
    $installerArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-Upgrade', '-HotfixManaged', '-TrayUserSid', $traySid, '-HotfixResultPath', $installerResultPath, '-HotfixOldManifestSha256', $oldManifestHash)
    if ($coreSid -ne $systemSid) { $installerArgs += @('-UserHost', '-HostUserSid', $coreSid) }
    $updateStarted = $true
    Invoke-BoundedProcess $powershell $installerArgs 1800 | Out-Null
    if (-not (Test-Path -LiteralPath $installerResultPath -PathType Leaf)) { throw 'installer_result_missing' }
    $installerResult = Get-Content -Raw -LiteralPath $installerResultPath | ConvertFrom-Json
    if ($installerResult.schema -ne 1 -or $installerResult.status -ne 'installed_health_passed' -or
        $installerResult.old_manifest_sha256 -ne $oldManifestHash.ToLowerInvariant()) { throw 'installer_result_invalid' }
    $installedManifest = Join-Path $AppRoot 'build_manifest.json'
    if (-not (Test-Path -LiteralPath $installedManifest -PathType Leaf) -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $installedManifest).Hash.ToLowerInvariant() -ne $expectedNewManifestHash) {
        throw 'installed_manifest_does_not_match_signed_package'
    }
    if (-not (Test-ExpectedTaskDefinition -Name $coreTaskName -ExpectedScript (Join-Path $AppRoot 'tools\run_host.py') -ExpectedRuntime (Join-Path $AppRoot 'runtime\python\python.exe') -RequiredArguments @('--dashboard-config "' + $DataRoot + '\config.json"', '--database-path "' + $DataRoot + '\meowkeeper.db"', '--event-directory "' + $DataRoot + '\events"') -ExpectedPrincipalSid $coreSid) -or
        -not (Test-ExpectedTaskDefinition -Name $trayTaskName -ExpectedScript (Join-Path $AppRoot 'tools\run_tray.py') -ExpectedRuntime (Join-Path $AppRoot 'runtime\python\pythonw.exe') -RequiredArguments @('--lock-root "' + $installRoot + '"') -ExpectedPrincipalSid $traySid)) {
        throw 'installed_core_or_tray_task_invalid'
    }
    $newWorkerSource = Join-Path $AppRoot 'scripts\Apply-MeowKeeperPlusUpdate.ps1'
    $newWorkerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $newWorkerSource).Hash.ToLowerInvariant()
    $newWorkerScript = Join-Path $updaterRoot ('Apply-MeowKeeperPlusUpdate-' + $newWorkerHash.Substring(0, 16) + '.ps1')
    if (-not (Test-ExpectedTaskDefinition -Name 'MeowKeeperPlus Update Worker' -ExpectedScript $newWorkerScript -ExpectedRuntime $expectedPowerShell -RequiredArguments @('-AppRoot "' + $AppRoot + '"', '-DataRoot "' + $DataRoot + '"') -ExpectedPrincipalSid $systemSid)) {
        throw 'installed_update_worker_task_invalid'
    }
    if (-not (Test-TreeWithoutReparsePoints $updaterRoot)) { throw 'installed_updater_tree_contains_reparse_point' }
    if (-not (Wait-ForDashboard 90)) { throw 'new_dashboard_health_failed' }
    $backupApp = [IO.Path]::GetFullPath([string]$installerResult.old_backup_app)
    if ([IO.Path]::GetDirectoryName($backupApp) -ne [IO.Path]::GetFullPath($installRoot) -or
        [IO.Path]::GetFileName($backupApp) -notmatch '^App\.upgrade-backup-[a-f0-9]{32}$') { throw 'old_app_backup_contract_invalid' }
    Assert-PlainPath $backupApp
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backupApp 'build_manifest.json')).Hash.ToLowerInvariant() -ne $oldManifestHash.ToLowerInvariant()) {
        throw 'old_app_backup_changed'
    }
    $residualData = Join-Path $backupApp 'data'
    $dataItems = if (Test-Path -LiteralPath $residualData -PathType Container) { @(Get-ChildItem -LiteralPath $residualData -Force) } else { @() }
    $hasUnmigratedData = @($dataItems | Where-Object { $_.Name -notin @('cache', 'locks', 'install-history') }).Count -gt 0
    $backupRetained = $hasUnmigratedData -or -not (Test-TreeWithoutReparsePoints $backupApp)
    if (-not $backupRetained) {
        try { Remove-Item -LiteralPath $backupApp -Recurse -Force -ErrorAction Stop }
        catch { $backupRetained = $true }
    }
    $tempRoot = [IO.Path]::GetFullPath([string]$installerResult.installer_temp_root)
    $tempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($tempRoot) -eq $tempParent -and
        [IO.Path]::GetFileName($tempRoot) -match '^MeowKeeperPlus-install-[a-f0-9]{32}$' -and
        (Test-Path -LiteralPath $tempRoot -PathType Container) -and
        -not ((Get-Item -LiteralPath $tempRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    $successMessage = if ($backupRetained) {
        '更新完成，Dashboard 與監控核心已通過健康檢查；為保留未遷移舊資料，舊版備份仍保留。'
    } else {
        '更新完成，Dashboard 與監控核心已通過健康檢查；舊版備份已安全清理。'
    }
    Write-UpdateStatus 'applied' $successMessage $version
    $updateSucceeded = $true
    Remove-OwnedRequest
}
catch {
    $rollbackOk = -not $updateStarted
    try {
        if ($updateStarted -and $taskRecords.Count -gt 0) {
            Stop-UpdateProcesses
            if ($installerResultPath -and (Test-Path -LiteralPath $installerResultPath -PathType Leaf)) {
                [void](Restore-PreviousApp $installerResultPath $oldManifestHash)
            }
            $dataBackup = Join-Path $workRoot 'data-backup'
            if (Test-Path -LiteralPath (Join-Path $workRoot 'data-snapshot.json') -PathType Leaf) {
                $restoreTool = Join-Path $packageRoot 'tools\hotfix_data_snapshot.py'
                Invoke-BoundedProcess (Join-Path $AppRoot 'runtime\python\python.exe') @($restoreTool, 'restore', '--data', $DataRoot, '--backup', $dataBackup) 900 | Out-Null
            }
            Restore-TaskRecords
            $currentManifest = Join-Path $AppRoot 'build_manifest.json'
            if ([string]::IsNullOrWhiteSpace($oldManifestHash) -or -not (Test-Path -LiteralPath $currentManifest) -or
                (Get-FileHash -Algorithm SHA256 -LiteralPath $currentManifest).Hash.ToLowerInvariant() -ne $oldManifestHash.ToLowerInvariant()) {
                throw 'old_app_manifest_not_restored'
            }
            $restoreDataRoot = if ($taskRecords.Count -gt 0 -and $taskRecords[0].DataRoot) { $taskRecords[0].DataRoot } else { $DataRoot }
            if (-not (Wait-ForDashboard 75 $restoreDataRoot)) { throw 'old_dashboard_health_failed' }
            $rollbackOk = $true
        }
    } catch { $rollbackOk = $false }
    if ($rollbackOk -and -not $updateStarted) {
        Write-UpdateStatus 'failed' '套件或更新前置驗證未通過；尚未切換版本，原有程式與資料維持不變。' $version
    } elseif ($rollbackOk) {
        Write-UpdateStatus 'failed' '更新未完成；已恢復原版本、資料及排程，並確認 Dashboard 重新啟動。失敗現場與快照已保留。' $version
    } else {
        Write-UpdateStatus 'rollback_failed' '更新或自動復原未能完成。請保留程式與資料，使用開始功能表啟動入口檢查狀態；不要再次套用。' $version
    }
    Remove-OwnedRequest
}
finally {
    if ($updateSucceeded -and $workRoot -and (Test-Path -LiteralPath $workRoot)) {
        try {
            $resolvedWork = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $workRoot).Path)
            if ($resolvedWork.StartsWith([IO.Path]::GetFullPath($updaterRoot) + '\', [StringComparison]::OrdinalIgnoreCase) -and
                [IO.Path]::GetFileName($resolvedWork) -match '^work-[a-f0-9]{32}$' -and
                (Test-TreeWithoutReparsePoints $resolvedWork)) {
                Remove-Item -LiteralPath $resolvedWork -Recurse -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
}
