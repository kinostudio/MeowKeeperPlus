# Scoped account recovery; not an installer and never migrates credentials.
param([switch]$CaptureStartup)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$taskName='MeowKeeperPlus Core'
$appRoot='C:\ProgramData\MeowKeeperPlus\App'
$dataRoot='C:\ProgramData\MeowKeeperPlus\Data'
$user='SHRIMP-ASUS\user'
$python=Join-Path $appRoot 'runtime\python\python.exe'
$entry=Join-Path $appRoot 'tools\run_host.py'
$changed=$false
$handles=@()
try{
    if((Get-CimInstance Win32_ComputerSystem).UserName -ne $user){throw 'interactive_user_mismatch'}
    $task=Get-ScheduledTask -TaskName $taskName
    if($task.Principal.UserId -notin @('SYSTEM','S-1-5-18')){throw 'unexpected_original_principal'}
    if(@($task.Actions).Count -ne 1){throw 'unexpected_actions'}
    $action=$task.Actions[0]
    if($action.Execute -ne $python -or $action.WorkingDirectory -ne $appRoot -or
       !$action.Arguments.Contains('"'+$entry+'"') -or
       !$action.Arguments.Contains('"'+$dataRoot+'\events"') -or
       !$action.Arguments.Contains('"'+$dataRoot+'\config.json"')){throw 'unexpected_host_action'}
    $originalArguments=$action.Arguments
    $backup=Join-Path $dataRoot ('repair-backups\host-account-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($backup)|Out-Null
    $xml=Export-ScheduledTask -TaskName $taskName
    $backupXml=Join-Path $backup 'core-original.xml'
    [IO.File]::WriteAllText($backupXml,$xml,[Text.Encoding]::Unicode)
    # Capture only the verified Host and its installed children before stopping.
    $all=@(Get-CimInstance Win32_Process)
    $cores=@($all|Where-Object {$_.ExecutablePath -eq $python -and $_.CommandLine.Contains($entry)})
    if($cores.Count -ne 1){throw 'host_count_mismatch'}
    $owned=@($cores);$ids=@($cores.ProcessId)
    do{
        $next=@($all|Where-Object {$_.ParentProcessId -in $ids -and $_.ProcessId -notin $owned.ProcessId})
        foreach($item in $next){
            if($item.ExecutablePath -eq (Join-Path $env:SystemRoot 'System32\conhost.exe')){continue}
            if(!$item.ExecutablePath.StartsWith($appRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'unverified_child'}
            $owned+=@($item)
        }
        $ids=@($next.ProcessId)
    }while($next.Count)
    foreach($item in $owned){
        try{
            $process=[Diagnostics.Process]::GetProcessById([int]$item.ProcessId)
            $null=$process.Handle
            if([Math]::Abs(($process.StartTime.ToUniversalTime()-$item.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 100){throw 'process_identity_mismatch'}
            $handles+=@($process)
        }catch [ArgumentException]{continue}
    }
    Disable-ScheduledTask -TaskName $taskName|Out-Null
    $changed=$true
    Stop-ScheduledTask -TaskName $taskName
    foreach($process in $handles){
        if(!$process.WaitForExit(1500)){$process.Kill();if(!$process.WaitForExit(5000)){throw 'owned_process_did_not_exit'}}
    }
    $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
    Set-ScheduledTask -TaskName $taskName -Principal $principal -Trigger $trigger|Out-Null
    Enable-ScheduledTask -TaskName $taskName|Out-Null
    $after=Get-ScheduledTask -TaskName $taskName
    if($after.Actions[0].Arguments -ne $originalArguments){throw 'action_changed'}
    if($CaptureStartup){
        $startupError=Join-Path $backup 'startup-stderr.txt'
        $startupOutput=Join-Path $backup 'startup-stdout.txt'
        $encodedArgs=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($originalArguments))
        $runner="`$a=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$encodedArgs'));Start-Process -FilePath '$python' -ArgumentList `$a -WorkingDirectory '$appRoot' -WindowStyle Hidden -Wait -RedirectStandardOutput '$startupOutput' -RedirectStandardError '$startupError'"
        $encodedRunner=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($runner))
        $captureAction=New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument ('-NoProfile -NonInteractive -EncodedCommand '+$encodedRunner) -WorkingDirectory $appRoot
        Set-ScheduledTask -TaskName $taskName -Action $captureAction|Out-Null
    }
    Start-ScheduledTask -TaskName $taskName
    $deadline=[DateTime]::UtcNow.AddSeconds(120)
    $confirmed=$null
    do{
        Start-Sleep -Milliseconds 800
        $running=@(Get-CimInstance Win32_Process|Where-Object {$_.ExecutablePath -eq $python -and $_.CommandLine.Contains($entry)})
        if($running.Count -gt 1){throw 'duplicate_host'}
        if($running.Count -eq 1){
            $owner=Invoke-CimMethod -InputObject $running[0] -MethodName GetOwner
            if(($owner.Domain+'\'+$owner.User) -eq $user){
                try{if((Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/v1/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200){$confirmed=$running[0]}}catch{}
            }
        }
    }while(!$confirmed -and [DateTime]::UtcNow -lt $deadline)
    if(!$confirmed){throw 'new_host_not_ready'}
    # A diagnostic wrapper must never become the long-running production action.
    if($CaptureStartup){throw 'diagnostic_started_successfully'}
    @{status='passed';owner=$user;pid=$confirmed.ProcessId;principal=$after.Principal.UserId;logon_type=[string]$after.Principal.LogonType;actions_preserved=$true;health_http=200;backup=$backup}|ConvertTo-Json -Compress
}catch{
    if($CaptureStartup -and $startupError -and (Test-Path -LiteralPath $startupError)){
        # Diagnostic reporting must never interrupt the rollback below.
        try{
            $raw=[IO.File]::ReadAllText($startupError)
            $frames=@([regex]::Matches($raw,'File "([^"]+)", line (\d+), in ([^\r\n]+)')|ForEach-Object {@{file=[IO.Path]::GetFileName($_.Groups[1].Value);line=$_.Groups[2].Value;function=$_.Groups[3].Value}})
            $types=@([regex]::Matches($raw,'(?m)^([A-Za-z]+(?:Error|Exception)):')|ForEach-Object {$_.Groups[1].Value})
            @{startup_error_types=$types;frames=$frames;raw_log_kept_local=$true}|ConvertTo-Json -Depth 5 -Compress
        }catch{[Console]::Error.WriteLine('啟動診斷暫時無法讀取；仍繼續回復原排程。')}
    }
    if($changed){
        try{
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Register-ScheduledTask -TaskName $taskName -Xml $xml -Force|Out-Null
            Start-ScheduledTask -TaskName $taskName
            [Console]::Error.WriteLine('已回復原 Core 排程；帳號恢復未完成。')
        }catch{[Console]::Error.WriteLine('Core 排程回復未完成；請保留備份並停止其他變更。')}
    }
    $code='operation_failed'
    if($_.Exception.Message -match '^[a-z_]+$'){$code=$_.Exception.Message}
    [Console]::Error.WriteLine('Host帳號恢復未完成：'+$code)
    exit 1
}finally{foreach($process in $handles){$process.Dispose()}}
