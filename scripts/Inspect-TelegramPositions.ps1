param()
$ErrorActionPreference = 'Stop'
$roots = @(
    'C:\Windows\System32\config\systemprofile\AppData\Local\MeowKeeperPlus\state',
    (Join-Path $env:LOCALAPPDATA 'MeowKeeperPlus\state'),
    'C:\ProgramData\MeowKeeperPlus\Data\state',
    'C:\ProgramData\MeowKeeperPlus\App\data\state',
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'data\state')
)
$items = foreach ($root in $roots) {
    $entry = [ordered]@{location=$root;accessible=$false;preset_exists=$false;camera_count=0;slots=@();error_type=$null}
    try {
        $null = Get-Item -LiteralPath $root -ErrorAction Stop
        $entry.accessible = $true
        $file = Join-Path $root 'telegram-position-presets.json'
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            $entry.preset_exists = $true
            $data = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
            $entry.camera_count = @($data.cameras.PSObject.Properties).Count
            $entry.slots = @($data.cameras.PSObject.Properties | ForEach-Object {
                $camera = $_.Value
                [ordered]@{slot1_present=($null -ne $camera.slots.'1');slot2_present=($null -ne $camera.slots.'2')}
            })
        }
    } catch {
        $entry.error_type = $_.Exception.GetType().Name
    }
    [pscustomobject]$entry
}
[ordered]@{mode='ReadOnly';credential_values_read=$false;locations=@($items)} | ConvertTo-Json -Depth 5
