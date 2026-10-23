@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Manage-CpuTemperature.ps1" -Mode Install
if errorlevel 1 pause
