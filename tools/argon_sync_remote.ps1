# Executed only by sync_to_argon.py over the already authorized SSH connection.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$root='C:\Users\user\Desktop\MeowKeeperPlus-Workspace'
$desktop='C:\Users\user\Desktop'
$manifestPath=Join-Path $root '.argon-sync-manifest.json'
function SafePath([string]$name){
    if(!$name -or $name -match '[\\:<>"|?*\x00-\x1f]' -or $name.StartsWith('/')){throw 'unsafe_path'}
    foreach($part in $name.Split('/')){
        if($part -in @('','.','..') -or $part -match '[ .]$' -or $part -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$'){throw 'unsafe_path'}
        if($part -in @('.git','runtime','vendor','models','data','dist','build','.venv','venv','__pycache__','secrets','node_modules','reports','logs','.ssh','.aws','.codex','Ultralytics')){throw 'excluded_path'}
        if($part -like '*.log' -or $part -eq 'kernel.errors.txt'){throw 'excluded_path'}
        if($name -ne 'configs/update-public.pem' -and ($part -match '(?i)\.(pyc|pyo|zip|pt|pth|onnx|db[^.]*|sqlite[^.]*|dpapi|pem|key|pfx|p12|exe|dll|pyd|bak[^.]*)$' -or $part -like '.env*' -or $part -like '*ed25519*' -or $part -like '*authorized_keys*')){throw 'excluded_path'}
    }
    $target=[IO.Path]::GetFullPath((Join-Path $root $name.Replace('/','\')))
    if(!$target.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'outside_workspace'}
    $cursor=$target
    while($cursor -and $cursor.Length -ge $root.Length){
        if(Test-Path -LiteralPath $cursor){if((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'reparse_path'}}
        $cursor=[IO.Path]::GetDirectoryName($cursor)
    }
    return $target
}
function HashBytes([byte[]]$bytes){$sha=[Security.Cryptography.SHA256]::Create();try{return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}}
function HashFile([string]$path){if(Test-Path -LiteralPath $path -PathType Leaf){return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()};return $null}
function WriteAtomic([string]$target,[byte[]]$bytes){
    $parent=[IO.Path]::GetDirectoryName($target)
    [IO.Directory]::CreateDirectory($parent)|Out-Null
    $temp=Join-Path $parent ('.argon-tmp-'+[guid]::NewGuid().ToString('N'))
    try{[IO.File]::WriteAllBytes($temp,$bytes);if(Test-Path -LiteralPath $target){[IO.File]::Replace($temp,$target,[NullString]::Value)}else{[IO.File]::Move($temp,$target)}}finally{if([IO.File]::Exists($temp)){[IO.File]::Delete($temp)}}
}
try{
    SafePath '.argon-git-meta.json'|Out-Null
    SafePath '.argon-sync-manifest.json'|Out-Null
    if((Get-Item -LiteralPath $desktop -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'desktop_reparse'}
    if(!$request){throw 'request_missing'}
    $old=@{}
    if(Test-Path -LiteralPath $manifestPath){
        $m=[IO.File]::ReadAllText($manifestPath,[Text.Encoding]::UTF8)|ConvertFrom-Json
        if($m.schema_version -ne 1 -or $m.managed_root -ne $root){throw 'manifest_mismatch'}
        foreach($p in $m.files.PSObject.Properties){SafePath $p.Name|Out-Null;$old[$p.Name]=$p.Value}
    }
    if($mode -eq 'inspect'){
        $actual=@{}
        foreach($name in @($request.paths)+@($old.Keys)){$actual[$name]=HashFile (SafePath $name)}
        # Only the non-secret installed build manifest is read for baseline proof.
        $installedPath='C:\ProgramData\MeowKeeperPlus\App\build_manifest.json'
        $installed=@{manifest_sha256=(HashFile $installedPath)}
        @{managed=@($old.Keys);actual=$actual;installed=$installed}|ConvertTo-Json -Depth 8 -Compress
        exit 0
    }
    if($mode -notin @('apply','stage')){throw 'invalid_mode'}
    $decoded=@{}
    foreach($entry in $request.files.PSObject.Properties){SafePath $entry.Name|Out-Null;if($entry.Value.sha256 -notmatch '^[0-9a-f]{64}$'){throw 'invalid_hash'}}
    foreach($entry in $request.changed.PSObject.Properties){
        $target=SafePath $entry.Name
        $bytes=[Convert]::FromBase64String($entry.Value)
        if((HashBytes $bytes) -ne $request.files.($entry.Name).sha256){throw 'payload_hash_mismatch'}
        if((HashFile $target) -ne $request.expected.($entry.Name)){throw 'concurrent_change'}
        $decoded[$entry.Name]=$bytes
    }
    foreach($name in @($request.remove)){
        $target=SafePath $name
        if(!$old.ContainsKey($name) -or (HashFile $target) -ne $request.expected.($name)){throw 'unsafe_delete'}
    }
    foreach($name in @($request.handoffs)){
        if($name -notmatch '^HANDOFF_ARGON_[A-Za-z0-9_.-]+\.md$'){throw 'unsafe_handoff'}
        SafePath $name|Out-Null
        $target=Join-Path $desktop $name
        if(Test-Path -LiteralPath $target){if((Get-Item -LiteralPath $target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'handoff_reparse'}}
    }
    foreach($name in $decoded.Keys){WriteAtomic (SafePath $name) $decoded[$name]}
    foreach($name in @($request.remove)){[IO.File]::Delete((SafePath $name))}
    foreach($entry in $request.files.PSObject.Properties){if((HashFile (SafePath $entry.Name)) -ne $entry.Value.sha256){throw 'final_hash_mismatch'}}
    if($mode -eq 'stage'){
        @{status='staged';verified_files=@($request.files.PSObject.Properties).Count;uploaded_files=$decoded.Count}|ConvertTo-Json -Compress
        exit 0
    }
    foreach($name in @($request.handoffs)){
        $source=SafePath $name;$target=Join-Path $desktop $name
        if((HashFile $target) -ne (HashFile $source)){WriteAtomic $target ([IO.File]::ReadAllBytes($source))}
    }
    $manifest=@{schema_version=1;managed_root=$root;synced_at_utc=$request.synced_at_utc;files=$request.files}
    WriteAtomic $manifestPath ([Text.Encoding]::UTF8.GetBytes(($manifest|ConvertTo-Json -Depth 10)))
    @{status='passed';verified_files=@($request.files.PSObject.Properties).Count;uploaded_files=$decoded.Count;deleted_files=@($request.remove).Count}|ConvertTo-Json -Compress
}catch{
    $code='operation_failed'
    if($_.Exception.Message -match '^[a-z_]+$'){$code=$_.Exception.Message}
    [Console]::Error.WriteLine('argon_sync_remote_failed:'+$code);exit 1
}
