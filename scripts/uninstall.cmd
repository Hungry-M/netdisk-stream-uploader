@echo off
rem StreamPack uninstaller (double-click entry).
rem   uninstall.cmd            preview only
rem   uninstall.cmd -Yes       actually uninstall
rem   uninstall.cmd -Yes -PurgeData   also remove user data
rem
rem ASCII only on purpose: cmd.exe reads .cmd in the OEM codepage and does not
rem honour a UTF-8 BOM, so non-ASCII comments break the script.
setlocal
rem Switch to TEMP first: if this process keeps the install directory as its
rem working directory, the directory cannot be deleted while we are running.
cd /d "%TEMP%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
rem Without arguments (interactive double-click) keep the window open.
if "%~1"=="" pause
