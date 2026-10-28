@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".runtime\runtime.json" (
  echo Please run setup.bat first.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\start_gui.ps1"
