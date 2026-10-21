@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Xiaomi-Firewall-Wizard.ps1"
if errorlevel 1 (
  echo Xiaomi firewall check did not complete. See the result above.
)
pause
