param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $packageRoot 'runtime\python\pythonw.exe'
$launcher = Join-Path $packageRoot 'tools\portable_start.py'
$icon = Join-Path $packageRoot 'assets\tray\meowkeeper-earth-ready.ico'
foreach ($required in @($python, $launcher, $icon)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw 'Portable launcher file missing.' }
}
if ($CheckOnly) { Write-Output 'Shortcut prerequisites verified.'; exit 0 }
$shortcutPath = Join-Path $packageRoot 'MeowKeeperPlus.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $python
$shortcut.Arguments = '"' + $launcher + '"'
$shortcut.WorkingDirectory = $packageRoot
$shortcut.IconLocation = $icon + ',0'
$shortcut.Description = 'MeowKeeper+ - Local AI Pet Guardian'
$shortcut.Save()
Write-Output 'Created MeowKeeperPlus.lnk. Regenerate after moving the portable folder.'
