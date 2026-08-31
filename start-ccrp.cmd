@echo off
setlocal

rem One-click launcher for the local SSH reverse tunnel.
rem Usage:
rem   start-ccrp.cmd
rem   start-ccrp.cmd -Config ccrp.h102-15721.json

set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%SCRIPT_DIR%scripts\start-ccrp.ps1" %*

endlocal
