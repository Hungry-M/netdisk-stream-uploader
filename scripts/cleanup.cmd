@echo off
rem StreamPack portable cleanup (double-click entry).
rem   cleanup.cmd               preview only
rem   cleanup.cmd -Yes          delete data files
rem   cleanup.cmd -Yes -KeepBackups -RemoveFolder
rem
rem ASCII only on purpose: cmd.exe reads .cmd in the OEM codepage and does not
rem honour a UTF-8 BOM, so non-ASCII comments break the script.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cleanup.ps1" %*
if "%~1"=="" pause
