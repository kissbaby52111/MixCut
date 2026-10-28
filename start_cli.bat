@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".runtime\python.cmd" (
  echo Please run setup.bat first.
  pause
  exit /b 1
)
call ".runtime\python.cmd" menu
pause
